"""A local, read-only export window with focused controls and optional filters."""
from datetime import datetime
import os
from pathlib import Path
import queue
import re
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .backend import Engine, time_window


class ExportWindow:
    def __init__(self, root, engine=None):
        self.root = root
        self.engine = engine if engine is not None else Engine()
        self.events = queue.Queue()
        self.busy = False
        self.sender_ids = None
        self.settings_dialog = None
        self.last_result = None
        self._controls = []
        self._saved_states = []
        self._closed = False
        root.title("微信聊天导出")
        root.configure(background="#F3F5F4")
        self._style()

        self.chat_name = tk.StringVar(value="")
        self.chat_name.trace_add("write", lambda *_: self._clear_senders())
        self.count = tk.StringVar(value="20")
        self.all_records = tk.BooleanVar(value=False)
        self.format = tk.StringVar(value="TXT")
        self.forwarded_only = tk.BooleanVar(value=False)
        self.forwarded_only.trace_add("write", lambda *_: self._mode_changed())
        self.use_interval = tk.BooleanVar(value=False)
        today = datetime.now().strftime("%Y-%m-%d")
        self.start = tk.StringVar(value=today + " 00:00")
        self.end = tk.StringVar(value=today + " 23:59")
        self.limit_senders = tk.BooleanVar(value=False)
        self.sender_summary = tk.StringVar(value="保留所有人和系统消息")
        self.include_time = tk.BooleanVar(value=True)
        self.include_sender = tk.BooleanVar(value=True)
        self.include_title = tk.BooleanVar(value=True)
        self.settings_summary = tk.StringVar(value="不限时间与发言人 · 保留时间、发言人和标题")
        for variable in (self.use_interval, self.limit_senders, self.include_time,
                         self.include_sender, self.include_title):
            variable.trace_add("write", lambda *_: self._update_settings_summary())
        self.interval_boxes = []
        self.sender_button = None

        panel = ttk.Frame(root, padding=(30, 24, 30, 18), style="Surface.TFrame")
        panel.pack(fill="both", expand=True)
        panel.columnconfigure(0, weight=1)
        ttk.Label(panel, text="微信聊天导出", style="Title.TLabel").grid(row=0, sticky="w")
        ttk.Label(panel, text="把电脑本地保存的聊天，整理成一份文件。", style="Muted.TLabel").grid(
            row=1, sticky="w", pady=(5, 22))
        ttk.Label(panel, text="聊天名称或备注", style="Section.TLabel").grid(row=2, sticky="w")
        self.chat_entry = self._control(ttk.Entry(panel, textvariable=self.chat_name, style="Chat.TEntry"))
        self.chat_entry.grid(row=3, sticky="ew", pady=(8, 7), ipady=5)
        ttk.Label(panel, text="填写完整群名、好友备注或 wxid", style="Muted.TLabel").grid(row=4, sticky="w")

        scope = ttk.Frame(panel, style="Surface.TFrame")
        scope.grid(row=5, sticky="ew", pady=(23, 0))
        ttk.Label(scope, text="导出范围", style="Section.TLabel").pack(side="left", padx=(0, 22))
        self._control(ttk.Radiobutton(scope, text="最近", variable=self.all_records, value=False,
                                     command=self._toggle_all)).pack(side="left")
        self.count_box = self._control(ttk.Spinbox(scope, from_=1, to=2000, width=6, textvariable=self.count))
        self.count_box.pack(side="left", padx=(7, 6), ipady=3)
        ttk.Label(scope, text="条").pack(side="left", padx=(0, 25))
        self._control(ttk.Radiobutton(scope, text="全部本地记录", variable=self.all_records, value=True,
                                     command=self._toggle_all)).pack(side="left")

        mode = ttk.Frame(panel, style="Surface.TFrame")
        mode.grid(row=6, sticky="ew", pady=(22, 0))
        ttk.Label(mode, text="导出内容", style="Section.TLabel").pack(side="left", padx=(0, 22))
        self._control(ttk.Radiobutton(mode, text="正常聊天", variable=self.forwarded_only, value=False)).pack(side="left")
        self._control(ttk.Radiobutton(mode, text="仅合并转发", variable=self.forwarded_only, value=True)).pack(
            side="left", padx=(25, 0))
        self.mode_hint = tk.StringVar()
        ttk.Label(panel, textvariable=self.mode_hint, style="Muted.TLabel", wraplength=620).grid(
            row=7, sticky="w", pady=(8, 0))

        formats = ttk.Frame(panel, style="Surface.TFrame")
        formats.grid(row=8, sticky="ew", pady=(22, 20))
        ttk.Label(formats, text="文件格式", style="Section.TLabel").pack(side="left", padx=(0, 22))
        for label, value in (("TXT  纯文本", "TXT"), ("PDF  图文排版", "PDF")):
            self._control(ttk.Radiobutton(formats, text=label, variable=self.format, value=value)).pack(
                side="left", padx=(0, 25))

        ttk.Separator(panel).grid(row=9, sticky="ew")
        settings = ttk.Frame(panel, style="Surface.TFrame")
        settings.grid(row=10, sticky="ew", pady=(12, 0))
        settings.columnconfigure(0, weight=1)
        ttk.Label(settings, textvariable=self.settings_summary, style="Muted.TLabel", wraplength=430).grid(
            row=0, column=0, sticky="w")
        self.settings_button = self._control(ttk.Button(settings, text="筛选与显示…", command=self._open_settings))
        self.settings_button.grid(row=0, column=1, padx=(12, 0))
        panel.rowconfigure(11, weight=1)

        footer = ttk.Frame(root, style="Footer.TFrame", padding=(30, 17, 30, 20))
        footer.pack(fill="x")
        footer.columnconfigure(0, weight=1)
        self.status = tk.StringVar(value="准备就绪，导出过程只读取本地记录。")
        self.status_label = ttk.Label(footer, textvariable=self.status, style="Footer.TLabel", wraplength=620)
        self.status_label.grid(row=0, column=0, columnspan=2, sticky="w")
        progress_track = ttk.Frame(footer, height=3, style="Footer.TFrame")
        progress_track.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(12, 15))
        self.progress = ttk.Progressbar(progress_track, mode="determinate", style="Green.Horizontal.TProgressbar")
        self.progress.place(x=0, y=0, relwidth=1, relheight=1)
        links = ttk.Frame(footer, style="Footer.TFrame")
        links.grid(row=2, column=0, sticky="w")
        self.result_actions = links
        self.open_file_button = ttk.Button(links, text="打开文件", command=self._open_file, state="disabled")
        self.open_file_button.pack(side="left")
        self.open_directory_button = ttk.Button(links, text="打开目录", command=self._open_directory, state="disabled")
        self.open_directory_button.pack(side="left", padx=(8, 0))
        links.grid_remove()
        self.export_button = ttk.Button(footer, text="导出聊天记录", command=self.export, style="Primary.TButton")
        self.export_button.grid(row=2, column=1, sticky="e", padx=(16, 0))
        self._mode_changed()
        self._fit_window(root, 700, 610)
        root.bind("<Control-Return>", lambda _event: self.export())
        root.bind("<Configure>", self._resize)
        root.after_idle(self.chat_entry.focus_set)
        self._poll_id = root.after(100, self.poll)
        root.protocol("WM_DELETE_WINDOW", self.close)

    @staticmethod
    def _fit_window(window, width, height):
        # Tk font sizes follow Windows scaling; let the required widget sizes
        # raise the minimum rather than clipping controls on a high-DPI display.
        window.update_idletasks()
        width = max(width, window.winfo_reqwidth())
        height = max(height, window.winfo_reqheight())
        window.minsize(width, height)
        window.geometry(f"{width}x{height}")

    def _style(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        font = ("Microsoft YaHei UI", 10)
        style.configure(".", font=font, background="#FFFFFF", foreground="#202823")
        style.configure("Surface.TFrame", background="#FFFFFF")
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 20, "bold"))
        style.configure("Section.TLabel", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("Muted.TLabel", foreground="#637069")
        style.configure("Chat.TEntry", font=("Microsoft YaHei UI", 12))
        style.configure("TEntry", padding=5, bordercolor="#CFD7D1", lightcolor="#FFFFFF", darkcolor="#FFFFFF")
        style.configure("TSpinbox", padding=3, arrowsize=13, bordercolor="#CFD7D1")
        style.map("TEntry", bordercolor=[("focus", "#078443")])
        style.map("TSpinbox", bordercolor=[("focus", "#078443")])
        style.configure("TButton", padding=(12, 7), background="#FFFFFF", bordercolor="#D4DBD6",
                        lightcolor="#FFFFFF", darkcolor="#FFFFFF", focusthickness=2, focuscolor="#078443")
        style.map("TButton", background=[("active", "#EAF1ED"), ("disabled", "#F1F3F1")],
                  foreground=[("disabled", "#87918A")])
        style.configure("Primary.TButton", padding=(23, 10), background="#078443", foreground="#FFFFFF",
                        bordercolor="#078443", lightcolor="#078443", darkcolor="#078443", focuscolor="#FFFFFF",
                        font=("Microsoft YaHei UI", 10, "bold"))
        style.map("Primary.TButton", background=[("disabled", "#B9D3C3"), ("pressed", "#046A35"), ("active", "#06763C")],
                  foreground=[("disabled", "#FFFFFF")], bordercolor=[("disabled", "#B9D3C3")])
        indicator_size = max(16, round(10 * float(self.root.tk.call("tk", "scaling"))))
        style.configure("TRadiobutton", padding=(0, 3), indicatormargin=6, indicatorsize=indicator_size,
                        indicatorbackground="#FFFFFF")
        style.map("TRadiobutton", indicatorbackground=[("disabled", "#E5EAE6"), ("selected", "#078443")],
                  indicatorforeground=[("disabled", "#87918A"), ("selected", "#078443")],
                  background=[("active", "#FFFFFF")])
        style.configure("TCheckbutton", padding=(0, 4), indicatormargin=6, indicatorsize=indicator_size)
        style.map("TCheckbutton", indicatorbackground=[("disabled", "#E5EAE6"), ("selected", "#078443")],
                  indicatorforeground=[("disabled", "#87918A"), ("selected", "#FFFFFF")],
                  background=[("active", "#FFFFFF")])
        style.configure("Footer.TFrame", background="#F3F5F4")
        style.configure("Footer.TLabel", background="#F3F5F4", foreground="#536258")
        style.configure("Green.Horizontal.TProgressbar", background="#078443", troughcolor="#E2E8E3", thickness=3)

    def _control(self, widget):
        self._controls.append(widget)
        return widget

    def _resize(self, event):
        if event.widget is self.root:
            self.status_label.configure(wraplength=max(500, event.width - 60))

    def _mode_changed(self):
        if not hasattr(self, "export_button"):
            return
        if self.forwarded_only.get():
            self.mode_hint.set("在所选范围内查找合并转发；每份完整内容独立保存。")
            self.export_button.configure(text="提取合并转发")
        else:
            self.mode_hint.set("聊天正文保留转发摘要，完整转发内容另存到同名目录。")
            self.export_button.configure(text="导出聊天记录")

    def _toggle_all(self):
        self.count_box.configure(state="disabled" if self.busy or self.all_records.get() else "normal")

    def _update_settings_summary(self):
        filters = []
        if self.use_interval.get():
            filters.append("限定时间")
        if self.limit_senders.get():
            filters.append("限定发言人")
        fields = [label for label, variable in (("时间", self.include_time), ("发言人", self.include_sender),
                                               ("标题", self.include_title)) if variable.get()]
        self.settings_summary.set(("、".join(filters) or "不限时间与发言人") + " · "
                                  + ("保留" + "、".join(fields) if fields else "仅保留正文"))

    def _open_settings(self):
        if self.busy:
            return
        if self.settings_dialog is not None and self.settings_dialog.winfo_exists():
            self.settings_dialog.lift()
            return
        dialog = tk.Toplevel(self.root)
        self.settings_dialog = dialog
        dialog.title("筛选与显示")
        dialog.transient(self.root)
        dialog.grab_set()
        frame = ttk.Frame(dialog, padding=24)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        ttk.Label(frame, text="筛选与显示", font=("Microsoft YaHei UI", 15, "bold")).grid(row=0, sticky="w", pady=(0, 18))
        self._control(ttk.Checkbutton(frame, text="限定时间范围", variable=self.use_interval,
                                     command=self._toggle_interval)).grid(row=1, sticky="w")
        times = ttk.Frame(frame)
        times.grid(row=2, sticky="ew", pady=(8, 5))
        times.columnconfigure((0, 1), weight=1)
        self.interval_boxes = []
        for column, (label, variable) in enumerate((("开始", self.start), ("结束", self.end))):
            ttk.Label(times, text=label, style="Muted.TLabel").grid(row=0, column=column, sticky="w")
            box = self._control(ttk.Entry(times, textvariable=variable, width=22))
            box.grid(row=1, column=column, sticky="ew", padx=(0, 12 if column == 0 else 0), pady=(5, 0))
            self.interval_boxes.append(box)
        self._toggle_interval()
        ttk.Label(frame, text="格式：2026-10-23 12:30，包含起止分钟。", style="Muted.TLabel").grid(row=3, sticky="w", pady=(0, 18))
        people = ttk.Frame(frame)
        people.grid(row=4, sticky="ew")
        self._control(ttk.Checkbutton(people, text="限定发言人", variable=self.limit_senders,
                                     command=self._toggle_senders)).pack(side="left")
        self.sender_button = self._control(ttk.Button(people, text="选择发言人…", command=self.choose_senders))
        self.sender_button.pack(side="right")
        self._toggle_senders()
        ttk.Label(frame, textvariable=self.sender_summary, style="Muted.TLabel", wraplength=510).grid(
            row=5, sticky="w", pady=(6, 19))
        ttk.Separator(frame).grid(row=6, sticky="ew")
        ttk.Label(frame, text="文件中显示", style="Section.TLabel").grid(row=7, sticky="w", pady=(17, 6))
        flags = ttk.Frame(frame)
        flags.grid(row=8, sticky="w")
        for label, variable in (("时间戳", self.include_time), ("发言人", self.include_sender), ("聊天标题", self.include_title)):
            self._control(ttk.Checkbutton(flags, text=label, variable=variable)).pack(side="left", padx=(0, 21))
        ttk.Label(frame, text="最近条数会在时间和发言人筛选后计算。", style="Muted.TLabel").grid(row=9, sticky="w", pady=(18, 0))
        frame.rowconfigure(10, weight=1)
        done = self._control(ttk.Button(frame, text="完成", command=self._close_settings))
        done.grid(row=11, sticky="e", pady=(18, 0))
        self._fit_window(dialog, 580, 530)
        dialog.protocol("WM_DELETE_WINDOW", self._close_settings)
        dialog.bind("<Escape>", lambda _event: self._close_settings())
        done.focus_set()

    def _close_settings(self):
        if self.busy:
            return
        if self.settings_dialog is not None:
            self.settings_dialog.destroy()
            self.settings_dialog = None
        self.settings_button.focus_set()

    def _toggle_interval(self):
        for field in self.interval_boxes:
            if field.winfo_exists():
                field.configure(state="normal" if self.use_interval.get() and not self.busy else "disabled")

    def _toggle_senders(self):
        if self.sender_button is not None and self.sender_button.winfo_exists():
            self.sender_button.configure(state="normal" if self.limit_senders.get() and not self.busy else "disabled")

    def _clear_senders(self):
        had_selection = bool(self.sender_ids)
        self.sender_ids = None
        if hasattr(self, "sender_summary"):
            self.sender_summary.set("聊天已更改，请重新选择发言人" if had_selection else "未限定时保留所有人和系统消息")

    def _set_busy(self, busy):
        self.busy = busy
        if busy:
            self._controls = [widget for widget in self._controls if widget.winfo_exists()]
            self._saved_states = [(widget, widget.cget("state")) for widget in self._controls]
            for widget, _state in self._saved_states:
                widget.configure(state="disabled")
            self.export_button.configure(state="disabled")
            self.open_file_button.configure(state="disabled")
            self.open_directory_button.configure(state="disabled")
            self.progress.configure(mode="indeterminate")
            self.progress.start(12)
        else:
            for widget, state in self._saved_states:
                if widget.winfo_exists():
                    widget.configure(state=state)
            self._saved_states = []
            self.export_button.configure(state="normal")
            self._toggle_all()
            self._toggle_interval()
            self._toggle_senders()
            self.progress.stop()
            self.progress.configure(mode="determinate", value=0)
            if self.last_result:
                self.open_file_button.configure(state="normal")
                self.open_directory_button.configure(state="normal")

    def _begin(self, task):
        if self.busy:
            return
        self._set_busy(True)
        def run():
            try:
                task()
            except Exception as error:
                self.events.put(("error", str(error)))
        threading.Thread(target=run, daemon=True).start()

    def _connect_chat(self, name, include_media=False):
        roots = self.engine.source_roots()
        if not roots:
            raise RuntimeError("找不到本机微信数据目录。")
        source = roots[0]
        accounts = self.engine.accounts(source)
        account = self.engine.settings.get("account")
        if account not in accounts:
            if len(accounts) != 1:
                raise RuntimeError("无法唯一确定本机微信账号。")
            account = accounts[0]
        chats = self.engine.connect(source, account,
            log=lambda line: self.events.put(("status", line)), include_media=include_media)
        matches = [chat for chat in chats if chat["name"] == name or chat["wxid"] == name]
        if len(matches) != 1:
            raise RuntimeError("聊天名称未唯一匹配，请填写完整群名、好友备注或 wxid。")
        return matches[0]

    def choose_senders(self):
        if self.busy:
            return
        name = self.chat_name.get().strip()
        if not name:
            messagebox.showerror("请输入聊天名称", "先填写要筛选的群名或好友备注。", parent=self.settings_dialog or self.root)
            return
        self.status.set("正在读取这个聊天的发言人……")
        def work():
            chat = self._connect_chat(name)
            self.events.put(("senders", (name, self.engine.list_senders(chat))))
        self._begin(work)

    def _sender_dialog(self, name, people):
        if name != self.chat_name.get().strip():
            self.status.set("聊天已更改，请重新选择发言人。")
            return
        parent = self.settings_dialog or self.root
        dialog = tk.Toplevel(parent)
        dialog.title("选择发言人")
        dialog.geometry("430x450")
        dialog.minsize(400, 350)
        dialog.transient(parent)
        dialog.grab_set()
        frame = ttk.Frame(dialog, padding=20)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="点击选择，可选择多人").pack(anchor="w", pady=(0, 10))
        area = ttk.Frame(frame)
        area.pack(fill="both", expand=True)
        listing = tk.Listbox(area, selectmode="multiple", exportselection=False, borderwidth=1,
                             relief="solid", highlightcolor="#078443", activestyle="dotbox",
                             font=("Microsoft YaHei UI", 10))
        listing.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(area, command=listing.yview)
        scrollbar.pack(side="right", fill="y")
        listing.configure(yscrollcommand=scrollbar.set)
        names = [person["name"] for person in people]
        for index, person in enumerate(people):
            label = person["name"]
            if names.count(label) > 1:
                label += " (" + person["id"][-8:] + ")"
            listing.insert("end", label)
            if person["id"] in (self.sender_ids or []):
                listing.selection_set(index)
        actions = ttk.Frame(frame)
        actions.pack(fill="x", pady=(14, 0))
        ttk.Button(actions, text="全选", command=lambda: listing.selection_set(0, "end")).pack(side="left")
        ttk.Button(actions, text="清空", command=lambda: listing.selection_clear(0, "end")).pack(side="left", padx=8)
        def close():
            dialog.destroy()
            if parent is not self.root and parent.winfo_exists():
                parent.grab_set()
        def confirm():
            selected = listing.curselection()
            if not selected:
                messagebox.showerror("请选择发言人", "至少选择一位；也可以选择系统消息。", parent=dialog)
                return
            self.sender_ids = [people[index]["id"] for index in selected]
            self.sender_summary.set(f"已选择 {len(selected)} 项；只保留这些人的发言")
            close()
        ttk.Button(actions, text="确定", command=confirm).pack(side="right")
        dialog.protocol("WM_DELETE_WINDOW", close)
        dialog.bind("<Escape>", lambda _event: close())
        listing.focus_set()

    def export(self):
        if self.busy or self.settings_dialog is not None:
            return
        name = self.chat_name.get().strip()
        if not name:
            messagebox.showerror("请输入聊天名称", "填写好友备注、群名或 wxid。", parent=self.root)
            self.chat_entry.focus_set()
            return
        try:
            try:
                latest = None if self.all_records.get() else int(self.count.get())
            except ValueError:
                raise ValueError("最近消息数量应为 1 到 2000。") from None
            if latest is not None and not 1 <= latest <= 2000:
                raise ValueError("最近消息数量应为 1 到 2000。")
            start, end = (self.start.get().strip(), self.end.get().strip()) if self.use_interval.get() else ("", "")
            time_window(start, end)
            sender_ids = self.sender_ids if self.limit_senders.get() else None
            if self.limit_senders.get() and not sender_ids:
                raise ValueError("请在“筛选与显示”中选择至少一位发言人。")
        except ValueError as error:
            messagebox.showerror("请检查导出选项", str(error), parent=self.root)
            return
        format_ = self.format.get().lower()
        filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).rstrip(" .") or "聊天"
        if self.forwarded_only.get():
            filename += "_合并转发"
        output_dir = self.engine.data_dir.parent / "exports"
        output_dir.mkdir(parents=True, exist_ok=True)
        output = filedialog.asksaveasfilename(parent=self.root, title="保存导出文件",
            initialdir=str(output_dir), initialfile=filename + "_" + datetime.now().strftime("%Y%m%d_%H%M%S") + "." + format_,
            defaultextension="." + format_, filetypes=[(self.format.get() + " 文件", "*." + format_)],
            confirmoverwrite=True)
        if not output:
            return
        flags = dict(sender_ids=sender_ids, include_time=self.include_time.get(),
                     include_sender=self.include_sender.get(), include_title=self.include_title.get(),
                     forwarded_only=self.forwarded_only.get())
        self.status.set("正在读取本地微信记录……")
        def work():
            chat = self._connect_chat(name, include_media=format_ == "pdf")
            result = self.engine.export(chat, output, format_, start, end, latest,
                log=lambda line: self.events.put(("status", line)), **flags)
            self.events.put(("done", result))
        self._begin(work)

    def _show_done(self, value):
        self.last_result = value
        noun = "条合并转发" if value.get("forwarded_only") else "条聊天记录"
        detail = f"已导出 {value['count']} {noun}"
        forwarded = value.get("forwarded_count", 0)
        if forwarded:
            detail += f"，另存 {forwarded} 份转发文件（含嵌套）"
        elif value.get("forwarded_only"):
            detail += "，所选范围内没有可导出的转发内容"
        missing = value.get("forwarded_missing", 0)
        if missing:
            detail += f"；{missing} 份转发明细缺失或不完整，已标明"
        media = value.get("media")
        if isinstance(media, dict):
            missing_media = sum(media.get(key, 0) for key in
                                ("missing_images", "missing_covers", "missing_emojis", "missing_videos"))
            if missing_media:
                detail += f"；{missing_media} 个媒体不可读取，已在 PDF 中标明"
        self.status.set(detail + "。\n" + Path(value["path"]).name)
        self.open_directory_button.configure(text="转发目录" if value.get("forwarded_directory") else "打开目录")
        self.result_actions.grid()
        self._fit_window(self.root, self.root.winfo_width(), self.root.winfo_height())

    def _open_path(self, path):
        try:
            os.startfile(str(path))
        except OSError as error:
            messagebox.showerror("无法打开", str(error), parent=self.root)

    def _open_file(self):
        if self.last_result and not self.busy:
            self._open_path(self.last_result["path"])

    def _open_directory(self):
        if self.last_result and not self.busy:
            self._open_path(self.last_result.get("forwarded_directory") or Path(self.last_result["path"]).parent)

    def poll(self):
        if self._closed:
            return
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "status":
                    self.status.set(value)
                    continue
                if kind == "done":
                    self._show_done(value)
                self._set_busy(False)
                if kind == "senders":
                    self._sender_dialog(*value)
                    self.status.set("选择要保留发言的人。")
                elif kind == "error":
                    self.status.set("导出未完成：" + value)
                    messagebox.showerror("操作未完成", value, parent=self.settings_dialog or self.root)
        except queue.Empty:
            pass
        self._poll_id = self.root.after(100, self.poll)

    def close(self):
        if self.busy:
            messagebox.showinfo("正在处理", "请等待当前操作完成后关闭窗口。", parent=self.settings_dialog or self.root)
            return
        self._closed = True
        self.root.after_cancel(self._poll_id)
        self.root.destroy()


def main():
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    root = tk.Tk()
    ExportWindow(root)
    root.mainloop()

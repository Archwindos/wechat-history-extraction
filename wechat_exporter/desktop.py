"""A small export window with optional time, speaker, metadata and PDF settings."""
from datetime import datetime
from pathlib import Path
import queue
import re
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .backend import Engine, time_window


class ExportWindow:
    def __init__(self, root):
        self.root = root
        self.engine = Engine()
        self.events = queue.Queue()
        self.busy = False
        self.sender_ids = None
        root.title("微信聊天导出 v4")
        root.geometry("640x310")
        root.resizable(False, False)
        root.option_add("*Font", ("Microsoft YaHei UI", 10))
        panel = ttk.Frame(root, padding=24)
        panel.pack(fill="both", expand=True)
        ttk.Label(panel, text="聊天名称或备注").pack(anchor="w")
        self.chat_name = tk.StringVar(value="")
        self.chat_name.trace_add("write", lambda *_: self._clear_senders())
        ttk.Entry(panel, textvariable=self.chat_name).pack(fill="x", pady=(6, 14))
        line = ttk.Frame(panel)
        line.pack(fill="x")
        ttk.Label(line, text="最近").pack(side="left")
        self.count = tk.StringVar(value="20")
        self.count_box = ttk.Spinbox(line, from_=1, to=2000, width=6, textvariable=self.count)
        self.count_box.pack(side="left", padx=8)
        ttk.Label(line, text="条").pack(side="left")
        self.all_records = tk.BooleanVar(value=False)
        ttk.Checkbutton(line, text="全部本地记录", variable=self.all_records,
                        command=lambda: self.count_box.configure(
                            state="disabled" if self.all_records.get() else "normal")).pack(side="left", padx=20)
        ttk.Label(line, text="格式").pack(side="left", padx=(10, 6))
        self.format = tk.StringVar(value="TXT")
        ttk.Combobox(line, textvariable=self.format, values=["TXT", "PDF"],
                     state="readonly", width=7).pack(side="left")
        self.advanced = False
        self.more_toggle = ttk.Button(panel, text="更多选项", command=self._toggle_options)
        self.more_toggle.pack(anchor="w", pady=(16, 0))
        self.options = ttk.Frame(panel, padding=(0, 12, 0, 0))
        self.use_interval = tk.BooleanVar(value=False)
        ttk.Checkbutton(self.options, text="限定时间范围（包含起止分钟）", variable=self.use_interval,
                        command=self._toggle_interval).grid(row=0, column=0, columnspan=4, sticky="w")
        today = datetime.now().strftime("%Y-%m-%d")
        self.start = tk.StringVar(value=today + " 00:00")
        self.end = tk.StringVar(value=today + " 23:59")
        ttk.Label(self.options, text="开始").grid(row=1, column=0, sticky="w", pady=8)
        start_box = ttk.Entry(self.options, textvariable=self.start, width=21, state="disabled")
        start_box.grid(row=1, column=1, sticky="w", padx=(8, 20))
        ttk.Label(self.options, text="结束").grid(row=1, column=2, sticky="w")
        end_box = ttk.Entry(self.options, textvariable=self.end, width=21, state="disabled")
        end_box.grid(row=1, column=3, sticky="w", padx=8)
        self.interval_boxes = [start_box, end_box]
        ttk.Label(self.options, text="时间格式：2026-10-14 12:30").grid(
            row=2, column=0, columnspan=4, sticky="w", pady=(0, 12))
        self.limit_senders = tk.BooleanVar(value=False)
        ttk.Checkbutton(self.options, text="限定发言人", variable=self.limit_senders,
                        command=self._toggle_senders).grid(row=3, column=0, columnspan=2, sticky="w")
        self.sender_button = ttk.Button(self.options, text="选择发言人…", command=self.choose_senders,
                                        state="disabled")
        self.sender_button.grid(row=3, column=2, columnspan=2, sticky="w")
        self.sender_summary = tk.StringVar(value="未选择；不勾选时保留所有人和系统消息")
        ttk.Label(self.options, textvariable=self.sender_summary, wraplength=570).grid(
            row=4, column=0, columnspan=4, sticky="w", pady=(8, 12))
        flags = ttk.Frame(self.options)
        flags.grid(row=5, column=0, columnspan=4, sticky="w")
        self.include_time = tk.BooleanVar(value=True)
        self.include_sender = tk.BooleanVar(value=True)
        self.include_title = tk.BooleanVar(value=True)
        for label, variable in (("保留时间戳", self.include_time), ("保留发言人", self.include_sender),
                                ("保留聊天标题", self.include_title)):
            ttk.Checkbutton(flags, text=label, variable=variable).pack(side="left", padx=(0, 16))
        actions = ttk.Frame(panel)
        actions.pack(fill="x", pady=(18, 10))
        self.export_button = ttk.Button(actions, text="导出聊天记录", command=self.export)
        self.export_button.pack(side="left")
        self.status = tk.StringVar(value="导出电脑本地保存的聊天记录。")
        ttk.Label(panel, textvariable=self.status, wraplength=580).pack(anchor="w")
        self.root.after(100, self.poll)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def _toggle_options(self):
        self.advanced = not self.advanced
        if self.advanced:
            self.more_toggle.configure(text="收起选项")
            self.options.pack(fill="x", after=self.more_toggle)
            self.root.geometry("640x550")
        else:
            self.more_toggle.configure(text="更多选项")
            self.options.pack_forget()
            self.root.geometry("640x310")

    def _toggle_interval(self):
        for field in self.interval_boxes:
            field.configure(state="normal" if self.use_interval.get() else "disabled")

    def _toggle_senders(self):
        self.sender_button.configure(state="normal" if self.limit_senders.get() and not self.busy else "disabled")

    def _clear_senders(self):
        self.sender_ids = None
        if hasattr(self, "sender_summary"):
            self.sender_summary.set("聊天已更改，请重新选择发言人")

    def _begin(self, task):
        if self.busy:
            return
        self.busy = True
        self.export_button.configure(state="disabled")
        self.sender_button.configure(state="disabled")
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
        name = self.chat_name.get().strip()
        if not name:
            messagebox.showerror("请输入聊天名称", "先填写要筛选的群名或好友备注。", parent=self.root)
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
        dialog = tk.Toplevel(self.root)
        dialog.title("选择发言人")
        dialog.geometry("430x450")
        dialog.transient(self.root)
        dialog.grab_set()
        frame = ttk.Frame(dialog, padding=20)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="点击选择，可选择多人").pack(anchor="w", pady=(0, 10))
        area = ttk.Frame(frame)
        area.pack(fill="both", expand=True)
        listing = tk.Listbox(area, selectmode="multiple", exportselection=False)
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
        def confirm():
            selected = listing.curselection()
            if not selected:
                messagebox.showerror("请选择发言人", "至少选择一位；也可以选择系统消息。", parent=dialog)
                return
            self.sender_ids = [people[index]["id"] for index in selected]
            self.sender_summary.set(f"已选择 {len(selected)} 项；导出时只保留这些人的发言")
            dialog.destroy()
        ttk.Button(actions, text="确定", command=confirm).pack(side="right")

    def export(self):
        if self.busy:
            return
        name = self.chat_name.get().strip()
        if not name:
            messagebox.showerror("请输入聊天名称", "填写好友备注、群名或 wxid。", parent=self.root)
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
                raise ValueError("请先点击“选择发言人”选择至少一位。")
        except ValueError as error:
            messagebox.showerror("请检查导出选项", str(error), parent=self.root)
            return
        format_ = self.format.get().lower()
        filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).rstrip(" .") or "聊天"
        output_dir = self.engine.data_dir.parent / "exports"
        output_dir.mkdir(parents=True, exist_ok=True)
        output = filedialog.asksaveasfilename(parent=self.root, title="保存聊天记录",
            initialdir=str(output_dir), initialfile=filename + "_" + datetime.now().strftime("%Y%m%d_%H%M%S") + "." + format_,
            defaultextension="." + format_, filetypes=[(self.format.get() + " 文件", "*." + format_)],
            confirmoverwrite=True)
        if not output:
            return
        flags = dict(sender_ids=sender_ids, include_time=self.include_time.get(),
                     include_sender=self.include_sender.get(), include_title=self.include_title.get())
        self.status.set("正在读取本地微信记录……")
        def work():
            chat = self._connect_chat(name, include_media=format_ == "pdf")
            result = self.engine.export(chat, output, format_, start, end, latest,
                log=lambda line: self.events.put(("status", line)), **flags)
            self.events.put(("done", result))
        self._begin(work)

    def poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "status":
                    self.status.set(value)
                    continue
                self.busy = False
                self.export_button.configure(state="normal")
                self._toggle_senders()
                if kind == "senders":
                    self._sender_dialog(*value)
                    self.status.set("选择要保留发言的人。")
                elif kind == "done":
                    detail = f"已导出 {value['count']} 条。"
                    media = value.get("media")
                    if isinstance(media, dict):
                        detail += f"\n图片 {media['images']} 张，封面 {media['covers']} 张。"
                        detail += f"\n表情 {media.get('emojis', 0)} 张，视频预览 {media.get('videos', 0)} 张。"
                        missing = sum(media.get(key, 0) for key in
                                      ("missing_images", "missing_covers", "missing_emojis", "missing_videos"))
                        if missing:
                            detail += f"\n有 {missing} 个媒体未保存在本机或无法读取，已在 PDF 中标明。"
                    self.status.set(detail.replace("\n", " ") + " " + Path(value["path"]).name)
                    messagebox.showinfo("导出完成", detail + "\n" + value["path"], parent=self.root)
                else:
                    self.status.set(value)
                    messagebox.showerror("导出未完成", value, parent=self.root)
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def close(self):
        if self.busy:
            messagebox.showinfo("正在导出", "请等待当前操作完成后关闭窗口。", parent=self.root)
            return
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

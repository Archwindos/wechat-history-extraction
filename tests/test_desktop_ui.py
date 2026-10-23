"""Exercise the actual Tk widgets using synthetic data, never a local account."""
from pathlib import Path
import threading
import time
import tkinter as tk

import pytest

from wechat_exporter import desktop


class FakeEngine:
    def __init__(self, directory):
        self.data_dir = directory / "state"
        self.settings = {"account": "synthetic"}
        self.calls = []
        self.release = threading.Event()
        self.release.set()

    def source_roots(self):
        return [Path("synthetic-source")]

    def accounts(self, source):
        return ["synthetic"]

    def connect(self, source, account, log, include_media=False):
        self.calls.append(("connect", include_media))
        return [{"name": "合成演示群", "wxid": "synthetic@chatroom"}]

    def export(self, chat, output, format_, start, end, latest, log, **flags):
        self.calls.append(("export", format_, start, end, latest, flags))
        self.release.wait(3)
        return dict(path=output, count=2, forwarded_count=3,
                    forwarded_directory=str(Path(output).with_name(Path(output).name + "_转发记录")),
                    forwarded_missing=1, forwarded_only=flags["forwarded_only"])


@pytest.fixture(scope="module")
def tk_root():
    root = tk.Tk()
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture
def window(tmp_path, monkeypatch, tk_root):
    # One Tcl interpreter avoids repeated Tk init/finalize in the same process.
    root = tk.Toplevel(tk_root)
    root.withdraw()
    fake = FakeEngine(tmp_path)
    app = desktop.ExportWindow(root, engine=fake)
    messages = []
    monkeypatch.setattr(desktop.messagebox, "showerror", lambda *args, **kwargs: messages.append(args))
    monkeypatch.setattr(desktop.messagebox, "showinfo", lambda *args, **kwargs: messages.append(args))
    app.test_messages = messages
    yield app
    fake.release.set()
    if app.settings_dialog:
        app._close_settings()
    app.busy = False
    app.close()


def wait_for_idle(app):
    deadline = time.monotonic() + 3
    while app.busy and time.monotonic() < deadline:
        app.root.update()
        time.sleep(.01)
    assert not app.busy


def test_settings_keep_filter_values_and_conditional_widget_states(window):
    window._open_settings()
    window.root.update()
    assert str(window.interval_boxes[0].cget("state")) == "disabled"
    assert str(window.sender_button.cget("state")) == "disabled"
    window.use_interval.set(True)
    window._toggle_interval()
    window.limit_senders.set(True)
    window._toggle_senders()
    window.include_title.set(False)
    window.start.set("2026-10-22 10:30")
    assert str(window.interval_boxes[0].cget("state")) == "normal"
    assert str(window.sender_button.cget("state")) == "normal"
    assert "限定时间、限定发言人" in window.settings_summary.get()
    window._close_settings()
    window._open_settings()
    assert window.start.get() == "2026-10-22 10:30"
    assert not window.include_title.get()


def test_forwarded_export_locks_inputs_then_exposes_output_actions(window, tmp_path, monkeypatch):
    output = tmp_path / "合成转发.pdf"
    monkeypatch.setattr(desktop.filedialog, "asksaveasfilename", lambda **kwargs: str(output))
    opened = []
    monkeypatch.setattr(window, "_open_path", opened.append)
    window.chat_name.set("合成演示群")
    window.format.set("PDF")
    window.forwarded_only.set(True)
    window.all_records.set(True)
    window.limit_senders.set(True)
    window.sender_ids = ["synthetic-person"]
    window.engine.release.clear()
    window.export()
    assert window.busy
    assert str(window.chat_entry.cget("state")) == "disabled"
    assert str(window.settings_button.cget("state")) == "disabled"
    assert str(window.export_button.cget("state")) == "disabled"
    window.export()  # Repeated keyboard activation cannot start another job.
    window.engine.release.set()
    wait_for_idle(window)
    export_calls = [call for call in window.engine.calls if call[0] == "export"]
    assert len(export_calls) == 1
    assert export_calls[0][1:5] == ("pdf", "", "", None)
    assert export_calls[0][5]["forwarded_only"] is True
    assert export_calls[0][5]["sender_ids"] == ["synthetic-person"]
    assert ("connect", True) in window.engine.calls
    assert str(window.chat_entry.cget("state")) == "normal"
    assert str(window.count_box.cget("state")) == "disabled"
    assert "3 份转发文件" in window.status.get()
    assert "1 份转发明细缺失或不完整" in window.status.get()
    assert str(window.open_file_button.cget("state")) == "normal"
    window.open_file_button.invoke()
    window.open_directory_button.invoke()
    assert opened == [str(output), str(output) + "_转发记录"]


def test_default_mode_and_validation_do_not_read_account(window, monkeypatch):
    assert not window.forwarded_only.get()
    assert window.export_button.cget("text") == "导出聊天记录"
    window.chat_name.set("合成演示群")
    window.count.set("0")
    window.export()
    assert window.test_messages and "1 到 2000" in window.test_messages[-1][1]
    assert window.engine.calls == []
    window.count.set("20")
    window.limit_senders.set(True)
    window.export()
    assert "选择至少一位" in window.test_messages[-1][1]
    assert window.engine.calls == []


def test_worker_error_recovers_controls(window):
    def fail():
        raise RuntimeError("合成错误")
    window._begin(fail)
    wait_for_idle(window)
    assert "合成错误" in window.status.get()
    assert str(window.chat_entry.cget("state")) == "normal"
    assert str(window.export_button.cget("state")) == "normal"
    assert str(window.open_file_button.cget("state")) == "disabled"


def test_normal_txt_export_keeps_default_mode_and_recent_scope(window, tmp_path, monkeypatch):
    monkeypatch.setattr(desktop.filedialog, "asksaveasfilename", lambda **kwargs: str(tmp_path / "合成聊天.txt"))
    window.chat_name.set("合成演示群")
    window.export()
    wait_for_idle(window)
    call = next(call for call in window.engine.calls if call[0] == "export")
    assert call[1:5] == ("txt", "", "", 20)
    assert call[5]["forwarded_only"] is False
    assert ("connect", False) in window.engine.calls


def test_chat_change_invalidates_selected_senders(window):
    window.chat_name.set("合成演示群")
    window.sender_ids = ["synthetic-person"]
    window.chat_name.set("另一个合成群")
    assert window.sender_ids is None
    assert "重新选择" in window.sender_summary.get()


def test_high_dpi_layout_keeps_primary_action_visible(window):
    window.root.deiconify()
    window.root.update()
    button_bottom = window.export_button.winfo_rooty() + window.export_button.winfo_height()
    window_bottom = window.root.winfo_rooty() + window.root.winfo_height()
    assert button_bottom <= window_bottom - 10
    assert window.chat_entry.winfo_width() >= 500


def test_indicator_size_follows_tk_scaling(window):
    style = desktop.ttk.Style(window.root)
    expected = max(16, round(10 * float(window.root.tk.call("tk", "scaling"))))
    for widget_style, element in (("TCheckbutton", "Checkbutton.indicator"),
                                  ("TRadiobutton", "Radiobutton.indicator")):
        assert "indicatorsize" in style.element_options(element)
        assert int(style.lookup(widget_style, "indicatorsize")) == expected

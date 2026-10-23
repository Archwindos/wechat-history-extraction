"""Preview the desktop window using synthetic text only; never connect to WeChat."""
import argparse
from pathlib import Path
import sys
from types import SimpleNamespace
import tkinter as tk

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wechat_exporter.desktop import ExportWindow


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screenshot-dir", type=Path)
    args = parser.parse_args()
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    root = tk.Tk()
    # No Engine instance is created, even in interactive preview.
    engine = SimpleNamespace(data_dir=Path("synthetic-preview/state"), settings={})
    window = ExportWindow(root, engine=engine)
    window.chat_name.set("周末读书会（合成演示）")
    window.export_button.configure(command=lambda: None)
    window.choose_senders = lambda: None
    root.title("微信聊天导出 · 合成预览")
    if args.screenshot_dir:
        from PIL import ImageGrab
        args.screenshot_dir.mkdir(parents=True, exist_ok=True)
        root.attributes("-topmost", True)
        root.geometry("+100+100")

        def capture(name, widget=root):
            root.update()
            left, top = widget.winfo_rootx(), widget.winfo_rooty()
            ImageGrab.grab(bbox=(left, top, left + widget.winfo_width(), top + widget.winfo_height())).save(
                args.screenshot_dir / name)

        def settings():
            window._open_settings()
            window.settings_dialog.attributes("-topmost", True)
            root.after(400, lambda: finish())

        def finish():
            capture("settings.png", window.settings_dialog)
            window._close_settings()
            window.close()

        def completed():
            capture("default.png")
            window.forwarded_only.set(True)
            window.format.set("PDF")
            window._show_done(dict(path="合成演示_合并转发.pdf", count=4, forwarded_count=6,
                                   forwarded_only=True, forwarded_directory="合成演示_合并转发.pdf_转发记录"))
            window._set_busy(False)
            root.after(300, lambda: capture("completed.png"))
            root.after(600, settings)

        root.after(500, completed)
    root.mainloop()


if __name__ == "__main__":
    main()

"""Render Chinese transcripts with local media through Pandoc and XeLaTeX."""
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from PIL import Image, ImageOps

from .media import LocalMedia


TEMPLATE = r"""\documentclass[10pt,a4paper]{article}
\usepackage[margin=15mm]{geometry}
\usepackage{fontspec}
\usepackage{xeCJK}
\setmainfont{$chinese-font$}
\setCJKmainfont{$chinese-font$}
$if(emoji-font)$
\newfontfamily\wechatemojiLatin{$emoji-font$}
\setCJKfamilyfont{wechatemojiCJK}{$emoji-font$}
\newcommand{\wechatemoji}{\wechatemojiLatin\CJKfamily{wechatemojiCJK}}
$else$\newcommand{\wechatemoji}{}$endif$
\usepackage{graphicx}
\usepackage{xcolor}
\setlength{\parindent}{0pt}
\setlength{\parskip}{1.5pt}
\linespread{1}
\emergencystretch=3em
\pagestyle{$if(has-metadata)$plain$else$empty$endif$}
\begin{document}
\fontsize{9}{11}\selectfont
\raggedright
$body$
\end{document}
"""


def pdf_tools():
    local = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local")))
    program_files = Path(os.environ.get("ProgramFiles", "C:/Program Files"))
    locations = {
        "pandoc": [local / "Pandoc/pandoc.exe", program_files / "Pandoc/pandoc.exe"],
        "xelatex": [program_files / "MiKTeX/miktex/bin/x64/xelatex.exe",
                    local / "Programs/MiKTeX/miktex/bin/x64/xelatex.exe"],
    }
    found = {}
    for name, candidates in locations.items():
        executable = os.environ.get(f"WECHAT_{name.upper()}") or shutil.which(name) or next(
            (str(p) for p in candidates if p.is_file()), None)
        if not executable:
            raise RuntimeError(f"PDF 导出需要 {name}，请安装 Pandoc 和 MiKTeX（XeLaTeX）后重试。")
        found[name] = executable
    return found


def chinese_font():
    folder = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    for file, name in (("msyh.ttc", "Microsoft YaHei"), ("simhei.ttf", "SimHei"), ("simsun.ttc", "SimSun")):
        if (folder / file).is_file():
            return name
    raise RuntimeError("没有找到 PDF 所需的中文字体，请安装微软雅黑或宋体。")


EMOJI_TEXT = r"([\U0001F000-\U0001FAFF\u21A9-\u21AA\u2600-\u27BF][\U0001F000-\U0001FAFF\u21A9-\u21AA\u2600-\u27BF\uFE0E\uFE0F\u200D]*)"


def text_inlines(text):
    # Str nodes escape user text in Pandoc; only our own layout uses raw TeX.
    result = []
    for index, line in enumerate(text.replace("\r\n", "\n").replace("\r", "\n").split("\n")):
        if index:
            result.append({"t": "LineBreak"})
        if not line:
            result.append({"t": "RawInline", "c": ["latex", r"\mbox{}"]})
        for part in re.split(r"([!-~]{25,})", line):
            if not part:
                continue
            if re.fullmatch(r"[!-~]{25,}", part):
                for offset in range(len(part)):
                    if offset:
                        result.append({"t": "RawInline", "c": ["latex", r"\allowbreak{}"]})
                    result.append({"t": "Str", "c": part[offset]})
            else:
                for piece in re.split(EMOJI_TEXT, part):
                    if not piece:
                        continue
                    if re.fullmatch(EMOJI_TEXT, piece):
                        result.extend([{"t": "RawInline", "c": ["latex", r"\begingroup\wechatemoji "]},
                                       {"t": "Str", "c": piece},
                                       {"t": "RawInline", "c": ["latex", r"\endgroup{}"]}])
                    else:
                        result.append({"t": "Str", "c": piece})
    return result


def paragraph(text, style=""):
    inlines = text_inlines(text)
    if style:
        inlines = [{"t": "RawInline", "c": ["latex", r"\begingroup" + style]}] + inlines
        inlines.append({"t": "RawInline", "c": ["latex", r"\par\endgroup"]})
    return {"t": "Para", "c": inlines}


def write_pdf(path, records, engine, chat, header, *, include_time=True, include_sender=True, log=lambda _: None):
    executables = pdf_tools()
    font = chinese_font()
    counts = {"images": 0, "covers": 0, "missing_images": 0, "missing_covers": 0,
              "emojis": 0, "videos": 0, "missing_emojis": 0, "missing_videos": 0}
    blocks = []
    media = LocalMedia(engine, chat)
    try:
        with tempfile.TemporaryDirectory(prefix="pdf-", dir=engine.data_dir) as temporary:
            folder = Path(temporary)
            def picture(data, index, cover=False, emoji=False):
                with Image.open(io.BytesIO(data)) as image:
                    image = ImageOps.exif_transpose(image)
                    if image.mode not in ("RGB", "RGBA", "L"):
                        image = image.convert("RGBA")
                    max_width, max_height = (80, 80) if emoji else (160, 190) if cover else (260, 220)
                    scale = min(max_width / image.width, max_height / image.height, 1)
                    attrs = [["width", f"{image.width * scale:.2f}pt"],
                             ["height", f"{image.height * scale:.2f}pt"]]
                    filename = f"{'emoji' if emoji else 'cover' if cover else 'image'}-{index}.png"
                    image.save(folder / filename)
                # Empty captions prevent floating figures; each image stays in message order.
                return {"t": "Para", "c": [{"t": "Image", "c": [["", [], attrs], [], [filename, ""]]}]}

            if header:
                blocks.append(paragraph(header.rstrip(), r"\fontsize{11}{13}\selectfont\bfseries "))
                blocks.append({"t": "RawBlock", "c": ["latex", r"\par\addvspace{6pt}"]})
            for index, record in enumerate(records, 1):
                labels = ([record["time"]] if include_time else [])
                if include_sender:
                    labels.append(record["sender_name"])
                if labels:
                    blocks.append(paragraph("  ".join(labels), r"\fontsize{7.5}{9}\selectfont\color{gray} "))
                    blocks.append({"t": "RawBlock", "c": ["latex", r"\nopagebreak[4]"]})
                if record["type"] == "3":
                    image = media.image(record)
                    if image:
                        blocks.append(picture(image, index))
                        counts["images"] += 1
                    else:
                        blocks.append(paragraph("[图片：本地文件未下载或无法读取]"))
                        counts["missing_images"] += 1
                elif record["type"] == "47" or record.get("app", {}).get("kind") == "emoji":
                    image = media.emoji(record)
                    if image:
                        blocks.append(picture(image, index, emoji=True))
                        counts["emojis"] += 1
                    else:
                        blocks.append(paragraph("[表情：本地缓存不存在或无法读取]", r"\fontsize{7.5}{9}\selectfont\color{gray} "))
                        counts["missing_emojis"] += 1
                elif record["type"] == "43" or record.get("app", {}).get("kind") in ("video_link", "finder", "finder_live"):
                    app = record.get("app", {})
                    if app:
                        from .backend import appmsg
                        blocks.append(paragraph(f"[{app['label']}] " + appmsg.format_app_message(app, include_url=False)))
                    image = media.video(record)
                    if image:
                        blocks.append(picture(image, index, cover=True))
                        counts["videos"] += 1
                    else:
                        blocks.append(paragraph("[视频：本地没有预览图]", r"\fontsize{7.5}{9}\selectfont\color{gray} "))
                        counts["missing_videos"] += 1
                else:
                    blocks.append(paragraph(record["content"]))
                    if record.get("app"):
                        image = media.cover(record)
                        if image:
                            blocks.append({"t": "RawBlock", "c": ["latex", r"\nopagebreak[4]"]})
                            blocks.append(picture(image, index, cover=True))
                            counts["covers"] += 1
                        elif record["app"].get("kind") == "file":
                            blocks.append(paragraph("[本地文件或封面未下载，无法生成预览]", r"\fontsize{7.5}{9}\selectfont\color{gray} "))
                            counts["missing_covers"] += 1
                blocks.append({"t": "RawBlock", "c": ["latex", r"\par\addvspace{3pt}"]})
                if index % 100 == 0:
                    log(f"已整理 {index} 条消息")
            if not blocks:
                blocks.append(paragraph("所选范围内没有本地聊天记录。"))
            document = {
                "pandoc-api-version": [1, 23, 1],
                "meta": {"chinese-font": {"t": "MetaString", "c": font},
                         "emoji-font": {"t": "MetaString", "c": "Segoe UI Emoji" if
                             (Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/seguiemj.ttf").is_file() else ""},
                         "has-metadata": {"t": "MetaBool", "c": bool(header or include_time or include_sender)}},
                "blocks": blocks,
            }
            (folder / "transcript.json").write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
            (folder / "transcript.tex").write_text(TEMPLATE, encoding="utf-8")
            log("正在使用 Pandoc + XeLaTeX 生成 PDF……")
            command = [executables["pandoc"], "transcript.json", "--from=json", "--to=latex", "--standalone",
                       "--template=transcript.tex", "--wrap=none", "--resource-path=.",
                       "--pdf-engine=" + executables["xelatex"], "--pdf-engine-opt=-no-shell-escape",
                       "--output=transcript.pdf"]
            try:
                completed = subprocess.run(command, cwd=folder, capture_output=True, encoding="utf-8", errors="replace",
                                           timeout=600, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            except subprocess.TimeoutExpired:
                raise RuntimeError("PDF 排版超过 10 分钟，请缩小聊天范围后重试。") from None
            if completed.returncode or not (folder / "transcript.pdf").is_file():
                error = (completed.stderr or completed.stdout).strip()
                raise RuntimeError("Pandoc + XeLaTeX 生成 PDF 失败：\n" + error[-1600:])
            shutil.copyfile(folder / "transcript.pdf", path)
    finally:
        media.close()
    return counts

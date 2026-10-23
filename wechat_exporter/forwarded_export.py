"""Save merged forwards beside a transcript, keeping only cards in the parent."""
from pathlib import Path
import re
import json


def card_text(bundle, detail_file="", *, include_time=True, include_sender=True):
    lines = ["[合并转发] " + (bundle.get("title") or "聊天记录")]
    if include_time and include_sender:
        lines.extend(bundle.get("preview", [])[:3])
    else:
        # Native card previews can contain sender names and times. Rebuild from
        # structured items when hiding metadata; do not guess at colon prefixes.
        for item in bundle.get("records", [])[:3]:
            text = item.get("content", "").splitlines()
            text = text[0] if text else "[空消息]"
            if include_sender and item.get("sender_name"):
                text = item["sender_name"] + ": " + text
            lines.append(text[:160])
    if detail_file:
        lines.append("详情：" + detail_file)
    if bundle.get("notice"):
        lines.append("[" + bundle["notice"] + "]")
    return "\n".join(lines)


def display_record(record, include_time, include_sender):
    labels = [f"[{record['time']}]"] if include_time and record.get("time") else []
    if include_sender and record.get("sender_name"):
        labels.append(f"[{record['sender_name']}]")
    return " ".join(labels + [record["content"]])


def json_record(record, include_time, include_sender):
    result = {key: value for key, value in record.items() if not key.startswith("_")}
    if not include_time:
        for key in ("time", "timestamp"):
            result.pop(key, None)
    if not include_sender:
        for key in ("sender_id", "sender_name", "is_self"):
            result.pop(key, None)
    return result


class ForwardedWriter:
    """Write to a private staging directory; Engine publishes the complete export."""

    def __init__(self, folder, directory_name, output_format, engine, chat, *,
                 include_time=True, include_sender=True, include_title=True, log=lambda _: None):
        self.folder = Path(folder)
        self.directory_name = directory_name
        self.output_format = output_format
        self.engine, self.chat = engine, chat
        self.include_time, self.include_sender, self.include_title = include_time, include_sender, include_title
        self.log = log
        self.files = []
        self.missing = 0

    def separate(self, record, *, nested=False):
        bundle = record.get("forwarded")
        if bundle is None:
            return record
        from .backend import sha256_file, transcript_render

        number = len(self.files) + 1
        title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", bundle.get("title", ""))[:48].rstrip(" .") or "聊天记录"
        suffix = {"markdown": "md"}.get(self.output_format, self.output_format)
        filename = f"{number:04d}_{title}.{suffix}"
        self.folder.mkdir(exist_ok=True)
        destination = self.folder / filename
        entry = {"file": self.directory_name + "/" + filename,
                 "source_record_id": record.get("record_id"), "status": bundle["status"],
                 "count": len(bundle["records"]), "notice": bundle.get("notice", "")}
        self.files.append(entry)
        if bundle["status"] != "available":
            self.missing += 1
        # Reserve the parent's number before recursively separating nested forwards.
        children = [self.separate({**child, "record_id": f"{record.get('record_id', '')}/forward/{index}"}, nested=True)
                    for index, child in enumerate(bundle["records"], 1)]
        header = "合并转发：" + bundle["title"] + "\n" if self.include_title else ""
        source = []
        if self.include_time and record.get("time"):
            source.append(record["time"])
        if self.include_sender and record.get("sender_name"):
            source.append(record["sender_name"])
        if source:
            header += "转发消息：" + "  ".join(source) + "\n"
        if bundle.get("notice"):
            header += "[" + bundle["notice"] + "]\n"
        if not children:
            header += "本机未保存这份转发的详细消息。可在微信中打开后重新导出。\n"
        if self.output_format == "pdf":
            from .pdf_export import write_pdf
            entry["media"] = write_pdf(destination, children, self.engine, self.chat, header,
                include_time=self.include_time, include_sender=self.include_sender, log=self.log)
        else:
            with destination.open("x", encoding="utf-8", newline="") as handle:
                if self.output_format == "jsonl":
                    if not children:
                        handle.write(json.dumps({"type": "forwarded_notice", "content": header.strip()}, ensure_ascii=False) + "\n")
                    for child in children:
                        handle.write(json.dumps(json_record(child, self.include_time, self.include_sender), ensure_ascii=False) + "\n")
                else:
                    handle.writelines(transcript_render.render(header,
                        ((display_record(child, self.include_time, self.include_sender), None) for child in children),
                        self.output_format))
        entry["sha256"] = sha256_file(destination)
        reference = filename if nested else entry["file"]
        # Raw XML contains the entire forward. Do not repeat it in the main JSONL.
        result = {key: value for key, value in record.items()
                  if key not in ("forwarded", "original_content", "original_blob_base64", "app")}
        result["content"] = card_text(bundle, reference, include_time=self.include_time, include_sender=self.include_sender)
        result["app"] = {"kind": "chat_history", "app_type": 19, "label": "合并转发", "title": bundle["title"]}
        result["forwarded"] = {"title": bundle["title"], "detail_file": reference,
                               "status": bundle["status"], "count": len(children), "notice": bundle.get("notice", "")}
        return result

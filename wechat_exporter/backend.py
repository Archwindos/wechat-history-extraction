"""Desktop adapter around the pinned tzwkb/wechat-decrypt implementation.

All source databases are read-only. Keys and snapshots are local application data.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import heapq
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta
import uuid

WORKSPACE = Path(__file__).resolve().parents[1]
UPSTREAM = (Path(sys._MEIPASS) / "upstream" if getattr(sys, "frozen", False)
            else WORKSPACE / "vendor/wechat-decrypt")
sys.path[:0] = [str(UPSTREAM), str(UPSTREAM / "scripts/common"), str(UPSTREAM / "scripts/windows")]

import appmsg
import config
import contacts
import db
import decrypt_all
import extract_raw_key
import message
import transcript_render


class LogWriter(io.TextIOBase):
    def __init__(self, callback):
        self.callback = callback
        self.pending = ""
        self.fallback = sys.stdout
        self.in_callback = False

    def write(self, text):
        if self.in_callback:
            return self.fallback.write(text)
        self.pending += text
        while "\n" in self.pending:
            line, self.pending = self.pending.split("\n", 1)
            if line.strip():
                self.in_callback = True
                try:
                    self.callback(re.sub(r"\b[0-9a-fA-F]{64}\b", "[密钥已隐藏]", line))
                finally:
                    self.in_callback = False
        return len(text)

    def flush(self):
        pass


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def numbered_shards(storage):
    return sorted((p for p in (Path(storage) / "message").glob("message_*.db")
                   if re.fullmatch(r"message_[0-9]+\.db", p.name)),
                  key=lambda p: int(p.stem[8:]))


def time_window(start="", end=""):
    def parse(value, closing=False):
        for format_, step in (("%Y-%m-%d %H:%M", timedelta(minutes=1)),
                              ("%Y-%m-%d", timedelta(days=1))):
            try:
                moment = datetime.strptime(value.strip(), format_)
                return int((moment + step if closing else moment).timestamp())
            except ValueError:
                continue
        raise ValueError("时间请填写 YYYY-MM-DD HH:MM，例如 2026-10-14 12:30。")
    since = parse(start) if start else 0
    until = parse(end, closing=True) if end else 2**62
    if until <= since:
        raise ValueError("结束时间不能早于开始时间。")
    return since, until


@contextlib.contextmanager
def read_connection(path):
    connection = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        yield connection
    finally:
        connection.close()


class Engine:
    def __init__(self, data_dir=None):
        base = Path(sys.executable).parent if getattr(sys, "frozen", False) else WORKSPACE
        self.data_dir = Path(data_dir or base / "private-data").resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.settings_path = self.data_dir / "settings.json"
        self.key_path = self.data_dir / "key_windows.txt"
        self.settings = {}
        if self.settings_path.exists():
            self.settings = json.loads(self.settings_path.read_text(encoding="utf-8"))
        self.storage = None
        self.snapshot = None
        active = self.settings.get("snapshot")
        if active:
            candidate = (self.data_dir / active).resolve()
            if candidate.is_relative_to(self.data_dir) and numbered_shards(candidate):
                self.snapshot = candidate
                self._configure()

    def save_settings(self):
        self.settings_path.write_text(json.dumps(self.settings, ensure_ascii=False, indent=2), encoding="utf-8")

    def _configure(self):
        config.DB_BACKEND = "sqlite3"
        config.DECRYPTED_DIR = str(self.snapshot.parents[1])
        config.CONTACTS_FILE = str(self.data_dir / "contacts.json")
        root = self.settings.get("source_root", "")
        config.WECHAT_DATA_GLOB = str(Path(root) / "*/db_storage")
        db.reset_caches()
        # Bind the selected account explicitly rather than picking by modification time.
        db._data_dir_cache = (db._data_dir_signature(), str(self.snapshot))
        contacts.invalidate_cache()

    def source_roots(self):
        candidates = [self.settings.get("source_root"), os.environ.get("WECHAT_DATA_ROOT"),
                      str(Path.home() / "Documents/xwechat_files")]
        return list(dict.fromkeys(str(Path(p)) for p in candidates if p and Path(p).is_dir()))

    def accounts(self, source_root):
        return sorted(p.parent.name for p in Path(source_root).glob("*/db_storage") if numbered_shards(p))

    def capture_key(self, source_root, account, log=lambda _: None):
        storage = Path(source_root) / account / "db_storage"
        shards = numbered_shards(storage)
        if not shards:
            raise ValueError("所选账号没有消息数据库。请先登录微信并核对数据目录。")
        extract_raw_key.KEY_FILE = str(self.key_path)
        original_finder = extract_raw_key.find_message_dbs
        extract_raw_key.find_message_dbs = lambda: [str(shards[0])]
        try:
            with contextlib.redirect_stdout(LogWriter(log)):
                result = extract_raw_key.main(["180"])
            if result:
                raise RuntimeError("没有捕获到有效密钥。请查看状态记录，并确认微信从桌面重新打开且已登录。")
        finally:
            extract_raw_key.find_message_dbs = original_finder

    def connect(self, source_root, account, log=lambda _: None, recapture=False, include_media=False):
        storage = Path(source_root).resolve() / account / "db_storage"
        if account not in self.accounts(source_root):
            raise ValueError("所选账号不属于这个数据目录。")
        sources = [storage / "contact/contact.db", *numbered_shards(storage)]
        if include_media:
            sources.extend(p for p in (storage / "message/message_resource.db", storage / "hardlink/hardlink.db")
                           if p.is_file())
        if not sources[0].is_file():
            raise FileNotFoundError("找不到联系人数据库，请核对所选账号。")
        if recapture:
            log("首次连接需要重启微信。请从桌面重新打开微信并完成登录。")
            self.capture_key(source_root, account, log)
        if not self.key_path.exists():
            raise RuntimeError("请先运行原项目的提取器获取本机密钥，再导出聊天。")
        key_text = self.key_path.read_text(encoding="ascii").strip()
        if not re.fullmatch(r"[0-9a-fA-F]{64}", key_text):
            raise ValueError("保存的本机密钥格式不正确，请重新提取。")
        raw_key = bytes.fromhex(key_text)
        snapshot = self.data_dir / "snapshots" / uuid.uuid4().hex / account / "db_storage"
        captured = []
        for index, source in enumerate(sources, 1):
            relative = source.relative_to(storage)
            destination = snapshot / relative
            log(f"读取本地数据 {index}/{len(sources)}：{relative.as_posix()}")
            if not decrypt_all.decrypt_db(raw_key, str(source), str(destination)):
                raise RuntimeError(f"{relative} 未通过解密校验。可能是微信更新、密钥不匹配或源文件仍在变化。")
            with read_connection(destination) as connection:
                status = [row[0] for row in connection.execute("PRAGMA quick_check")]
            if status != ["ok"]:
                raise RuntimeError(f"{relative} 解密后的结构校验失败，本轮数据不会用于导出。")
            captured.append({"file": relative.as_posix(), "bytes": destination.stat().st_size,
                             "sha256": sha256_file(destination)})
        self.storage, self.snapshot = storage, snapshot
        self.settings.update(source_root=str(Path(source_root).resolve()), account=account,
                             snapshot=str(snapshot.relative_to(self.data_dir)),
                             captured_at=datetime.now().isoformat(timespec="seconds"))
        (snapshot / "snapshot.json").write_text(json.dumps({"captured_at": self.settings["captured_at"],
                "account": account, "files": captured}, ensure_ascii=False, indent=2), encoding="utf-8")
        self.save_settings()
        self._configure()
        log("本地数据校验通过，可以选择聊天并导出。")
        return self.list_chats()

    def list_chats(self):
        if not self.snapshot:
            return []
        self._configure()
        mapping = db.get_name2id()
        table_names = set()
        for shard in numbered_shards(self.snapshot):
            with read_connection(shard) as connection:
                table_names.update(row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'"))
        return sorted([{"wxid": wxid, "table": table, "name": contacts.resolve_contact_name(wxid),
                        "kind": "群聊" if "@chatroom" in wxid else "单聊"}
                       for table, wxid in mapping.items() if table in table_names], key=lambda x: x["name"].casefold())

    def list_senders(self, chat):
        senders = set()
        for shard in numbered_shards(self.snapshot):
            with read_connection(shard) as connection:
                if not connection.execute("SELECT 1 FROM sqlite_master WHERE name=?", (chat["table"],)).fetchone():
                    continue
                senders.update(row[0] for row in connection.execute(
                    'SELECT DISTINCT CASE WHEN (CAST(m.local_type AS INTEGER) & 65535) IN (10000,10002) '
                    "THEN '__system__' ELSE COALESCE(n.user_name,'__unknown__') END "
                    f'FROM "{chat["table"]}" m LEFT JOIN Name2Id n ON n.rowid=m.real_sender_id'))
        def name(sender):
            if sender == "__system__":
                return "系统消息"
            if sender == "__unknown__":
                return "未知发送人"
            return contacts.resolve_contact_name(sender)
        return sorted([{"id": sender, "name": name(sender)} for sender in senders],
                      key=lambda item: (item["name"].casefold(), item["id"]))

    def _shard_rows(self, shard, table, since, until, descending, sender_ids=None):
        if not re.fullmatch(r"Msg_[0-9a-f]{32}", table):
            raise ValueError("消息表名称不合法。")
        with read_connection(shard) as connection:
            if not connection.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone():
                return
            order = "DESC" if descending else "ASC"
            query = (f'SELECT m.local_id,m.server_id,m.create_time,m.local_type,m.real_sender_id,'
                     f'm.message_content,n.user_name AS sender_wxid FROM "{table}" m '
                     'LEFT JOIN Name2Id n ON n.rowid=m.real_sender_id '
                     'WHERE m.create_time>=? AND m.create_time<?')
            parameters = [since, until]
            if sender_ids is not None:
                if not sender_ids:
                    return
                clauses = []
                people = sorted(set(sender_ids) - {"__system__", "__unknown__"})
                human = '(CAST(m.local_type AS INTEGER) & 65535) NOT IN (10000,10002)'
                if people:
                    clauses.append(f'({human} AND n.user_name IN ({",".join("?" for _ in people)}))')
                    parameters.extend(people)
                if "__system__" in sender_ids:
                    clauses.append('(CAST(m.local_type AS INTEGER) & 65535) IN (10000,10002)')
                if "__unknown__" in sender_ids:
                    clauses.append(f'({human} AND n.user_name IS NULL)')
                query += ' AND (' + ' OR '.join(clauses) + ')'
            query += f' ORDER BY m.create_time {order},m.local_id {order}'
            cursor = connection.execute(query, parameters)
            while batch := cursor.fetchmany(256):
                for row in batch:
                    item = dict(row)
                    item["_db"] = str(shard)
                    item["_shard"] = shard.name
                    yield item

    def iter_rows(self, chat, start="", end="", latest=None, sender_ids=None):
        if not self.snapshot:
            raise RuntimeError("请先连接微信。")
        since, until = time_window(start, end)
        descending = latest is not None
        streams = [self._shard_rows(p, chat["table"], since, until, descending, sender_ids)
                   for p in numbered_shards(self.snapshot)]
        def sort_key(row):
            return int(row["create_time"] or 0), int(row["local_id"] or 0)
        merged = heapq.merge(*streams, key=sort_key, reverse=descending)
        try:
            if latest is None:
                yield from merged
            else:
                if not 1 <= latest <= 2000:
                    raise ValueError("最近消息数量应在1到2000之间。")
                selected = []
                for row in merged:
                    selected.append(row)
                    if len(selected) >= latest:
                        break
                yield from reversed(selected)
        finally:
            for stream in streams:
                stream.close()

    def record(self, row, chat):
        raw = row["message_content"]
        original = message.decode_message_content(raw)
        local_type = message.normalize_type(row["local_type"])
        sender = row.get("sender_wxid")
        sender_name = contacts.resolve_contact_name(sender) if sender else "未知发送人"
        content = original
        extra = {}
        if local_type == "49":
            parsed = appmsg.parse_app_message(original, row["local_type"])
            if parsed["kind"] == "quote":
                identity = parsed.get("quoted_sender_id") or parsed.get("quoted_sender", "")
                if identity:
                    nickname = contacts.resolve_nickname(identity)
                    if nickname != identity:
                        parsed["quoted_sender"] = nickname
                parsed["summary"] = appmsg.format_app_message(parsed)
            content = f"[{parsed['label']}] {parsed['summary']}"
            extra["app"] = parsed
            if parsed["kind"] in ("chat_history", "forwarded"):
                from .forwarded import parse_forwarded
                from .forwarded_export import card_text
                bundle = parse_forwarded(original, row["local_type"], contacts.resolve_nickname)
                if bundle is not None:
                    extra["forwarded"] = bundle
                    content = card_text(bundle)
        elif local_type in ("10000", "10002"):
            event = message.parse_system_message(original, default_event="recall" if local_type == "10002" else "")
            content = event["text"]
            sender_name = "系统"
            extra["system"] = event
        elif local_type != "1":
            content = f"[{message.MSG_TYPES.get(local_type, '其他消息')}]"
            extra["media_status"] = "not_exported"
        timestamp = int(row["create_time"] or 0)
        account = self.settings.get("account", self.snapshot.parent.name)
        return {"record_id": f"{account}/{row['_shard']}/{row['local_id']}",
                "local_id": str(row["local_id"]), "server_id": str(row["server_id"] or "0"),
                "shard": row["_shard"], "conversation_id": chat["wxid"], "conversation": chat["name"],
                "timestamp": timestamp, "time": message.format_time(timestamp),
                "sender_id": sender, "sender_name": sender_name,
                "is_self": bool(sender and sender == db.get_my_wxid()), "type": local_type,
                "content": content, "original_content": original,
                "original_blob_base64": base64.b64encode(raw).decode() if isinstance(raw, bytes) else None,
                **extra}

    def preview(self, chat, count=20):
        return [self.record(row, chat) for row in self.iter_rows(chat, latest=count)]

    def export(self, chat, output, output_format="markdown", start="", end="", latest=None, log=lambda _: None,
               *, sender_ids=None, include_time=True, include_sender=True, include_title=True,
               forwarded_only=False):
        from .forwarded_export import ForwardedWriter, display_record, json_record

        if output_format not in {"txt", "markdown", "html", "jsonl", "pdf"}:
            raise ValueError("不支持该导出格式。")
        output = Path(output).resolve()
        if output.exists():
            raise FileExistsError("导出文件已存在，请选择新文件名。")
        manifest_path = output.with_suffix(output.suffix + ".manifest.json")
        if manifest_path.exists():
            raise FileExistsError("同名校验文件已存在，请选择新文件名。")
        forwarded_directory = output.with_name(output.name + "_转发记录")
        output.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        first = last = None
        seen_ids = set()
        label = "合并转发索引" if forwarded_only else "聊天记录"
        header = f"与 {chat['name']} 的{label}\n" if include_title else ""
        if include_time:
            header += f"导出时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        media_counts = {}
        def records():
            nonlocal count, first, last
            for row in self.iter_rows(chat, start, end, latest, sender_ids):
                record = self.record(row, chat)
                if forwarded_only and "forwarded" not in record:
                    continue
                if record["record_id"] in seen_ids:
                    raise RuntimeError("发现重复的本地消息标识，已停止导出以避免结果不准确。")
                seen_ids.add(record["record_id"])
                count += 1
                first = first or record["timestamp"]
                last = record["timestamp"]
                if count % 1000 == 0:
                    log(f"已导出 {count:,} 条")
                yield forwards.separate(record)
        with tempfile.TemporaryDirectory(dir=output.parent, prefix=".wechat-export-") as temporary:
            staging = Path(temporary)
            staged_output = staging / ("transcript" + output.suffix)
            forwards = ForwardedWriter(staging / "forwards", forwarded_directory.name, output_format,
                self, chat, include_time=include_time, include_sender=include_sender,
                include_title=include_title, log=log)
            if output_format == "pdf":
                from .pdf_export import write_pdf
                media_counts = write_pdf(staged_output, records(), self, chat, header,
                                         include_time=include_time, include_sender=include_sender, log=log)
                for entry in forwards.files:
                    for key, value in entry.get("media", {}).items():
                        media_counts[key] = media_counts.get(key, 0) + value
            else:
                with staged_output.open("x", encoding="utf-8", newline="") as destination:
                    if output_format == "jsonl":
                        for record in records():
                            destination.write(json.dumps(json_record(record, include_time, include_sender), ensure_ascii=False) + "\n")
                    else:
                        destination.writelines(transcript_render.render(header,
                            ((display_record(r, include_time, include_sender), None) for r in records()), output_format))
                    destination.flush()
                    os.fsync(destination.fileno())
            manifest = {"schema": "wechat-export-manifest-v1", "conversation": chat["name"],
                    "conversation_id": chat["wxid"], "count": count, "first_timestamp": first if include_time else None,
                    "last_timestamp": last if include_time else None, "format": output_format, "file": output.name,
                    "sha256": sha256_file(staged_output), "snapshot_captured_at": self.settings.get("captured_at"),
                    "start": start or None, "end": end or None, "latest": latest,
                    "source_shards": [p.name for p in numbered_shards(self.snapshot)],
                    "sender_filter_applied": sender_ids is not None, "include_time": include_time,
                    "include_sender": include_sender, "include_title": include_title,
                    "forwarded_only": forwarded_only, "forwarded_count": len(forwards.files),
                    "forwarded_directory": str(forwarded_directory) if forwards.files else None,
                    "forwarded_missing": forwards.missing, "forwarded_files": forwards.files,
                    "media": media_counts or "Image, voice and video are represented by explicit placeholders in this version."}
            staged_manifest = staging / "manifest.json"
            staged_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            moves = [(staged_output, output), (staged_manifest, manifest_path)]
            if forwards.files:
                moves.insert(0, (forwards.folder, forwarded_directory))
            if any(target.exists() for _, target in moves):
                raise FileExistsError("导出文件或转发记录目录已存在，请选择新文件名。")
            published = []
            try:
                for source, target in moves:
                    # Windows rename refuses to overwrite a file created during export.
                    os.rename(source, target)
                    published.append((source, target))
            except BaseException:
                for source, target in reversed(published):
                    os.rename(target, source)
                raise
        log(f"导出完成：{count:,} 条，校验文件已生成。")
        return {"path": str(output), "manifest": str(manifest_path), **manifest}

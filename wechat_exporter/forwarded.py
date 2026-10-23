"""Parse locally embedded WeChat merged-forward records without fetching resources."""
from __future__ import annotations

from datetime import datetime
import html
import re
import xml.etree.ElementTree as ET


MAX_XML_CHARS = 8 * 1024 * 1024
MAX_ITEMS = 10000
MAX_DEPTH = 5
MAX_NODES = 100000
MAX_XML_DEPTH = 128
_MD5 = re.compile(r"[0-9a-fA-F]{32}\Z")


def _tag(node):
    return str(node.tag).rsplit("}", 1)[-1].lower()


def _child(node, *names):
    if node is None:
        return None
    return next((n for n in node if _tag(n) in names), None)


def _text(node, *names, strip=True):
    target = _child(node, *names) if names else node
    value = "" if target is None else "".join(target.itertext())
    return value.strip() if strip else value


def _integer(value):
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _result(title="聊天记录", status="available", notice=""):
    return {"title": title or "聊天记录", "preview": [], "records": [],
            "status": status, "notice": notice, "declared_count": None}


def _preview(value):
    return [line[:160] for line in value.splitlines() if line.strip()][:3]


class _LimitError(ValueError):
    pass


def _xml(value):
    """Decode only XML envelopes; never repeatedly unescape message text."""
    value = value.strip("\x00\r\n \t")
    if len(value) > MAX_XML_CHARS:
        raise _LimitError("内嵌 XML 超过解析大小限制。")
    for _ in range(3):
        if value.startswith("<![CDATA[") and value.endswith("]]>"):
            value = value[9:-3].strip()
        elif value.startswith(("&lt;", "&#60;", "&#x3c;")):
            value = html.unescape(value).strip()
        else:
            break
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", value, re.I):
        raise _LimitError("内嵌 XML 含不支持的 DTD 或实体声明。")
    # The database may prefix an app message with a chatroom sender line.
    start = re.search(r"<(?:\?xml\b|msg\b|appmsg\b|recordinfo\b|recorditem\b)", value, re.I)
    if start and start.start():
        value = value[start.start():]
    root = ET.fromstring(value)
    pending, count = [(root, 0)], 0
    while pending:
        node, depth = pending.pop()
        count += 1
        if count > MAX_NODES or depth > MAX_XML_DEPTH:
            raise _LimitError("内嵌 XML 节点数或结构深度超过解析限制。")
        pending.extend((child, depth + 1) for child in node)
    return root


def _payload(node):
    """A recorditem can contain actual XML children or serialized XML text."""
    if _tag(node) == "recordinfo":
        return node
    direct = _child(node, "recordinfo")
    if direct is not None:
        return direct
    if node.text and node.text.strip():
        return _xml(node.text)
    return None


def _sender(item, resolve_name):
    source = _child(item, "sourceinfo", "dataitemsource")
    sender_id = (_text(source, "realchatname", "fromusr", "sourceusername")
                 or _text(item, "realchatname", "fromusr", "sourceusername")) or None
    name = (_text(item, "sourcename", "displayname", "nickname")
            or _text(source, "sourcename", "displayname", "nickname"))
    if not name or name == sender_id or name.startswith("wxid_"):
        candidate = sender_id or name
        if candidate and resolve_name:
            resolved = resolve_name(candidate)
            if resolved:
                name = str(resolved)
        name = name or sender_id or "未知发送人"
    return sender_id, name


def _time(item):
    source = _child(item, "sourceinfo", "dataitemsource")
    raw = (_text(item, "srcmsgcreatetime", "createtime", "timestamp")
           or _text(source, "srcmsgcreatetime", "createtime", "timestamp"))
    shown = _text(item, "sourcetime") or _text(source, "sourcetime")
    timestamp = _integer(raw) if raw else _integer(shown)
    if timestamp is not None and timestamp > 0:
        if timestamp > 10**12:
            timestamp //= 1000
        try:
            shown = datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, OSError, OverflowError):
            return None, shown if _integer(shown) is None else ""
        return timestamp, shown
    return None, shown if _integer(shown) is None else ""


def _media_ids(item):
    result = []
    pending = [item]
    while pending:
        node = pending.pop()
        # An outer record must not absorb all nested records' resource IDs.
        if node is not item and _tag(node) == "dataitem":
            continue
        pending.extend(reversed(list(node)))
        values = list(node.attrib.values())
        if _tag(node) in ("dataid", "fullmd5", "thumbmd5", "thumbfullmd5", "md5", "filemd5", "cdnthumbmd5"):
            values.append(node.text or "")
        for value in values:
            if _MD5.fullmatch(value.strip()):
                result.append(value.strip().lower())
    return list(dict.fromkeys(result))


def _app(item, kind):
    # Import lazily: backend sets the pinned upstream path before calling us.
    from .backend import appmsg

    embedded = _child(item, "appmsg")
    if embedded is not None:
        return appmsg.parse_app_message(ET.tostring(embedded, encoding="unicode"))
    web = _child(item, "weburlitem")
    root = ET.Element("appmsg")
    values = {"type": str(kind), "title": _text(item, "datatitle", "title") or _text(web, "title"),
              "des": _text(web, "desc", strip=False) or _text(item, "datadesc", "desc", strip=False),
              "url": _text(item, "dataurl", "url", "link") or _text(web, "link", "url"), "fileext": _text(item, "datafmt"),
              "totallen": _text(item, "datasize", "totallen"),
              "md5": _text(item, "fullmd5", "md5")}
    for key, value in values.items():
        ET.SubElement(root, key).text = value
    return appmsg.parse_app_message(ET.tostring(root, encoding="unicode"))


def _record(item, index, depth, resolve_name, budget):
    datatype = _integer(item.get("datatype") or _text(item, "datatype"))
    sender_id, sender_name = _sender(item, resolve_name)
    timestamp, shown = _time(item)
    original = ET.tostring(item, encoding="unicode")
    content = _text(item, "datadesc", "content", "desc", strip=False)
    title = _text(item, "datatitle", "title")
    type_ = {1: "1", 2: "3", 3: "34", 4: "43", 5: "49", 8: "49", 17: "49"}.get(datatype, "49")
    record = {"type": type_, "content": content, "original_content": original,
              "sender_id": sender_id, "sender_name": sender_name,
              "timestamp": timestamp, "time": shown,
              "local_id": item.get("dataid") or _text(item, "dataid") or str(index),
              "server_id": _text(item, "fromnewmsgid", "msgid", "svrid") or "0",
              "record_id": str(index), "is_self": False, "_forwarded_item": True,
              "media_ids": _media_ids(item)}
    if datatype == 17:
        nested = _child(item, "recorditem", "recordxml", "recordinfo")
        if nested is None:
            record["forwarded"] = _result(title, "missing", "该嵌套记录未包含内嵌明细。")
        else:
            record["forwarded"] = _parse_node(nested, depth + 1, resolve_name, budget, title)
        record["content"] = record["forwarded"]["title"]
    elif datatype in (5, 8):
        record["app"] = _app(item, 5 if datatype == 5 else 6)
        record["content"] = record["app"]["summary"]
    elif _child(item, "emoji", "emoticon") is not None:
        record["type"], record["content"] = "47", content or "[表情]"
    elif datatype in (2, 3, 4):
        record["content"] = content or {2: "[图片]", 3: "[语音]", 4: "[视频]"}[datatype]
    elif datatype != 1:
        record["content"] = f"[未识别转发条目 datatype={datatype}]" + (f" {title or content}" if title or content else "")
    return record


def _parse_node(node, depth, resolve_name, budget, title="", description=""):
    result = _result(title)
    if depth > MAX_DEPTH:
        result.update(status="limited", notice="嵌套聊天记录超过解析深度限制，明细未继续展开。")
        return result
    try:
        root = _payload(node)
    except _LimitError as error:
        result.update(status="limited", notice=str(error))
        return result
    except ET.ParseError:
        result.update(status="malformed", notice="内嵌聊天记录 XML 损坏，无法解析明细。")
        return result
    if root is None:
        result.update(status="missing", notice="消息中未包含内嵌聊天记录明细。")
        return result
    if _tag(root) != "recordinfo":
        candidate = _child(root, "recordinfo")
        if candidate is None:
            result.update(status="malformed", notice="内嵌 XML 缺少 recordinfo 结构。")
            return result
        root = candidate
    result["title"] = title or _text(root, "title") or "聊天记录"
    description = description or _text(root, "desc", "des")
    result["preview"] = _preview(description)
    datalist = _child(root, "datalist")
    if datalist is None:
        result.update(status="missing", notice="内嵌记录未包含 datalist 明细。")
        return result
    result["declared_count"] = _integer(datalist.get("count") or datalist.get("totalcount") or _text(root, "count"))
    items = [n for n in datalist if _tag(n) == "dataitem"]
    for index, item in enumerate(items, 1):
        if budget[0] >= MAX_ITEMS:
            result.update(status="limited", notice="内嵌条目总数超过解析限制，后续明细未展开。")
            break
        budget[0] += 1
        result["records"].append(_record(item, index, depth, resolve_name, budget))
    if result["status"] == "available":
        if not items:
            result.update(status="missing", notice="内嵌聊天记录为空，未包含可导出的条目。")
        elif result["declared_count"] is not None and len(items) < result["declared_count"]:
            result.update(status="missing", notice=f"明细不完整：声明 {result['declared_count']} 条，内嵌仅有 {len(items)} 条。")
        elif any(r.get("forwarded", {}).get("status") == "limited" for r in result["records"]):
            result.update(status="limited", notice="部分嵌套记录达到解析限制，请查看对应条目的说明。")
    if not result["preview"]:
        result["preview"] = _preview("\n".join(f"{r['sender_name']}: {r['content'].splitlines()[0] if r['content'] else '[空消息]'}"
                                                for r in result["records"][:3]))
    return result


def parse_forwarded(content: str, raw_type=0, resolve_name=None) -> dict | None:
    """Return a normalized embedded merged-forward, or None for other messages.

    No network, filesystem, account lookup, or fabricated sender/time data is used.
    ``resolve_name`` is an optional caller-supplied local contact resolver.
    """
    content = content or ""
    subtype = ((_integer(raw_type) or 0) >> 32) & 0xFFFFFFFF
    hint = subtype in (19, 40) or bool(re.search(r"<(?:recorditem|recordinfo)\b|<type>\s*(?:19|40)\s*</type>", content, re.I))
    try:
        root = _xml(content)
    except _LimitError as error:
        return _result(status="limited", notice=str(error)) if hint else None
    except ET.ParseError:
        return _result(status="malformed", notice="消息 XML 损坏，无法解析内嵌聊天记录。") if hint else None
    if _tag(root) in ("recordinfo", "recorditem"):
        return _parse_node(root, 0, resolve_name, [0])
    app = root if _tag(root) == "appmsg" else _child(root, "appmsg")
    if app is None or (_integer(_text(app, "type")) or subtype) not in (19, 40):
        return None
    title, description = _text(app, "title"), _text(app, "des", "description")
    recorditem = _child(app, "recorditem", "recordinfo")
    if recorditem is None:
        result = _result(title, "missing", "消息中未包含内嵌聊天记录明细。")
        result["preview"] = _preview(description)
        return result
    return _parse_node(recorditem, 0, resolve_name, [0], title, description)

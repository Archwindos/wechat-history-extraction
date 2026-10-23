"""End-to-end merged-forward export using synthetic temporary databases only."""
from datetime import datetime
import hashlib
import html
import io
import json
from pathlib import Path
import sqlite3

import pytest

from test_desktop_backend import engine
from wechat_exporter.backend import numbered_shards, sha256_file


STAMP = int(datetime(2026, 1, 1, 12, 0).timestamp())


def _item(text, index=1, datatype=1, extra=""):
    return (f'<dataitem datatype="{datatype}" dataid="synthetic-{index}">'
            f'<sourcename>内层成员{index}</sourcename><srcMsgCreateTime>{STAMP - 3600 + index}</srcMsgCreateTime>'
            '<dataitemsource><realchatname>wxid_inner</realchatname></dataitemsource>'
            f'<datadesc>{html.escape(text)}</datadesc>{extra}</dataitem>')


def _record(items):
    return f'<recordinfo><datalist count="{len(items)}">{"".join(items)}</datalist></recordinfo>'


def _card(items=None, *, title="合成转发", description="", payload=None):
    if payload is None:
        payload = _record(items or [])
    return ('<msg><appmsg><type>19</type>'
            f'<title>{html.escape(title)}</title><des>{html.escape(description)}</des>'
            f'<recorditem><![CDATA[{payload}]]></recorditem></appmsg></msg>')


def _feed(engine, messages):
    """Replace this test's fixture messages, retaining the synthetic contacts."""
    chat = engine.list_chats()[0]
    shards = numbered_shards(engine.snapshot)
    for shard in shards:
        with sqlite3.connect(shard) as connection:
            connection.execute(f'DELETE FROM "{chat["table"]}"')
    with sqlite3.connect(shards[0]) as connection:
        connection.executemany(f'INSERT INTO "{chat["table"]}" VALUES (?,?,?,?,?,?)',
            [(100 + index, 10000 + index, stamp, type_, sender, content.encode())
             for index, (stamp, type_, sender, content) in enumerate(messages)])
    return chat


def _jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()]


def _details(result):
    return [Path(result["path"]).parent / entry["file"] for entry in result["forwarded_files"]]


def _assert_hashes(result):
    manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
    assert manifest["sha256"] == sha256_file(result["path"])
    for entry in manifest["forwarded_files"]:
        assert sha256_file(Path(result["path"]).parent / entry["file"]) == entry["sha256"]


def test_txt_parent_is_a_bounded_card_and_detail_preserves_every_message(engine, tmp_path):
    messages = [f"合成正文第{i}条" for i in range(1, 7)]
    chat = _feed(engine, [(STAMP, 49, 2, _card([_item(text, i) for i, text in enumerate(messages, 1)]))])
    result = engine.export(chat, tmp_path / "parent.txt", "txt")
    parent = Path(result["path"]).read_text(encoding="utf-8")
    detail, = _details(result)
    assert result["count"] == result["forwarded_count"] == 1
    assert detail.parent.parent == Path(result["path"]).parent
    assert all(text in parent for text in messages[:3])
    assert all(text not in parent for text in messages[3:])
    assert "详情：" + result["forwarded_files"][0]["file"] in parent
    assert all(text in detail.read_text(encoding="utf-8") for text in messages)
    assert not Path(result["forwarded_files"][0]["file"]).is_absolute()
    _assert_hashes(result)


def test_nested_forward_has_its_own_detail_and_relative_sibling_reference(engine, tmp_path):
    nested = _record([_item("只属于最内层的正文", 8)])
    outer = _item("外层文本", 1)
    nested_item = _item("", 2, datatype=17, extra='<datatitle>嵌套标题</datatitle><recordxml>' + nested + '</recordxml>')
    chat = _feed(engine, [(STAMP, 49, 2, _card([outer, nested_item], description="外层短摘要"))])
    result = engine.export(chat, tmp_path / "nested.txt", "txt")
    assert result["forwarded_count"] == 2
    parent_detail, nested_detail = _details(result)
    parent_text = parent_detail.read_text(encoding="utf-8")
    # The nested card may preview the body, but must not repeat its full transcript metadata.
    assert "详情：" + nested_detail.name in parent_text
    assert "详情：" + result["forwarded_files"][1]["file"] not in parent_text
    assert "[内层成员8]" not in parent_text
    assert "[内层成员8]" in nested_detail.read_text(encoding="utf-8")
    assert "只属于最内层的正文" not in Path(result["path"]).read_text(encoding="utf-8")
    _assert_hashes(result)


def test_jsonl_index_has_no_raw_xml_or_base64_and_detail_has_normalized_children(engine, tmp_path):
    texts = [f"正文-{i}" for i in range(5)]
    chat = _feed(engine, [(STAMP, 49, 2, _card([_item(text, i) for i, text in enumerate(texts)]))])
    result = engine.export(chat, tmp_path / "parent.jsonl", "jsonl")
    row, = _jsonl(result["path"])
    assert "original_content" not in row and "original_blob_base64" not in row
    assert "records" not in row["forwarded"]
    assert "<recordinfo" not in json.dumps(row, ensure_ascii=False)
    assert texts[4] not in json.dumps(row, ensure_ascii=False)
    children = _jsonl(_details(result)[0])
    assert [row["content"] for row in children] == texts
    assert children[0]["sender_name"] == "内层成员0"
    assert children[0]["timestamp"] == STAMP - 3600
    assert all("original_blob_base64" not in row for row in children)
    _assert_hashes(result)


def test_forwarded_only_keeps_outer_latest_time_and_sender_selection(engine, tmp_path):
    card = _card([_item("内层发言人不参与外层筛选")])
    chat = _feed(engine, [(STAMP - 60, 49, 2, card), (STAMP, 1, 2, "普通消息"),
                          (STAMP + 1, 49, 1, card), (STAMP + 2, 49, 2, card),
                          (STAMP + 3, 1, 2, "最新普通消息")])
    # Sender filtering precedes latest; forwarded-only then operates on that selected range.
    result = engine.export(chat, tmp_path / "selected.jsonl", "jsonl", latest=2,
        sender_ids={"wxid_peer"}, forwarded_only=True,
        start="2026-01-01 12:00", end="2026-01-01 12:00")
    rows = _jsonl(result["path"])
    assert result["count"] == 1
    assert rows[0]["timestamp"] == STAMP + 2
    assert rows[0]["sender_id"] == "wxid_peer"
    assert result["latest"] == 2 and result["sender_filter_applied"] is True
    assert result["forwarded_only"] is True
    assert result["first_timestamp"] == result["last_timestamp"] == STAMP + 2
    assert _jsonl(_details(result)[0])[0]["sender_id"] == "wxid_inner"
    empty = engine.export(chat, tmp_path / "latest-ordinary.jsonl", "jsonl", latest=1, forwarded_only=True)
    assert empty["count"] == 0 and _jsonl(empty["path"]) == []
    assert empty["forwarded_directory"] is None


@pytest.mark.parametrize("format_,suffix", [("txt", ".txt"), ("jsonl", ".jsonl")])
def test_disabled_time_and_sender_apply_to_all_inner_metadata(engine, tmp_path, format_, suffix):
    chat = _feed(engine, [(STAMP, 49, 2, _card([_item("隐藏元数据正文")]))])
    result = engine.export(chat, tmp_path / ("metadata" + suffix), format_,
                           include_time=False, include_sender=False, include_title=False)
    detail = _details(result)[0]
    if format_ == "jsonl":
        for row in _jsonl(detail) + _jsonl(result["path"]):
            assert not {"time", "timestamp", "sender_id", "sender_name", "is_self"} & row.keys()
            assert "内层成员" not in row["content"]
    else:
        text = detail.read_text(encoding="utf-8") + Path(result["path"]).read_text(encoding="utf-8")
        assert "内层成员" not in text and "wxid_inner" not in text and "2026-" not in text
        assert "隐藏元数据正文" in text


@pytest.mark.parametrize("payload,status", [("", "missing"), ("<recordinfo>", "malformed")])
def test_missing_and_malformed_details_leave_notices_without_fabricated_messages(engine, tmp_path, payload, status):
    chat = _feed(engine, [(STAMP, 49, 2, _card(payload=payload))])
    result = engine.export(chat, tmp_path / (status + ".jsonl"), "jsonl", forwarded_only=True)
    row, = _jsonl(result["path"])
    assert row["forwarded"]["status"] == status
    assert row["forwarded"]["count"] == 0 and row["forwarded"]["notice"]
    detail_row, = _jsonl(_details(result)[0])
    assert detail_row["type"] == "forwarded_notice"
    assert "sender_name" not in detail_row and "timestamp" not in detail_row
    assert result["forwarded_missing"] == 1
    _assert_hashes(result)


def test_existing_detail_directory_is_never_overwritten(engine, tmp_path):
    chat = _feed(engine, [(STAMP, 49, 2, _card([_item("正文")]))])
    output = tmp_path / "existing.txt"
    folder = output.with_name(output.name + "_转发记录")
    folder.mkdir()
    marker = folder / "keep.txt"
    marker.write_text("existing bytes", encoding="utf-8")
    with pytest.raises(FileExistsError):
        engine.export(chat, output, "txt")
    assert marker.read_text(encoding="utf-8") == "existing bytes"
    assert list(folder.iterdir()) == [marker]
    assert not output.exists() and not output.with_suffix(".txt.manifest.json").exists()
    assert not list(tmp_path.glob(".wechat-export-*"))


@pytest.mark.parametrize("failure", ["detail_write", "pdf", "publish_manifest"])
def test_failed_export_removes_all_partial_deliverables(engine, tmp_path, monkeypatch, failure):
    from wechat_exporter import backend, pdf_export

    chat = _feed(engine, [(STAMP, 49, 2, _card([_item("正文")]))])
    format_ = "pdf" if failure == "pdf" else "txt"
    output = tmp_path / ("failure." + format_)
    if failure == "detail_write":
        real_open = Path.open

        def failing_open(path, *args, **kwargs):
            if path.parent.name == "forwards":
                raise OSError("synthetic detail write failure")
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr(Path, "open", failing_open)
    elif failure == "pdf":
        def failing_pdf(path, records, *args, **kwargs):
            # Force lazy generation so the detailed PDF fails after records were parsed.
            list(records)
            Path(path).write_bytes(b"partial synthetic PDF")
            raise RuntimeError("synthetic PDF failure")

        monkeypatch.setattr(pdf_export, "write_pdf", failing_pdf)
    else:
        real_rename = backend.os.rename

        def failing_rename(source, target):
            if Path(target).name.endswith(".manifest.json"):
                raise OSError("synthetic publication failure")
            return real_rename(source, target)

        monkeypatch.setattr(backend.os, "rename", failing_rename)
    with pytest.raises((OSError, RuntimeError), match="synthetic"):
        engine.export(chat, output, format_)
    assert not output.exists()
    assert not output.with_suffix(output.suffix + ".manifest.json").exists()
    assert not output.with_name(output.name + "_转发记录").exists()
    assert not list(tmp_path.glob(".wechat-export-*"))


def test_real_pdf_forwarded_detail_contains_text_and_saved_local_image(engine, tmp_path):
    """Run real Pandoc/XeLaTeX; the only image is generated inside this fixture."""
    from PIL import Image
    import pymupdf
    from wechat_exporter.pdf_export import pdf_tools

    try:
        pdf_tools()
    except RuntimeError as error:
        pytest.skip(str(error))
    digest = "a" * 32
    source = tmp_path / "source"
    engine.settings["source_root"] = str(source)
    chat = engine.list_chats()[0]
    folder = (source / engine.settings["account"] / "msg/attach"
              / hashlib.md5(chat["wxid"].encode()).hexdigest() / "2026-01/Img")
    folder.mkdir(parents=True)
    Image.new("RGB", (120, 80), "#397b91").save(folder / (digest + "_t.dat"), "PNG")
    image = _item("", 2, datatype=2, extra=f'<fullmd5>{digest}</fullmd5>')
    chat = _feed(engine, [(STAMP, 49, 2, _card([_item("合成详单文字证据"), image], description="短摘要"))])
    result = engine.export(chat, tmp_path / "real-detail.pdf", "pdf", forwarded_only=True)
    detail, = _details(result)
    with pymupdf.open(detail) as document:
        assert "合成详单文字证据" in "".join(page.get_text() for page in document)
        assert sum(len(page.get_images()) for page in document) == 1
        assert document.page_count == 1
        document[0].get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5)).save(tmp_path / "detail-preview.png")
    assert result["media"]["images"] == 1
    assert result["media"]["missing_images"] == 0
    _assert_hashes(result)


def test_forwarded_image_uses_hardlink_md5_when_filename_and_original_chat_differ(engine, tmp_path):
    from PIL import Image
    from wechat_exporter.media import LocalMedia

    digest = "b" * 32
    card = _card([_item("", datatype=2, extra=f'<fullmd5>{digest}</fullmd5>')])
    chat = _feed(engine, [(STAMP, 49, 2, card)])
    source = tmp_path / "source"
    engine.settings["source_root"] = str(source)
    original_chat_directory = hashlib.md5(b"different_original_chat").hexdigest()
    assert original_chat_directory != hashlib.md5(chat["wxid"].encode()).hexdigest()
    image_path = (source / engine.settings["account"] / "msg/attach" / original_chat_directory
                  / "2025-12/Img/opaque-registered-image.dat")
    image_path.parent.mkdir(parents=True)
    Image.new("RGB", (87, 63), "#6b953c").save(image_path, "PNG")
    index = engine.snapshot / "hardlink/hardlink.db"
    index.parent.mkdir()
    with sqlite3.connect(index) as connection:
        connection.execute('CREATE TABLE dir2id(username TEXT)')
        connection.executemany('INSERT INTO dir2id VALUES (?)', [(original_chat_directory,), ("2025-12",)])
        connection.execute('CREATE TABLE image_hardlink_info_v4(file_name TEXT,dir1 INTEGER,dir2 INTEGER,md5 TEXT)')
        connection.execute('INSERT INTO image_hardlink_info_v4 VALUES (?,1,2,?)', (image_path.name, digest))
    child = engine.preview(chat, 1)[0]["forwarded"]["records"][0]
    media = LocalMedia(engine, chat)
    try:
        assert media._resource_ids(child) == [digest]
        data = media.image(child)
        assert data is not None
        with Image.open(io.BytesIO(data)) as decoded:
            assert decoded.size == (87, 63)
    finally:
        media.close()


def test_forwarded_thumbnail_with_missing_timestamp_finds_saved_cache(engine, tmp_path):
    from PIL import Image
    from wechat_exporter.media import LocalMedia

    digest = "c" * 32
    inner = _item("", datatype=2, extra=f'<thumbfullmd5>{digest}</thumbfullmd5>')
    inner = inner.replace(f'<srcMsgCreateTime>{STAMP - 3600 + 1}</srcMsgCreateTime>', "")
    chat = _feed(engine, [(STAMP, 49, 2, _card([inner]))])
    source = tmp_path / "source"
    engine.settings["source_root"] = str(source)
    thumb = (source / engine.settings["account"] / "cache/2025-12/Message"
             / hashlib.md5(chat["wxid"].encode()).hexdigest() / "Thumb" / (digest + "_t.dat"))
    thumb.parent.mkdir(parents=True)
    Image.new("RGB", (51, 37), "#95563c").save(thumb, "PNG")
    child = engine.preview(chat, 1)[0]["forwarded"]["records"][0]
    assert child["timestamp"] is None and child["time"] == ""
    media = LocalMedia(engine, chat)
    try:
        data = media.thumbnail(child)
        assert data is not None
        with Image.open(io.BytesIO(data)) as decoded:
            assert decoded.size == (51, 37)
    finally:
        media.close()

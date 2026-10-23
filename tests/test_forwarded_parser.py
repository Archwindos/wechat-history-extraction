"""Synthetic fixtures only: no account databases or private messages."""
import html

import pytest

from wechat_exporter.backend import Engine  # Sets the pinned appmsg import path.
from wechat_exporter import forwarded
from wechat_exporter.forwarded import parse_forwarded


def item(text="正文", datatype="1", extra="", dataid="item-1"):
    return (f'<dataitem datatype="{datatype}" dataid="{dataid}">'
            '<sourcename>中文昵称</sourcename><sourceinfo><realchatname>wxid_test</realchatname></sourceinfo>'
            f'<datadesc>{html.escape(text)}</datadesc>{extra}</dataitem>')


def record(items, count=None, desc=""):
    count = count if count is not None else len(items)
    return f'<recordinfo><desc>{html.escape(desc)}</desc><datalist count="{count}">{"".join(items)}</datalist></recordinfo>'


def card(payload, mode="cdata", desc="", type_="19"):
    encoded = {"cdata": f"<![CDATA[{payload}]]>", "escaped": html.escape(payload), "direct": payload}[mode]
    return f'<msg><appmsg><type>{type_}</type><title>群聊记录</title><des>{html.escape(desc)}</des><recorditem>{encoded}</recorditem></appmsg></msg>'


@pytest.mark.parametrize("mode", ["cdata", "escaped", "direct"])
def test_envelopes_preserve_nickname_text_and_literal_entities(mode):
    text = "  第一行\n    第二行，字面量 &lt;tag&gt; &amp;\n"
    parsed = parse_forwarded(card(record([item(text)]), mode))
    assert parsed["status"] == "available"
    row = parsed["records"][0]
    assert row["content"] == text
    assert row["sender_name"] == "中文昵称"
    assert row["sender_id"] == "wxid_test"
    assert row["time"] == "" and row["timestamp"] is None
    assert row["is_self"] is False and row["_forwarded_item"] is True
    assert row["type"] == "1" and row["local_id"] == "item-1"
    assert row["record_id"] == "1" and row["server_id"] == "0"


def test_outer_preview_is_bounded_and_does_not_include_full_detail():
    description = "摘要第一行\n" + "长" * 300 + "\n第三行\n不应显示"
    parsed = parse_forwarded(card(record([item("秘密完整正文")]), desc=description))
    assert parsed["preview"] == ["摘要第一行", "长" * 160, "第三行"]
    assert "秘密完整正文" not in "".join(parsed["preview"])


def test_record_desc_and_fallback_preview():
    parsed = parse_forwarded(card(record([item("秘密正文")], desc="内部摘要")))
    assert parsed["preview"] == ["内部摘要"]
    parsed = parse_forwarded(card(record([item("多" * 300)] * 5)))
    assert len(parsed["preview"]) == 3
    assert all(len(line) <= 160 for line in parsed["preview"])
    assert parsed["declared_count"] == 5


def test_non_forwarded_and_raw_subtype():
    assert parse_forwarded("hello") is None
    assert parse_forwarded('<msg><appmsg><type>5</type><title>链接</title></appmsg></msg>') is None
    parsed = parse_forwarded('<msg><appmsg><title>聊天</title></appmsg></msg>', (19 << 32) | 49)
    assert parsed["status"] == "missing" and parsed["notice"]
    assert parse_forwarded(card(record([item()]), type_="40"))["status"] == "available"


@pytest.mark.parametrize("payload,status", [
    ("", "missing"), ("<recordinfo>", "malformed"),
    (record([]), "missing"), (record([item()], count=3), "missing"),
    ("<recordinfo/>", "missing"), ("<bad/>", "malformed"),
])
def test_incomplete_and_malformed_are_explicit(payload, status):
    parsed = parse_forwarded(card(payload))
    assert parsed["status"] == status
    assert parsed["notice"]


def test_malformed_outer_and_missing_recorditem():
    assert parse_forwarded('<msg><appmsg><type>19</type>')["status"] == "malformed"
    assert parse_forwarded('<appmsg><type>19</type></appmsg>')["status"] == "missing"


def test_sender_resolution_and_existing_nickname_priority():
    payload = record([item().replace("中文昵称", "wxid_test")])
    parsed = parse_forwarded(card(payload), resolve_name=lambda value: "本地联系人")
    assert parsed["records"][0]["sender_name"] == "本地联系人"
    parsed = parse_forwarded(card(record([item()])), resolve_name=lambda value: "不应替换")
    assert parsed["records"][0]["sender_name"] == "中文昵称"


def test_media_and_app_records_preserve_evidence():
    md5 = "abcdef0123456789" * 2
    items = [item(datatype=kind, dataid=md5, extra=f'<fullmd5>{md5}</fullmd5><thumbmd5>invalid</thumbmd5>')
             for kind in ("2", "3", "4")]
    items += [item(datatype="5", extra='<datatitle>原链接</datatitle><dataurl>https://example.com</dataurl>'),
              item(datatype="8", extra='<datatitle>文件.pdf</datatitle><datafmt>pdf</datafmt><datasize>123</datasize>'),
              item(datatype="19", extra='<emoji md5="' + md5 + '"/>'), item(datatype="999")]
    rows = parse_forwarded(card(record(items)))["records"]
    assert [r["type"] for r in rows] == ["3", "34", "43", "49", "49", "47", "49"]
    assert all(r["media_ids"] == [md5] for r in rows[:3])
    assert '<fullmd5>' in rows[0]["original_content"]
    assert rows[3]["app"]["kind"] == "link"
    assert rows[3]["app"]["url"] == "https://example.com"
    assert rows[4]["app"]["kind"] == "file" and rows[4]["app"]["file_ext"] == "pdf"
    assert "999" in rows[6]["content"]


def test_inner_timestamp_and_server_id():
    parsed = parse_forwarded(card(record([item(extra='<srcMsgCreateTime>1700000000</srcMsgCreateTime><fromnewmsgid>1234567</fromnewmsgid>')])))
    row = parsed["records"][0]
    assert row["timestamp"] == 1700000000 and row["time"]
    assert row["server_id"] == "1234567"
    row = parse_forwarded(card(record([item(extra='<sourcetime>昨天 12:34</sourcetime>')])))["records"][0]
    assert row["time"] == "昨天 12:34" and row["timestamp"] is None


@pytest.mark.parametrize("mode", ["direct", "escaped"])
def test_nested_forwarded(mode):
    inner = record([item("嵌套正文")])
    if mode == "escaped":
        inner = html.escape(inner)
    parent = item(datatype="17", extra=f'<recorditem>{inner}</recorditem>')
    parsed = parse_forwarded(card(record([parent]), mode="direct"))
    nested = parsed["records"][0]["forwarded"]
    assert nested["records"][0]["content"] == "嵌套正文"
    assert nested["status"] == "available"


def test_recordxml_real_field_shape_is_scoped_to_current_datalist():
    """Field structure observed locally; all fixture values here are invented."""
    nested = record([item(f"嵌套合成条目 {i}") for i in range(8)])
    parent = item(datatype="17", extra=f'<recordxml>{nested}</recordxml><srcMsgCreateTime>1700000000</srcMsgCreateTime><sourcetime>合成时间标签</sourcetime>')
    image = item(datatype="2", extra='<group><filemd5>' + 'a' * 32 + '</filemd5></group><thumbfullmd5>' + 'b' * 32 + '</thumbfullmd5>')
    link = item(datatype="5", extra='<weburlitem><title>合成网站</title><link>https://example.org</link><desc>合成介绍</desc></weburlitem>')
    parsed = parse_forwarded(card(record([parent, image, link, item(), item()])))
    assert len(parsed["records"]) == 5
    assert len(parsed["records"][0]["forwarded"]["records"]) == 8
    assert parsed["records"][0]["timestamp"] == 1700000000
    assert parsed["records"][0]["time"] != "合成时间标签"
    assert parsed["records"][1]["media_ids"] == ['a' * 32, 'b' * 32]
    assert parsed["records"][2]["app"]["url"] == "https://example.org"
    assert parsed["records"][2]["app"]["title"] == "合成网站"


def test_nested_resource_ids_do_not_leak_to_parent():
    inner = record([item(datatype="2", dataid='c' * 32)])
    parent = item(datatype="17", extra=f'<recordxml>{inner}</recordxml>')
    row = parse_forwarded(card(record([parent]), mode="direct"))["records"][0]
    assert row["media_ids"] == []
    assert row["forwarded"]["records"][0]["media_ids"] == ['c' * 32]


def test_limits_are_explicit(monkeypatch):
    monkeypatch.setattr(forwarded, "MAX_ITEMS", 1)
    parsed = parse_forwarded(card(record([item(), item()])))
    assert parsed["status"] == "limited" and parsed["notice"]
    assert len(parsed["records"]) == 1
    monkeypatch.setattr(forwarded, "MAX_DEPTH", 0)
    nested = item(datatype="17", extra=f'<recorditem>{record([item()])}</recorditem>')
    parsed = parse_forwarded(card(record([nested]), mode="direct"))
    assert parsed["records"][0]["forwarded"]["status"] == "limited"


def test_xml_size_and_entity_declarations_are_bounded(monkeypatch):
    monkeypatch.setattr(forwarded, "MAX_XML_CHARS", 80)
    parsed = parse_forwarded(card(record([item()])))
    assert parsed["status"] == "limited" and parsed["notice"]
    monkeypatch.setattr(forwarded, "MAX_XML_CHARS", 100000)
    parsed = parse_forwarded('<!DOCTYPE msg [<!ENTITY x "test">]><msg><appmsg><type>19</type><title>&x;</title></appmsg></msg>')
    assert parsed["status"] == "limited"

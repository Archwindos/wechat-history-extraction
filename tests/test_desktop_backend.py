import hashlib
import io
import json
from pathlib import Path
import sqlite3
import struct
from datetime import datetime

import pytest

from wechat_exporter.backend import Engine, numbered_shards, sha256_file


@pytest.fixture
def engine(tmp_path):
    engine = Engine(tmp_path / 'state')
    account = 'wxid_test_device'
    root = engine.data_dir / 'snapshots/test' / account / 'db_storage'
    (root / 'message').mkdir(parents=True)
    (root / 'contact').mkdir(parents=True)
    group = '12345@chatroom'
    table = 'Msg_' + hashlib.md5(group.encode()).hexdigest()
    with sqlite3.connect(root / 'contact/contact.db') as con:
        con.execute('CREATE TABLE contact(username TEXT, alias TEXT, nick_name TEXT, remark TEXT, local_type INTEGER)')
        con.executemany('INSERT INTO contact VALUES (?, "", ?, "", 3)',
                        [(group, '验收群'), ('wxid_test', '本人'), ('wxid_peer', '群成员')])
    for shard in (0, 10):
        with sqlite3.connect(root / f'message/message_{shard}.db') as con:
            con.execute('CREATE TABLE Name2Id(user_name TEXT)')
            names = ['wxid_test', 'wxid_peer', group] if shard == 0 else ['wxid_peer', 'wxid_test', group]
            con.executemany('INSERT INTO Name2Id VALUES (?)', [(name,) for name in names])
            con.execute(f'CREATE TABLE {table}(local_id INTEGER, server_id INTEGER, create_time INTEGER,'
                        'local_type INTEGER, real_sender_id INTEGER, message_content BLOB)')
            for i in range(12):
                sender = names.index('wxid_test' if i % 2 else 'wxid_peer') + 1
                text = f'内容-{shard}-{i}\n\n  保留换行和缩进'
                con.execute(f'INSERT INTO {table} VALUES (?,?,?,?,?,?)',
                            (i + 1, 10**18 + shard * 100 + i, 1767225600 + i * 2 + (1 if shard else 0),
                             1, sender, text.encode('utf-8')))
    engine.snapshot = root
    engine.settings = {'account': account, 'captured_at': '2026-10-14T12:00:00'}
    engine._configure()
    return engine


def test_chats_and_shard_local_sender_ids(engine):
    chat = next(chat for chat in engine.list_chats() if chat['name'] == '验收群')
    rows = engine.preview(chat, count=20)
    assert len(rows) == 20
    assert [r['timestamp'] for r in rows] == list(range(1767225604, 1767225624))
    for row in rows:
        index = int(row['original_content'].split('\n')[0].rsplit('-', 1)[1])
        assert row['is_self'] == bool(index % 2)
        assert row['sender_name'] == ('本人' if index % 2 else '群成员')


@pytest.mark.parametrize('kind,extension', [('txt','.txt'),('markdown','.md'),('html','.html'),('jsonl','.jsonl')])
def test_all_formats_preserve_count_and_original_text(engine, tmp_path, kind, extension):
    chat = engine.list_chats()[0]
    output = tmp_path / ('export' + extension)
    result = engine.export(chat, output, output_format=kind, latest=20)
    assert result['count'] == 20
    manifest = json.loads(Path(result['manifest']).read_text(encoding='utf-8'))
    assert manifest['sha256'] == sha256_file(output)
    if kind == 'jsonl':
        records = [json.loads(line) for line in output.read_text(encoding='utf-8').splitlines()]
        assert len(records) == 20
        assert len({r['record_id'] for r in records}) == 20
        assert all(isinstance(r['server_id'], str) and '\n\n  ' in r['original_content'] for r in records)
    elif kind == 'txt':
        assert '\n\n  保留换行和缩进' in output.read_text(encoding='utf-8')


def test_date_window_and_all_records(engine, tmp_path):
    chat = engine.list_chats()[0]
    result = engine.export(chat, tmp_path / 'all.jsonl', 'jsonl', '2026-01-01', '2026-01-01')
    assert result['count'] == 24
    with pytest.raises(ValueError):
        list(engine.iter_rows(chat, '2026-02-01', '2026-01-01'))


def test_export_never_overwrites(engine, tmp_path):
    chat = engine.list_chats()[0]
    output = tmp_path / 'existing.txt'
    output.write_text('existing content', encoding='utf-8')
    with pytest.raises(FileExistsError):
        engine.export(chat, output)
    assert output.read_text(encoding='utf-8') == 'existing content'


def test_stream_is_read_only(engine):
    shard = numbered_shards(engine.snapshot)[0]
    from wechat_exporter.backend import read_connection
    with read_connection(shard) as con:
        with pytest.raises(sqlite3.OperationalError):
            con.execute('DELETE FROM Name2Id')


def test_minute_window_includes_last_second_and_excludes_next_minute(engine):
    chat = engine.list_chats()[0]
    stamp = int(datetime(2026, 10, 14, 12, 30).timestamp())
    for shard in numbered_shards(engine.snapshot):
        with sqlite3.connect(shard) as connection:
            connection.execute(f'UPDATE "{chat["table"]}" SET create_time=?', (stamp - 60,))
    with sqlite3.connect(numbered_shards(engine.snapshot)[0]) as connection:
        for local_id, offset in ((1, 0), (2, 59), (3, 60)):
            connection.execute(f'UPDATE "{chat["table"]}" SET create_time=? WHERE local_id=?', (stamp + offset, local_id))
    rows = list(engine.iter_rows(chat, '2026-10-14 12:30', '2026-10-14 12:30'))
    assert [row['local_id'] for row in rows] == [1, 2]


def test_sender_filter_precedes_latest_limit_and_uses_shard_local_ids(engine):
    chat = engine.list_chats()[0]
    rows = list(engine.iter_rows(chat, latest=20, sender_ids=['wxid_peer']))
    assert len(rows) == 12
    assert all(row['sender_wxid'] == 'wxid_peer' for row in rows)
    assert list(engine.iter_rows(chat, sender_ids=[])) == []
    assert {item['id'] for item in engine.list_senders(chat)} == {'wxid_test', 'wxid_peer'}


def test_text_metadata_can_be_removed_without_changing_original_content(engine, tmp_path):
    chat = engine.list_chats()[0]
    result = engine.export(chat, tmp_path / 'body.txt', 'txt', latest=3,
                           include_time=False, include_sender=False, include_title=False)
    content = Path(result['path']).read_text(encoding='utf-8')
    assert '导出时间' not in content and '2026-' not in content
    assert '本人' not in content and '群成员' not in content
    assert content.strip() == '\n'.join(item['content'] for item in engine.preview(chat, 3)).strip()
    assert result['first_timestamp'] is None and result['last_timestamp'] is None


def test_v2_image_decryption_uses_full_padding_block_and_rejects_wrong_key():
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad
    from PIL import Image
    from wechat_exporter.media import decode_image
    stream = io.BytesIO()
    Image.new('RGB', (120, 80), '#5c8faa').save(stream, 'PNG')
    original = stream.getvalue()
    key, xor = b'0123456789abcdef', 42
    size, tail = 64, 20
    container = (b'\x07\x08V2\x08\x07' + struct.pack('<II', size, tail) + b'\x00'
                 + AES.new(key, AES.MODE_ECB).encrypt(pad(original[:size], 16))
                 + original[size:-tail] + bytes(value ^ xor for value in original[-tail:]))
    assert decode_image(container, [(key, xor)]) == original
    assert decode_image(container, [(b'x' * 16, xor)]) is None
    assert decode_image(container[:25], [(key, xor)]) is None


def test_pdf_embeds_registered_v2_image_and_local_pdf_first_page(engine, tmp_path, monkeypatch):
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad
    from PIL import Image, ImageDraw
    import pymupdf
    chat = engine.list_chats()[0]
    source_root = tmp_path / 'original'
    account = source_root / engine.settings['account']
    engine.settings['source_root'] = str(source_root)
    key, xor = b'0123456789abcdef', 42
    monkeypatch.setattr('wechat_exporter.media.account_image_keys', lambda _account: [(key, xor)])
    identifier = 'a' * 32
    stamp = 1767226000
    picture = Image.new('RGB', (800, 320), '#c6dee9')
    draw = ImageDraw.Draw(picture)
    draw.rectangle((30, 30, 380, 290), fill='#487a96')
    draw.text((420, 145), 'LOCAL IMAGE TEST', fill='#183b4e')
    image_stream = io.BytesIO()
    picture.save(image_stream, 'PNG')
    original = image_stream.getvalue()
    container = (b'\x07\x08V2\x08\x07' + struct.pack('<II', 64, 20) + b'\x00'
        + AES.new(key, AES.MODE_ECB).encrypt(pad(original[:64], 16))
        + original[64:-20] + bytes(value ^ xor for value in original[-20:]))
    image_dir = account / 'msg/attach' / hashlib.md5(chat['wxid'].encode()).hexdigest() / '2026-01/Img'
    image_dir.mkdir(parents=True)
    (image_dir / (identifier + '.dat')).write_bytes(container)
    resource = engine.snapshot / 'message/message_resource.db'
    with sqlite3.connect(resource) as connection:
        connection.execute('CREATE TABLE ChatName2Id(user_name TEXT)')
        connection.execute('INSERT INTO ChatName2Id VALUES (?)', (chat['wxid'],))
        connection.execute('CREATE TABLE MessageResourceInfo(chat_id INTEGER,message_local_id INTEGER,'
                           'message_svr_id INTEGER,message_create_time INTEGER,message_local_type INTEGER,packed_info BLOB)')
        connection.execute('INSERT INTO MessageResourceInfo VALUES (1,100,1000,?,3,?)',
                           (stamp, b'\x12\x22\x0a\x20' + identifier.encode()))
    links = engine.snapshot / 'hardlink/hardlink.db'
    links.parent.mkdir()
    with sqlite3.connect(links) as connection:
        connection.execute('CREATE TABLE dir2id(username TEXT)')
        connection.executemany('INSERT INTO dir2id VALUES (?)',
                              [(hashlib.md5(chat['wxid'].encode()).hexdigest(),), ('2026-01',)])
        connection.execute('CREATE TABLE image_hardlink_info_v4(file_name TEXT,dir1 INTEGER,dir2 INTEGER)')
        connection.execute('INSERT INTO image_hardlink_info_v4 VALUES (?,1,2)', (identifier + '.dat',))
    file_dir = account / 'msg/file/2026-01'
    file_dir.mkdir(parents=True)
    attachment = file_dir / '文件封面测试.pdf'
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((40, 65), 'ATTACHMENT FIRST PAGE', fontsize=20)
        page.draw_rect(pymupdf.Rect(40, 100, 400, 260), fill=(.88, .92, .94))
        document.save(attachment)
    app = '<msg><appmsg><type>6</type><title>文件封面测试.pdf</title><fileext>pdf</fileext></appmsg></msg>'
    with sqlite3.connect(numbered_shards(engine.snapshot)[0]) as connection:
        connection.executemany(f'INSERT INTO "{chat["table"]}" VALUES (?,?,?,?,?,?)',
            [(100, 1000, stamp, 3, 2, b'<msg><img/></msg>'),
             (101, 1001, stamp + 1, 49, 2, app.encode())])
    result = engine.export(chat, tmp_path / 'images-and-cover.pdf', 'pdf', latest=2)
    assert result['count'] == 2
    assert result['media'] == {'images': 1, 'covers': 1, 'missing_images': 0, 'missing_covers': 0,
                               'emojis': 0, 'videos': 0, 'missing_emojis': 0, 'missing_videos': 0}
    with pymupdf.open(result['path']) as document:
        text = ''.join(page.get_text() for page in document)
        assert '验收群' in text and '群成员' in text and '文件封面测试.pdf' in text
        assert sum(len(page.get_images()) for page in document) == 2


def test_pdf_long_chinese_text_paginates_and_hides_metadata(engine, tmp_path):
    import pymupdf
    chat = engine.list_chats()[0]
    text = ('\n\n' + '中文内容需要正确分页并保留完整正文。' * 1000
            + '<b>这不是格式标签</b>\\input{literal} % # _ & $ ^ ~ { }'
            + '\nhttps://example.com/path/' + 'long-word-' * 30 + '?literal=%20&value=1'
            + '\n\n空行后的正文\n  保留缩进\n👍🉑💩🤷🍐🍬🤮🤣✖\n中文🍐接续文本👍\n')
    with sqlite3.connect(numbered_shards(engine.snapshot)[0]) as connection:
        connection.execute(f'INSERT INTO "{chat["table"]}" VALUES (100,1000,1767226000,1,2,?)', (text.encode(),))
    result = engine.export(chat, tmp_path / 'long-text.pdf', 'pdf', latest=1,
                           include_time=False, include_sender=False, include_title=False)
    with pymupdf.open(result['path']) as document:
        assert document.page_count > 1
        rendered = ''.join(page.get_text() for page in document)
        assert '验收群' not in rendered and '群成员' not in rendered and '2026-' not in rendered
        assert ''.join(rendered.split()) == ''.join(text.split())
        assert all(0 <= box[0] <= box[2] <= page.rect.width - 15 * 72 / 25.4 + 2 for page in document
                   for box in page.get_text('blocks'))


def test_local_image_survives_pruned_hardlinks_and_duplicate_resource_rows(engine, tmp_path, monkeypatch):
    from PIL import Image
    from wechat_exporter.media import LocalMedia
    chat = engine.list_chats()[0]
    source = tmp_path / 'source'
    engine.settings['source_root'] = str(source)
    identifier = 'b' * 32
    folder = source / engine.settings['account'] / 'msg/attach' / hashlib.md5(chat['wxid'].encode()).hexdigest() / '2026-01/Img'
    folder.mkdir(parents=True)
    (folder / (identifier + '.dat')).write_bytes(b'unsupported original container')
    Image.new('RGB', (100, 80), '#3c739b').save(folder / (identifier + '_t.dat'), 'PNG')
    with sqlite3.connect(engine.snapshot / 'message/message_resource.db') as connection:
        connection.execute('CREATE TABLE ChatName2Id(user_name TEXT)')
        connection.execute('INSERT INTO ChatName2Id VALUES (?)', (chat['wxid'],))
        connection.execute('CREATE TABLE MessageResourceInfo(chat_id INTEGER,message_local_id INTEGER,'
                           'message_svr_id INTEGER,message_create_time INTEGER,message_local_type INTEGER,packed_info BLOB)')
        connection.executemany('INSERT INTO MessageResourceInfo VALUES (1,1,8,100,3,?)',
                               [(b'\x12\x22\x0a\x20' + identifier.encode(),)] * 2)
    record = dict(local_id='1', server_id='8', timestamp=100, type='3', original_content='<msg><img/></msg>')
    media = LocalMedia(engine, chat)
    try:
        assert media._resource_ids(record) == [identifier]
        data = media.image(record)
        with Image.open(io.BytesIO(data)) as image:
            assert image.size == (100, 80)
    finally:
        media.close()


def test_pdf_recovers_encrypted_sticker_and_video_card_and_reference_nickname(engine, tmp_path, monkeypatch):
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad
    from PIL import Image, ImageDraw
    import pymupdf
    chat = engine.list_chats()[0]
    source = tmp_path / 'source'
    engine.settings['source_root'] = str(source)
    account = source / engine.settings['account']
    key, digest = b'0123456789abcdef', 'c' * 32
    monkeypatch.setattr('wechat_exporter.media.account_emoticon_keys', lambda _account: [key])
    first = Image.new('RGB', (100, 80), 'white')
    second = first.copy()
    ImageDraw.Draw(second).rectangle((10, 10, 90, 70), fill='#3c739b')
    animated = io.BytesIO()
    first.save(animated, 'GIF', save_all=True, append_images=[second], duration=100, loop=0)
    sticker = account / 'cache/2026-01/Emoticon' / digest[:2] / digest
    sticker.parent.mkdir(parents=True)
    sticker.write_bytes(AES.new(key, AES.MODE_CBC, iv=key).encrypt(pad(animated.getvalue(), 16)))
    thumb_url = 'https://example.invalid/cover.jpg'
    thumb = account / 'cache/2026-01/HttpResource' / hashlib.md5(thumb_url.encode()).hexdigest()
    thumb.parent.mkdir(parents=True)
    second.save(thumb, 'PNG')
    messages = [
        (47, f'<msg><emoji md5="{digest}"/></msg>'),
        (49, '<msg><appmsg><type>4</type><title>视频卡片</title><url>https://example.invalid/player?token=private</url>'
             f'<appattach><cdnthumburl>{thumb_url}</cdnthumburl></appattach></appmsg></msg>'),
        (49, '<msg><appmsg><type>57</type><title>回复正文</title><refermsg><fromusr>wxid_peer</fromusr>'
             '<displayname>旧昵称</displayname><content>被引用的正文</content></refermsg></appmsg></msg>'),
    ]
    with sqlite3.connect(numbered_shards(engine.snapshot)[0]) as connection:
        connection.executemany(f'INSERT INTO "{chat["table"]}" VALUES (?,?,?,?,?,?)',
            [(100 + i, 1000 + i, 1767228000 + i, type_, 2, text.encode()) for i, (type_, text) in enumerate(messages)])
    result = engine.export(chat, tmp_path / 'sticker-video-quote.pdf', 'pdf', latest=3)
    assert result['media']['emojis'] == result['media']['videos'] == 1
    assert result['media']['missing_emojis'] == result['media']['missing_videos'] == 0
    quote = engine.preview(chat, 1)[0]
    assert quote['app']['quoted_sender'] == '群成员'
    assert 'wxid_peer' not in quote['content']
    with pymupdf.open(result['path']) as document:
        text = ''.join(page.get_text() for page in document)
        assert '视频卡片' in text and '被引用的正文' in text
        assert '↩' in text
        assert 'example.invalid' not in text and 'wxid_peer' not in text
        assert sum(len(page.get_images()) for page in document) == 2


def test_local_video_uses_saved_preview_image(engine, tmp_path):
    from PIL import Image
    from wechat_exporter.media import LocalMedia
    source = tmp_path / 'source'
    engine.settings['source_root'] = str(source)
    digest = 'd' * 32
    folder = source / engine.settings['account'] / 'msg/video/2026-01'
    folder.mkdir(parents=True)
    Image.new('RGB', (100, 80), '#3c739b').save(folder / (digest + '_thumb.jpg'))
    record = dict(local_id='1', server_id='8', timestamp=1767228000, type='43',
                  original_content=f'<msg><videomsg md5="{digest}"/></msg>')
    media = LocalMedia(engine, engine.list_chats()[0])
    try:
        assert media.video(record)
    finally:
        media.close()


def test_missing_external_ffmpeg_leaves_media_unavailable(engine, monkeypatch):
    from wechat_exporter.media import LocalMedia

    def missing_ffmpeg():
        raise RuntimeError("No ffmpeg exe could be found")

    monkeypatch.setattr('imageio_ffmpeg.get_ffmpeg_exe', missing_ffmpeg)
    media = LocalMedia(engine, engine.list_chats()[0])
    try:
        assert media._wxgf(b'wxgf' + b'not-a-real-video') is None
    finally:
        media.close()

import contextlib
import hashlib
import hmac
import io
import struct
import sys

from Crypto.Cipher import AES

from wechat_exporter.cache_keys import authenticated_mode
from wechat_exporter.backend import LogWriter


def make_page():
    raw = bytes(range(32))
    salt = bytes(range(16))
    derived = hashlib.pbkdf2_hmac('sha512', raw, salt, 256000, 32)
    mac_key = hashlib.pbkdf2_hmac('sha512', derived, bytes(x ^ 0x3a for x in salt), 2, 32)
    payload = bytearray(4000)
    payload[:8] = bytes([0x10,0x00,1,1,0x50,0x40,0x20,0x20])
    iv = b'v' * 16
    page = salt + AES.new(derived, AES.MODE_CBC, iv).encrypt(payload) + iv
    page += hmac.new(mac_key, page[16:] + struct.pack('<I',1), hashlib.sha512).digest()
    return raw, derived, page


def test_cache_key_requires_page_hmac():
    raw, derived, page = make_page()
    assert authenticated_mode(raw.hex(), page) == 'raw'
    assert authenticated_mode(derived.hex(), page) == 'derived'
    assert authenticated_mode('ff' * 32, page) is None
    corrupted = bytearray(page)
    corrupted[100] ^= 1
    assert authenticated_mode(derived.hex(), bytes(corrupted)) is None


def test_derived_key_uses_full_page_authentication(tmp_path):
    from wechat_exporter.backend import decrypt_all
    _raw, derived, page = make_page()
    source = tmp_path / 'source.db'
    source.write_bytes(page)
    output = tmp_path / 'output.db'
    assert decrypt_all.decrypt_db(derived, str(source), str(output), 'derived')
    assert output.read_bytes().startswith(b'SQLite format 3\x00')


def test_log_writer_accepts_print_callback(monkeypatch):
    output = io.StringIO()
    monkeypatch.setattr(sys, 'stdout', output)
    writer = LogWriter(print)
    with contextlib.redirect_stdout(writer):
        print('capture started')
    assert output.getvalue() == 'capture started\n'

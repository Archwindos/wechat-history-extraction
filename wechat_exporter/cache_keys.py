"""Find WCDB's cached key strings and authenticate them against local pages.

No keys are logged. This reads only the user's running WeChat process and
accepts candidates matching the salts and HMACs of the selected databases.
"""
import ctypes
from ctypes import wintypes
import hashlib
import hmac
import json
from pathlib import Path
import re
import struct
import threading

import frida
from Crypto.Cipher import AES


def running_pid():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in ("peak_working", "working", "peak_paged", "paged",
                                               "peak_nonpaged", "nonpaged", "pagefile", "peak_pagefile")]
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    candidates = []
    for process in frida.get_local_device().enumerate_processes():
        if process.name.casefold() != "weixin.exe":
            continue
        handle = kernel.OpenProcess(0x410, False, process.pid)
        if not handle:
            continue
        try:
            counters = Counters()
            counters.cb = ctypes.sizeof(counters)
            if psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                candidates.append((counters.working, process.pid))
        finally:
            kernel.CloseHandle(handle)
    return max(candidates)[1] if candidates else None


def authenticated_mode(candidate, page):
    if not re.fullmatch(r"[0-9a-fA-F]{64}", candidate) or len(page) != 4096:
        return None
    supplied = bytes.fromhex(candidate)
    salt = page[:16]
    choices = [("derived", supplied), ("raw", hashlib.pbkdf2_hmac("sha512", supplied, salt, 256000, 32))]
    for mode, enc in choices:
        mac_key = hashlib.pbkdf2_hmac("sha512", enc, bytes(value ^ 0x3A for value in salt), 2, 32)
        mac = hmac.new(mac_key, page[16:4032] + struct.pack("<I", 1), hashlib.sha512).digest()
        if hmac.compare_digest(mac, page[4032:4096]):
            plain = AES.new(enc, AES.MODE_CBC, page[4016:4032]).decrypt(page[16:32])
            if plain[0:2] == b"\x10\x00" and plain[4:6] == b"\x50\x40":
                return mode
    return None


SCAN = r"""
var wanted = SALTS;
var unique = Object.create(null), scanned=0, skipped=0;
function pattern(text, wide) {
  return text.split('').map(function(c) { var hex=c.charCodeAt(0).toString(16).padStart(2,'0'); return wide ? hex+' 00' : hex; }).join(' ');
}
var ranges = Process.enumerateRanges({protection:'rw-',coalesce:true});
ranges.forEach(function(range) {
  if (range.size > 256*1024*1024) { skipped++; return; }
  wanted.forEach(function(salt) {
    [salt,salt.toUpperCase()].forEach(function(text) {
      [false,true].forEach(function(wide) {
        try {
          Memory.scanSync(range.base,range.size,pattern(text,wide)).forEach(function(hit) {
            try {
              var begin=hit.address.sub(wide ? 128 : 64);
              var full=(wide ? begin.readUtf16String(96) : begin.readUtf8String(96)).toLowerCase();
              if (!/^[0-9a-f]{96}$/.test(full) || full.slice(64)!==salt) return;
              var key=full.slice(0,64), token=salt+key;
              if (!unique[token]) { unique[token]=true; send({kind:'candidate',salt:salt,key:key}); }
            } catch(e) {}
          });
        } catch(e) { skipped++; }
      });
    });
  });
  scanned+=range.size;
});
send({kind:'done',bytes:scanned,skipped:skipped});
"""


def read_cached_keys(sources, log=lambda _: None, pid=None):
    pages = {}
    for source in sources:
        with Path(source).open("rb") as handle:
            page = handle.read(4096)
        if len(page) == 4096:
            pages.setdefault(page[:16].hex(), []).append((Path(source), page))
    selected_pid = pid or running_pid()
    if not selected_pid:
        raise RuntimeError("没有检测到已运行的微信。请从桌面打开微信并完成登录。")
    found = {}
    complete = threading.Event()
    errors = []
    def receive(event, _data):
        if event.get("type") == "error":
            errors.append("微信缓存读取失败：" + event.get("description", "unknown"))
            complete.set()
            return
        payload = event.get("payload", {})
        if not isinstance(payload, dict):
            return
        if payload.get("kind") == "candidate":
            for path, page in pages.get(payload["salt"], []):
                mode = authenticated_mode(payload["key"], page)
                if mode:
                    found[str(path)] = {"key": payload["key"], "mode": mode}
        elif payload.get("kind") == "done":
            complete.set()
    session = frida.get_local_device().attach(selected_pid)
    try:
        script = session.create_script(SCAN.replace("SALTS", json.dumps(list(pages))))
        script.on("message", receive)
        script.load()
        if not complete.wait(45):
            raise TimeoutError("微信缓存扫描超时，本轮未发布任何数据库。")
    finally:
        session.detach()
    if errors:
        raise RuntimeError(errors[0])
    log(f"本机缓存中验证到 {len(found)}/{len(sources)} 个数据库的密钥。")
    return found

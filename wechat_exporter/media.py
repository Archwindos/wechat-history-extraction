"""Read selected messages' local images and attachment previews. No CDN requests."""
from datetime import datetime
import hashlib
import io
import os
from pathlib import Path
import re
import struct
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile

from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
from PIL import Image, ImageStat
import pymupdf

from .backend import read_connection
import media_manifest


V2 = b"\x07\x08V2\x08\x07"
V1 = b"\x07\x08V1\x08\x07"


def account_seeds():
    """Read only seed filenames; derived keys never leave this process."""
    roaming = Path(os.environ.get("APPDATA", str(Path.home() / "AppData/Roaming")))
    seeds = set()
    patterns = (r"key_0_(\d+)_", r"key_(\d{8,12})_\d+_", r"monitordata_(\d{8,12})_")
    for directory in (roaming / "Tencent/xwechat").glob("net*/kvcomm"):
        if directory.is_dir():
            for file in directory.iterdir():
                for pattern in patterns:
                    match = re.search(pattern, file.name)
                    if match and int(match[1]):
                        seeds.add(int(match[1]))
    return sorted(seeds)


def account_image_keys(account):
    wxid = re.sub(r"_[0-9a-fA-F]{4}$", "", account.name)
    return [(hashlib.md5(f"{seed}{wxid}".encode()).hexdigest()[:16].encode(), seed & 255)
            for seed in account_seeds()]


def account_emoticon_keys(account):
    wxid = re.sub(r"_[0-9a-fA-F]{4}$", "", account.name)
    return [hashlib.md5(f"{seed}{wxid}EMOTICON".encode()).digest()[:16] for seed in account_seeds()]


def decode_image(data, candidates=()):
    if data[:6] in (V1, V2):
        if len(data) < 31:
            return None
        aes_size, xor_size = struct.unpack_from("<II", data, 6)
        encrypted_size = aes_size + (16 - aes_size % 16)
        if 15 + encrypted_size + xor_size > len(data):
            return None
        choices = [(b"cfcd208495d565ef", xor) for _, xor in candidates] if data[:6] == V1 else candidates
        for key, xor in choices:
            try:
                prefix = unpad(AES.new(key, AES.MODE_ECB).decrypt(data[15:15 + encrypted_size]), 16)
                if len(prefix) != aes_size:
                    continue
                raw_end = len(data) - xor_size
                decoded = prefix + data[15 + encrypted_size:raw_end]
                decoded += bytes(value ^ xor for value in data[raw_end:])
                if decoded.startswith(b"wxgf"):
                    continue  # Prefer the registered JPEG thumbnail for this container.
                with Image.open(io.BytesIO(decoded)) as image:
                    image.verify()
                return decoded
            except (ValueError, OSError, SyntaxError):
                continue
        return None
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
        return data
    except (ValueError, OSError, SyntaxError):
        for magic in (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"GIF89a", b"GIF87a", b"BM"):
            if len(data) < len(magic):
                continue
            xor = data[0] ^ magic[0]
            if bytes(value ^ xor for value in data[:len(magic)]) != magic:
                continue
            decoded = bytes(value ^ xor for value in data)
            try:
                with Image.open(io.BytesIO(decoded)) as image:
                    image.verify()
                return decoded
            except (ValueError, OSError, SyntaxError):
                continue
    return None


class LocalMedia:
    def __init__(self, engine, chat):
        self.engine, self.chat = engine, chat
        self.account = Path(engine.settings.get("source_root", engine.data_dir / "original")) / engine.settings["account"]
        self.keys = None
        self.emoticon_keys = None
        self.decoded_stickers = {}
        self.decoded_wxgf = {}
        self.office = {}
        self.com_initialized = False
        self.temp = tempfile.TemporaryDirectory(prefix="covers-", dir=engine.data_dir)

    def close(self):
        collections = {"Word": "Documents", "PowerPoint": "Presentations", "Excel": "Workbooks"}
        for kind, application in self.office.items():
            try:
                if getattr(application, collections[kind]).Count == 0:
                    application.Quit()
            except Exception:
                pass
        if self.com_initialized:
            import pythoncom
            pythoncom.CoUninitialize()
        self.temp.cleanup()

    def _read_image(self, path):
        if not path.resolve().is_relative_to(self.account.resolve()) or not path.is_file():
            return None
        try:
            data = path.read_bytes()
            if data[:6] in (V1, V2) and self.keys is None:
                self.keys = account_image_keys(self.account)
            return decode_image(data, self.keys or [])
        except (OSError, ValueError):
            return None

    def _resource_ids(self, record):
        path = self.engine.snapshot / "message/message_resource.db"
        if not path.is_file():
            return []
        with read_connection(path) as connection:
            query = ('SELECT i.packed_info FROM MessageResourceInfo i '
                'JOIN ChatName2Id c ON c.rowid=i.chat_id WHERE c.user_name=? '
                'AND i.message_local_id=? AND i.message_create_time=? '
                'AND (i.message_local_type & 65535)=?')
            parameters = [self.chat["wxid"], record["local_id"], record["timestamp"], int(record["type"])]
            if record["server_id"] != "0":
                query += ' AND CAST(i.message_svr_id AS TEXT)=?'
                parameters.append(record["server_id"])
            rows = connection.execute(query, parameters).fetchall()
        identifiers = []
        for row in rows:
            blob = row[0].encode() if isinstance(row[0], str) else row[0]
            if record["type"] in ("3", "43"):
                identifier = media_manifest._image_resource_id(blob.hex())
                if identifier:
                    identifiers.append(identifier)
            else:
                identifiers.extend(value.decode().lower() for value in re.findall(rb"[0-9a-fA-F]{32}", blob))
        return list(dict.fromkeys(identifiers))

    def _registered_paths(self, identifiers, table, category):
        path = self.engine.snapshot / "hardlink/hardlink.db"
        if not path.is_file():
            return []
        paths = []
        with read_connection(path) as connection:
            for identifier in identifiers:
                rows = connection.execute(f'SELECT h.file_name,d1.username,d2.username FROM "{table}" h '
                    'JOIN dir2id d1 ON d1.rowid=h.dir1 JOIN dir2id d2 ON d2.rowid=h.dir2 '
                    'WHERE lower(substr(h.file_name,1,32))=?', (identifier,)).fetchall()
                for filename, directory, month in rows:
                    if any(not item or re.search(r"[/\\:\x00]", item) or item in (".", "..")
                           for item in (filename, directory, month)):
                        continue
                    if category == "Img":
                        if directory != hashlib.md5(self.chat["wxid"].encode()).hexdigest():
                            continue
                        paths.append(self.account / "msg/attach" / directory / month / "Img" / filename)
                    else:
                        paths.extend((self.account / "msg/file" / directory / month / filename,
                                      self.account / "msg/file" / directory / filename,
                                      self.account / "msg/file" / month / filename))
        return list(dict.fromkeys(paths))

    def image(self, record):
        identifiers = self._resource_ids(record)
        paths = self._registered_paths(identifiers, "image_hardlink_info_v4", "Img")
        # Hardlink rows can be pruned while the actual files are still present.
        directory = hashlib.md5(self.chat["wxid"].encode()).hexdigest()
        root = self.account / "msg/attach" / directory
        for identifier in identifiers:
            paths.extend(root.glob("*/Img/" + identifier + "*"))
        # Windows often registers only the original; its JPEG thumbnails are siblings.
        for path in list(paths):
            identifier = path.stem.split("_")[0]
            if re.fullmatch(r"[0-9a-fA-F]{32}", identifier):
                paths.extend(path.parent.glob(identifier + "*"))
        paths = list(dict.fromkeys(paths))
        paths.sort(key=lambda path: (path.stem.endswith("_t"), path.stem.endswith("_h")))
        for path in paths:
            data = self._read_image(path)
            if data:
                return data
        return self.thumbnail(record)

    @staticmethod
    def _xml(record):
        raw = record.get("original_content", "")
        try:
            return ET.fromstring(raw[raw.find("<"):])
        except ET.ParseError:
            return None

    def video(self, record):
        thumbnail = self.thumbnail(record)
        if thumbnail:
            return thumbnail
        identifiers = self._resource_ids(record)
        root = self._xml(record)
        if root is not None:
            for node in root.iter():
                values = [value for name, value in node.attrib.items() if "md5" in name]
                if "md5" in node.tag:
                    values.append(node.text or "")
                identifiers.extend(value.lower() for value in values if re.fullmatch(r"[0-9a-fA-F]{32}", value))
        for identifier in dict.fromkeys(identifiers):
            for pattern in (identifier + "_thumb.*", identifier + ".jpg", identifier + ".png"):
                for path in (self.account / "msg/video").glob("*/" + pattern):
                    image = self._read_image(path)
                    if image:
                        return image
        return None

    def _wxgf(self, data):
        digest = hashlib.sha256(data).hexdigest()
        if digest in self.decoded_wxgf:
            return self.decoded_wxgf[digest]
        import imageio_ffmpeg
        folder = Path(self.temp.name) / digest
        folder.mkdir(exist_ok=True)
        try:
            subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-nostdin", "-hide_banner", "-loglevel", "error",
                            "-threads", "1", "-f", "hevc", "-i", "pipe:0", "-frames:v", "8", str(folder / "%02d.png")],
                           input=data[4:], capture_output=True, timeout=30,
                           creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            best, score = None, 0
            for path in sorted(folder.glob("*.png")):
                with Image.open(path) as image:
                    detail = sum(ImageStat.Stat(image.convert("RGB")).stddev)
                    if detail > score:
                        best, score = path, detail
            result = best.read_bytes() if best else None
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
            result = None
        self.decoded_wxgf[digest] = result
        return result

    @staticmethod
    def _sticker_frame(data):
        try:
            with Image.open(io.BytesIO(data)) as image:
                best, score = None, -1
                for frame in range(min(getattr(image, "n_frames", 1), 8)):
                    image.seek(frame)
                    candidate = image.convert("RGBA")
                    detail = sum(ImageStat.Stat(candidate.convert("RGB")).stddev)
                    if detail > score:
                        best, score = candidate.copy(), detail
                output = io.BytesIO()
                best.save(output, "PNG")
                return output.getvalue()
        except (OSError, ValueError, SyntaxError):
            return None

    def emoji(self, record):
        root = self._xml(record)
        if root is None:
            return None
        identifiers = []
        for node in root.iter():
            values = [value for name, value in node.attrib.items() if "md5" in name]
            if "md5" in node.tag:
                values.append(node.text or "")
            identifiers.extend(value.lower() for value in values if re.fullmatch(r"[0-9a-fA-F]{32}", value))
        if self.emoticon_keys is None:
            self.emoticon_keys = account_emoticon_keys(self.account)
        for identifier in dict.fromkeys(identifiers):
            if identifier in self.decoded_stickers:
                if self.decoded_stickers[identifier]:
                    return self.decoded_stickers[identifier]
                continue
            paths = []
            for category in ("Persist", "PersistStore", "Thumb", "ThumbStore", "Temp"):
                paths.extend((self.account / "business/emoticon" / category / identifier[:2]).glob(identifier + "*"))
            paths.extend((self.account / "cache").glob("*/Emoticon/" + identifier[:2] + "/" + identifier + "*"))
            result = None
            for path in paths:
                if not path.is_file() or not path.resolve().is_relative_to(self.account.resolve()):
                    continue
                try:
                    data = path.read_bytes()
                except OSError:
                    continue
                result = self._sticker_frame(data)
                if not result and len(data) % 16 == 0:
                    for key in self.emoticon_keys:
                        try:
                            decoded = unpad(AES.new(key, AES.MODE_CBC, iv=key).decrypt(data), 16)
                        except ValueError:
                            continue
                        result = self._wxgf(decoded) if decoded.startswith(b"wxgf") else self._sticker_frame(decoded)
                        if result:
                            break
                if result:
                    break
            self.decoded_stickers[identifier] = result
            if result:
                return result
        return self.thumbnail(record)

    def thumbnail(self, record):
        identifiers = self._resource_ids(record)
        app = record.get("app", {})
        if app.get("thumb_url"):
            identifiers.append(hashlib.md5(app["thumb_url"].encode()).hexdigest())
        xml = self._xml(record)
        if xml is not None:
            for node in xml.iter():
                values = [(name, value) for name, value in node.attrib.items()]
                values.append((node.tag, node.text or ""))
                for name, value in values:
                    if "thumb" in name and "url" in name and value:
                        identifiers.append(hashlib.md5(value.encode()).hexdigest())
                    if "thumb" in name and "md5" in name and re.fullmatch(r"[0-9a-fA-F]{32}", value):
                        identifiers.append(value.lower())
        server_id = str(int(record["server_id"]) & ((1 << 64) - 1))
        if server_id != "0":
            identifiers.append(server_id)
        month = datetime.fromtimestamp(record["timestamp"]).strftime("%Y-%m")
        directory = hashlib.md5(self.chat["wxid"].encode()).hexdigest()
        roots = [self.account / "cache" / month / "Message" / directory / "Thumb"]
        roots.extend((self.account / "cache").glob("*/Message/" + directory + "/Thumb"))
        for root in dict.fromkeys(roots):
            if not root.is_dir():
                continue
            matches = [path for path in root.iterdir() if path.is_file() and any(
                path.stem == identifier or path.stem.startswith(identifier + "_") for identifier in identifiers)]
            for path in sorted(matches, key=lambda path: path.stat().st_size, reverse=True):
                data = self._read_image(path)
                if data:
                    return data
        for identifier in dict.fromkeys(identifiers):
            if not re.fullmatch(r"[0-9a-fA-F]{32}", identifier):
                continue
            for path in (self.account / "cache").glob("*/HttpResource/" + identifier + "*"):
                data = self._read_image(path)
                if data:
                    return data
        return None

    def _attachment(self, record):
        app = record["app"]
        name, digest = app.get("file_name", ""), app.get("file_md5", "").lower()
        paths = []
        index = self.engine.snapshot / "hardlink/hardlink.db"
        if re.fullmatch(r"[0-9a-f]{32}", digest) and index.is_file():
            with read_connection(index) as connection:
                rows = connection.execute('SELECT h.file_name,d1.username,d2.username '
                    'FROM file_hardlink_info_v4 h JOIN dir2id d1 ON d1.rowid=h.dir1 '
                    'JOIN dir2id d2 ON d2.rowid=h.dir2 WHERE lower(h.md5)=?', (digest,)).fetchall()
                for filename, directory, month in rows:
                    if any(re.search(r"[/\\:\x00]", item or "") or item in (".", "..")
                           for item in (filename, directory, month)):
                        continue
                    paths.extend((self.account / "msg/file" / directory / month / filename,
                                  self.account / "msg/file" / directory / filename,
                                  self.account / "msg/file" / month / filename))
        if name and Path(name).name == name:
            root = self.account / "msg/file"
            if root.is_dir():
                paths.extend(path for path in root.glob("*/*") if path.is_file() and path.name == name)
                paths.append(root / name)
        existing = [path for path in dict.fromkeys(paths)
                    if path.resolve().is_relative_to(self.account.resolve()) and path.is_file()]
        if digest:
            for path in existing:
                checksum = hashlib.md5()
                with path.open("rb") as handle:
                    for block in iter(lambda: handle.read(1024 * 1024), b""):
                        checksum.update(block)
                if checksum.hexdigest() == digest:
                    return path
            return None
        return existing[0] if len(existing) == 1 else None

    @staticmethod
    def _pdf_cover(path):
        try:
            with pymupdf.open(path) as document:
                if document.page_count and not document.needs_pass:
                    return document[0].get_pixmap(matrix=pymupdf.Matrix(1.4, 1.4), alpha=False).tobytes("png")
        except (RuntimeError, ValueError):
            pass
        return None

    def _office_cover(self, path):
        suffix = path.suffix.lower()
        kind = ("Word" if suffix in (".doc", ".docx", ".rtf", ".odt") else
                "PowerPoint" if suffix in (".ppt", ".pptx") else
                "Excel" if suffix in (".xls", ".xlsx") else None)
        if not kind:
            return None
        document = None
        try:
            import pythoncom
            import win32com.client
            if not self.com_initialized:
                pythoncom.CoInitialize()
                self.com_initialized = True
            if kind not in self.office:
                application = win32com.client.DispatchEx(kind + ".Application")
                collection = {"Word": "Documents", "PowerPoint": "Presentations", "Excel": "Workbooks"}[kind]
                if getattr(application, collection).Count:
                    return None
                self.office[kind] = application
                application.AutomationSecurity = 3
                if kind != "PowerPoint":
                    application.Visible = False
                    application.DisplayAlerts = False if kind == "Excel" else 0
            application = self.office[kind]
            output = Path(self.temp.name) / (hashlib.sha256(str(path).encode()).hexdigest() + ".pdf")
            if kind == "Word":
                document = application.Documents.Open(str(path), ReadOnly=True, AddToRecentFiles=False,
                                                       Visible=False, ConfirmConversions=False)
                document.ExportAsFixedFormat(str(output), 17, OpenAfterExport=False, Range=3, From=1, To=1)
            elif kind == "Excel":
                document = application.Workbooks.Open(str(path), UpdateLinks=0, ReadOnly=True)
                document.Worksheets(1).ExportAsFixedFormat(0, str(output), From=1, To=1, OpenAfterPublish=False)
            else:
                document = application.Presentations.Open(str(path), ReadOnly=-1, Untitled=0, WithWindow=0)
                png = output.with_suffix(".png")
                height = max(1, int(1100 * document.PageSetup.SlideHeight / document.PageSetup.SlideWidth))
                document.Slides(1).Export(str(png), "PNG", 1100, height)
                return png.read_bytes()
            return self._pdf_cover(output)
        except Exception:
            return None
        finally:
            if document is not None:
                try:
                    document.Close() if kind == "PowerPoint" else document.Close(False)
                except Exception:
                    pass

    def cover(self, record):
        thumbnail = self.thumbnail(record)
        if thumbnail:
            return thumbnail
        if record.get("app", {}).get("kind") != "file":
            return None
        path = self._attachment(record)
        if not path:
            return None
        if path.suffix.lower() == ".pdf":
            return self._pdf_cover(path)
        if path.suffix.lower() in (".docx", ".pptx", ".xlsx"):
            try:
                with zipfile.ZipFile(path) as archive:
                    for name in ("docProps/thumbnail.jpeg", "docProps/thumbnail.jpg", "docProps/thumbnail.png"):
                        if name in archive.namelist():
                            preview = decode_image(archive.read(name))
                            if preview:
                                return preview
            except (OSError, ValueError, zipfile.BadZipFile):
                pass
        return self._office_cover(path)

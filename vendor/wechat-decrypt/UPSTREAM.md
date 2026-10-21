# Upstream

- Repository: https://github.com/tzwkb/wechat-decrypt
- Base commit: `f0b3f9f95dda2d954ee3dbe1d139fb1944a5ecfd`
- License: MIT; see [LICENSE](LICENSE).

This directory contains only the Python modules needed by the Windows desktop
exporter. It is a modified subset, not a Git submodule or an unchanged upstream
checkout. Local changes cover SQLite cache handling, Windows WAL-safe decryption,
the Windows extraction entry point, and contact-name resolution in quoted messages.

No keys, contacts, databases, generated configuration or upstream Git history are
included. Runtime configuration is provided by `wechat_exporter.backend.Engine`.

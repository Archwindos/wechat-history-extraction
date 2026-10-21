# 第三方来源与许可

本文件记录源码来源及主要运行依赖的许可边界。源码目录不包含 Python 依赖包、FFmpeg、Pandoc、TeX、Microsoft Office 或字体二进制。安装依赖与再次分发依赖是不同的行为；另行发布可执行程序时，应依据实际打包版本补齐许可、署名和相应源码。

本项目原创部分采用 GNU Affero General Public License 第 3 版（`AGPL-3.0-only`），完整文本见根目录 `LICENSE`。该许可允许商业使用，须遵守适用的开源及相应源码交付等义务。下列第三方代码继续适用各自许可证；本项目的根许可证不替代或删除它们的原始许可与署名。

## tzwkb/wechat-decrypt

- 来源：<https://github.com/tzwkb/wechat-decrypt>
- 基准 commit：`f0b3f9f95dda2d954ee3dbe1d139fb1944a5ecfd`
- 存放目录：`vendor/wechat-decrypt`
- 许可：MIT；原始文本同时保存在 `vendor/wechat-decrypt/LICENSE`。

此目录是上游的最小运行时子集，并含本项目的修改，涉及消息解析、数据库读取、Windows 数据库解密与密钥提取适配；不是未经修改的上游完整发行版。

```text
MIT License

Copyright (c) 2026 cocofeng

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## weflow-cli

- 来源：<https://github.com/zhuobichen/weflow-cli>
- 参考 commit：`5ad2fca0c9156224a1a69e4c13b9f81752b6459b`
- 参考文件：[`scripts/wechat_emoticon.py`](https://github.com/zhuobichen/weflow-cli/blob/5ad2fca0c9156224a1a69e4c13b9f81752b6459b/scripts/wechat_emoticon.py)
- 用途：本地表情和媒体格式处理参考，相关适配位于 `wechat_exporter/media.py`。
- 许可：MIT；下文按该固定 commit 的 [LICENSE](https://github.com/zhuobichen/weflow-cli/blob/5ad2fca0c9156224a1a69e4c13b9f81752b6459b/LICENSE) 保留全文，版权行与上游一致。

```text
MIT License

Copyright (c) 2026

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## PyMuPDF / MuPDF

`wechat_exporter/media.py` 使用 PyMuPDF 生成本地 PDF 附件的首页预览。PyMuPDF 与 MuPDF 采用 GNU Affero General Public License 3.0 或 Artifex 商业许可，具体以实际版本附带的准确文本及授权为准。

本项目选择 AGPL 开源分发路线。保留该依赖并分发组合程序时，必须按适用的 AGPL 条件提供相应权利及源码。仅提供项目自身的 Python 文件不等同于已经提供随包 PyMuPDF、MuPDF 的完整相应源码。

当前验证环境报告 PyMuPDF `1.27.2.2`、MuPDF `1.27.2`。以下是这些准确版本的官方源码入口：

- [PyMuPDF 1.27.2.2 源码包](https://files.pythonhosted.org/packages/f1/32/f6b645c51d79a188a4844140c5dabca7b487ad56c4be69c4bc782d0d11a9/pymupdf-1.27.2.2.tar.gz)，PyPI 公布的 SHA-256 为 `ea8fdc3ab6671ca98f629d5ec3032d662c8cf1796b146996b7ad306ac7ed3335`。
- [MuPDF 1.27.2 完整源码包](https://mupdf.com/downloads/archive/mupdf-1.27.2-source.tar.gz)，该地址来自上述 PyMuPDF 版本的 `setup.py`。
- [PyMuPDF 1.27.2.2 COPYING](https://github.com/pymupdf/PyMuPDF/blob/1.27.2.2/COPYING) 与 [MuPDF 1.27.2 COPYING](https://github.com/ArtifexSoftware/mupdf/blob/1.27.2/COPYING)。

Release 应记录实际随包版本，并在可执行程序下载链接旁提供本项目该次构建的准确源码及这些依赖的对应源码下载指引。AGPL 第 6(d) 条允许对应源码由具有同等下载条件的其他服务器提供，但分发者仍须确保其按要求持续可得；不能仅链接项目首页或一个不确定版本的分支。版本号相符本身也不能代替对实际构建修改及相应源码完整性的核对。

本次验证的 PyMuPDF 安装元数据中，`pymupdf-1.27.2.2.dist-info/COPYING` 仅含一行双许可说明，不能用它代替完整 AGPL 文本。Release 应附完整 AGPL 文本及适用的版权和第三方声明，并从上述源码保留其相应许可材料。

- [PyMuPDF 官方许可说明](https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright)
- [GNU AGPL 3.0 全文](https://www.gnu.org/licenses/agpl-3.0.html)
- [Artifex 商业许可](https://artifex.com/licensing/)

## imageio-ffmpeg / FFmpeg

本项目通过 `imageio-ffmpeg` 调用独立的 FFmpeg 进程处理本地媒体。`imageio-ffmpeg` Python 包装器采用 BSD-2-Clause，部分平台的 wheel 内含 FFmpeg 可执行文件；包装器许可不替代该可执行文件及其组件的许可。

本项目的 Release 构建不内置 FFmpeg，可由用户自行安装并通过 PATH 或 `IMAGEIO_FFMPEG_EXE` 指定。仅调用用户已有的外部程序不等于本项目向用户分发该二进制。

本项目验证环境中的 `imageio-ffmpeg 0.6.0` 所带 Windows 二进制为 FFmpeg `7.1-essentials_build-www.gyan.dev`，构建参数含 `--enable-gpl --enable-version3 --enable-static`，其 `-L` 输出声明 GPL 第 3 版或更新版本。未来安装的包或替换的二进制可能不同，打包前须重新核对实际构建。

若再次分发上述 FFmpeg 二进制，应随分发保留 GPL-3.0-or-later 许可及版权声明，并按 GPL 要求提供该实际构建对应的完整源码、修改及所需构建资料；仅链接 FFmpeg 首页或附上包装器 BSD 许可不充分。独立子进程调用本身不足以确定整个 Python 应用的许可，PyMuPDF 的许可要求仍应单独处理。

- [imageio-ffmpeg 来源与许可证](https://github.com/imageio/imageio-ffmpeg)
- [FFmpeg 官方许可与分发说明](https://ffmpeg.org/legal.html)
- [GNU GPL 3.0 全文](https://www.gnu.org/licenses/gpl-3.0.html)

## 其他主要依赖

以下依赖通过 `requirements.txt` 安装，不将其包内容作为本次源码发布的一部分：

- Frida：wxWindows Library Licence 3.1，含其附带的例外条款。
- PyCryptodome：包含 BSD 许可及 public-domain 部分；发布随包版本时保留其 `LICENSE.rst`、`AUTHORS.rst`。
- zstandard：BSD-3-Clause。
- Pillow：许可标识随版本可能为 HPND 或 MIT-CMU，以安装版本附带文本为准。
- pywin32：PSF 许可及其随包组件的独立许可。

Pandoc、XeLaTeX/TeX 发行版、字体和 Microsoft Office 由用户单独安装，各自受其许可约束。本项目不附带这些程序、字体或其使用授权。开发依赖与构建工具也应按实际发布内容核对，不能将此简表用作二进制发行的完整许可清单。

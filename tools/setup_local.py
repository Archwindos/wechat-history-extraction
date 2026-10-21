"""Capture a local key once, without storing any personal defaults in source."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wechat_exporter.backend import Engine


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", required=True,
                        help="微信设置中显示的 xwechat_files 目录，不是账号或 db_storage 子目录")
    parser.add_argument("--account", help="多个本地账号时，指定 xwechat_files 下的账号目录名")
    args = parser.parse_args()
    engine = Engine()
    source = Path(args.data_root).expanduser().resolve()
    accounts = engine.accounts(source)
    if not accounts:
        parser.error("找不到消息数据库，请核对数据目录，并先登录微信。")
    account = args.account
    if not account:
        if len(accounts) != 1:
            print("找到多个账号，请加 --account 指定：")
            for candidate in accounts:
                print(candidate)
            return 2
        account = accounts[0]
    if account not in accounts:
        parser.error("指定账号不在该数据目录中。")
    print("提取器会关闭微信；请随后从桌面重新打开微信并完成登录。", flush=True)
    engine.capture_key(source, account, log=lambda line: print(line, flush=True))
    engine.settings.update(source_root=str(source), account=account)
    engine.save_settings()
    print("本机密钥已保存到 private-data。现在可以运行 python app.py。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

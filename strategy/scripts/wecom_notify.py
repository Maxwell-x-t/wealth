#!/usr/bin/env python3
"""独立的企业微信群机器人推送脚本。

凭证从环境变量 WECOM_WEBHOOK_URL 读取（可用 --webhook 覆盖）。

用法：
    python scripts/wecom_notify.py --title "红利网格提醒" --content "**测试**正文"
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dividend_grid.notifiers import WeComWebhookNotifier  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="企业微信群机器人推送")
    p.add_argument("--title", required=True)
    p.add_argument("--content", required=True)
    p.add_argument("--webhook", default=None, help="覆盖 WECOM_WEBHOOK_URL")
    args = p.parse_args()
    try:
        WeComWebhookNotifier(webhook_url=args.webhook).send(args.title, args.content)
    except Exception as e:  # noqa: BLE001
        print(f"推送失败: {e}", file=sys.stderr)
        return 1
    print("已推送到企业微信。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

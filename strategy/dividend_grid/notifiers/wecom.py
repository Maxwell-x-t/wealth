"""企业微信群机器人 Webhook 通知通道。

凭证从环境变量 WECOM_WEBHOOK_URL 读取（完整 webhook 地址，或只给 key）。
不硬编码、不入库。
"""

from __future__ import annotations

import os
from typing import Optional


DEFAULT_BASE = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key="
MAX_MARKDOWN_BYTES = 3800


def _split_text(text: str, max_bytes: int) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    current_bytes = 0
    for char in text:
        char_bytes = len(char.encode("utf-8"))
        if current and current_bytes + char_bytes > max_bytes:
            chunks.append("".join(current))
            current = []
            current_bytes = 0
        current.append(char)
        current_bytes += char_bytes
    if current or not chunks:
        chunks.append("".join(current))
    return chunks


def _split_content(title: str, content: str) -> list[str]:
    prefix = f"# {title}\n"
    max_content_bytes = MAX_MARKDOWN_BYTES - len(prefix.encode("utf-8"))
    chunks: list[str] = []
    current = ""
    for line in content.splitlines(keepends=True):
        if len(line.encode("utf-8")) > max_content_bytes:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_split_text(line, max_content_bytes))
        elif current and len((current + line).encode("utf-8")) > max_content_bytes:
            chunks.append(current)
            current = line
        else:
            current += line
    if current or not chunks:
        chunks.append(current)
    return chunks


class WeComWebhookError(RuntimeError):
    pass


class WeComWebhookNotifier:
    def __init__(self, webhook_url: Optional[str] = None, timeout: int = 15):
        url = webhook_url or os.environ.get("WECOM_WEBHOOK_URL", "")
        url = url.strip()
        if not url:
            raise WeComWebhookError(
                "缺少企业微信 Webhook 地址：请设置环境变量 WECOM_WEBHOOK_URL"
            )
        # 允许只传 key
        if not url.startswith("http"):
            url = DEFAULT_BASE + url
        self._url = url
        self._timeout = timeout

    def send(self, title: str, content: str) -> None:
        import requests

        # 企业微信 markdown 单条限制约 4096 字节，长网格提醒拆成多条发送。
        for chunk in _split_content(title, content):
            body = f"# {title}\n{chunk}"
            payload = {"msgtype": "markdown", "markdown": {"content": body}}
            resp = requests.post(self._url, json=payload, timeout=self._timeout)
            resp.raise_for_status()
            data = resp.json()
            if data.get("errcode", 0) != 0:
                raise WeComWebhookError(
                    f"企业微信推送失败: errcode={data.get('errcode')} "
                    f"errmsg={data.get('errmsg')}"
                )

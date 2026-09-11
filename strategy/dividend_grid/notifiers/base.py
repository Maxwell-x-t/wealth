"""通知通道抽象。"""

from __future__ import annotations

from typing import Protocol


class Notifier(Protocol):
    def send(self, title: str, content: str) -> None:
        """发送一条提醒。content 为 markdown 文本。"""
        ...

"""控制台 / 日志通知通道（跨平台，默认）。"""

from __future__ import annotations

import sys


class ConsoleNotifier:
    def __init__(self, stream=None):
        self._stream = stream or sys.stdout

    def send(self, title: str, content: str) -> None:
        line = "=" * 48
        print(line, file=self._stream)
        print(f"[提醒] {title}", file=self._stream)
        print(line, file=self._stream)
        print(content, file=self._stream)
        print(line, file=self._stream)
        self._stream.flush()

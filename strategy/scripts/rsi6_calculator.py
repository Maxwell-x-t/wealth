"""512890 周 RSI(6) 层仓提醒入口。"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dividend_grid.rsi6_cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())

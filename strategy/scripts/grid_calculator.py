#!/usr/bin/env python3
"""兼容入口：等价于 `python -m dividend_grid.cli`。

用法示例：
    python scripts/grid_calculator.py --stocks "sz000538:云南白药:5.26" "sh600795:国电电力:4.79"
    python scripts/grid_calculator.py --source akshare --stocks "sh600690:海尔智家" --notify wecom
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dividend_grid.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())

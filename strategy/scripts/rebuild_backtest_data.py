"""用已缓存的原始响应重建回测数据集，不联网。

当前用途：修正财报 `published` 日期。上游 `publish_date` 对较早的报告期会多算一年，
导致基本面整体滞后 12 个月，并在错位区交界处造成每年数月的 EPS 空窗。
本脚本按 `update_time` 重新解析缓存中的 `raw/*.json`。

用法：
    python scripts/rebuild_backtest_data.py --source backtests/data-20260909 \
        --output backtests/data-20260910
"""

import argparse
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dividend_grid.backtest_data import rebuild_financials


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path("backtests/data-20260909"))
    parser.add_argument("--output", type=Path, default=Path("backtests/data-20260910"))
    args = parser.parse_args()

    if args.source.resolve() == args.output.resolve():
        parser.error("--output 必须与 --source 不同，避免覆盖原始缓存")
    if args.output.exists():
        raise SystemExit(f"{args.output} 已存在，请先移除或换一个输出目录")

    shutil.copytree(args.source, args.output)
    results = rebuild_financials(args.output)
    corrected = sum(row["corrected"] for row in results)
    reports = sum(row["reports"] for row in results)
    print(f"已重建 {len(results)} 只标的的财报，修正 {corrected} / {reports} 条披露日期")
    for row in sorted(results, key=lambda item: -item["corrected"]):
        if row["corrected"]:
            print(f'  {row["code"]}  修正 {row["corrected"]:3d} / {row["reports"]} 期')
    print(f"输出目录：{args.output}")


if __name__ == "__main__":
    main()

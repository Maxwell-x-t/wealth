"""就地修复回测数据集的日线坏点，不联网。

腾讯行情接口偶发单日坏数据（实测 `sh510880` 的 2008-01-02 被记成 1.467，
前后两个交易日分别是 4.637 与 4.794），会在收益序列里伪造一次 -68%/+227% 的
往返，并让周线 RSI 误判为超卖而触发假买入。本脚本用缓存中的 `raw/*.json`
重新解析日线并剔除这类孤立跳变。

用法：
    python scripts/repair_backtest_prices.py --data backtests/data-bear
"""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dividend_grid.backtest_data import rebuild_prices


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=Path("backtests/data-bear"))
    args = parser.parse_args()
    results = rebuild_prices(args.data)
    dropped = [row for row in results if row["dropped"]]
    print(json.dumps({"assets": len(results), "assets_with_dropped": len(dropped),
                      "dropped_total": sum(row["dropped"] for row in results)},
                     ensure_ascii=False))
    for row in dropped:
        print(f'  {row["code"]}  剔除 {row["dropped"]} 条坏点，剩余 {row["bars"]} 条日线')


if __name__ == "__main__":
    main()

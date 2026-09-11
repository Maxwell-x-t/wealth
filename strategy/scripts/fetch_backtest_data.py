"""Fetch immutable public inputs; never accesses live holdings or notifiers."""

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dividend_grid.backtest_data import download_dataset


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2017-01-01")
    parser.add_argument("--end", required=True)
    parser.add_argument("--watchlist", type=Path, default=Path("watchlist.json"))
    parser.add_argument("--output", type=Path, default=Path("backtests/data"))
    parser.add_argument("--etf-code", default="sh512890")
    parser.add_argument("--etf-name", default="红利低波ETF")
    args = parser.parse_args()
    download_dataset(args.watchlist, args.output, args.start, args.end,
                     etf_code=args.etf_code, etf_name=args.etf_name)

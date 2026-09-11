"""Run offline comparisons from the cached public dataset."""

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dividend_grid.backtest import Costs, History, SCENARIOS, Simulation, dataset_fingerprint
from dividend_grid.grid import GroupThresholds
from dividend_grid.portfolio import load_limits
from dividend_grid.quality import load_quality_config
from dividend_grid.coverage import CoverageConfig
from dividend_grid.rsi6 import DEFAULT_RSI_PARAMS


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=Path("backtests/data-coverage-20260911"))
    parser.add_argument("--output", type=Path, default=Path("backtests/results-coverage-20260911"))
    parser.add_argument("--start", default="2019-01-01")
    parser.add_argument("--end", default="2026-09-10")
    parser.add_argument("--capital", type=float, default=1_000_000)
    parser.add_argument("--slippage", type=float, default=0.0005)
    parser.add_argument("--no-dividend-tax", action="store_true")
    parser.add_argument("--dividend-delay", type=int, default=5)
    parser.add_argument("--scenarios", nargs="+", choices=[s.key for s in SCENARIOS], default=None)
    args = parser.parse_args()
    if args.capital <= 0 or not 0 <= args.slippage < 0.1 or args.dividend_delay < 0:
        parser.error("Invalid capital, slippage or dividend delay")
    history_cache: dict[str, History] = {}

    def history_for(caliber: str) -> History:
        if caliber not in history_cache:
            history_cache[caliber] = History.load(args.data, caliber=caliber)
        return history_cache[caliber]

    raw_groups = json.loads(Path("strategy_groups.json").read_text(encoding="utf-8"))
    groups = {group: GroupThresholds(tuple(value["buy"]), tuple(value["sell"])) for group, value in raw_groups.items()}
    limits = load_limits(Path("portfolio_rules.json"))
    quality = load_quality_config(json.loads(Path("quality_rules.json").read_text(encoding="utf-8")))
    costs = Costs(slippage=args.slippage, dividend_tax=not args.no_dividend_tax)
    args.output.mkdir(parents=True, exist_ok=True)
    summaries = []
    for scenario in SCENARIOS:
        if args.scenarios and scenario.key not in args.scenarios:
            continue
        result = Simulation(history_for(scenario.caliber), scenario, groups, limits, args.start,
                            args.end, args.capital, costs, args.dividend_delay,
                            quality_config=quality).run()
        summaries.append(result["metrics"])
        (args.output / f"{scenario.key}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        print(json.dumps(result["metrics"], ensure_ascii=False), flush=True)
    audit = history_for("fiscal").audit
    output = {"configuration": {"start": args.start, "end": args.end, "capital": args.capital,
                                "costs": asdict(costs), "limits": asdict(limits), "groups": raw_groups,
                                "dps_caliber": "fiscal 财年口径（修正）",
                                "quality": asdict(quality), "coverage": asdict(CoverageConfig()),
                                "rsi": asdict(DEFAULT_RSI_PARAMS),
                                "limitations": ["使用当前清单和阈值回看历史，存在选股及事后设定规则偏差",
                                    "公开财报为当前可下载版本，不能排除历史重述；披露日期使用接口元数据",
                                    "没有保险历史人工复核则保持未知并禁止新增买入",
                                    "未提供历史ETF行业权重，不进行行业穿透；现金不计息",
                                    "分红总额由每股分红乘接口总股本估算，回购专户等差异未单独重建"],
                                "dividend_delay_trading_days": args.dividend_delay,
                                "dataset_sha256": dataset_fingerprint(args.data)},
              "results": summaries, "data_audit": audit}
    (args.output / "summary.json").write_text(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


if __name__ == "__main__":
    main()

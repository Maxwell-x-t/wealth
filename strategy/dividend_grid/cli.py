"""命令行入口：取数 → 网格计算 → 输出建议表 →（可选）推送提醒。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from .models import Stock
from .grid import GridConfig, evaluate_portfolio
from .datasource import make_source, apply_overrides
from .quality import assess as assess_quality, load_quality_config
from .coverage import CoverageConfig, CoverageMetrics, apply_coverage_review, evaluate_coverage
from .report import render_table, build_dividend_message, quote_yield_warnings
from .notifiers import ConsoleNotifier, WeComWebhookNotifier
from .portfolio import account_path, load_limits, load_etf_layers, value_account
from .dividends import DividendUnavailable


def _default_path(filename: str) -> Path:
    """技能根目录（当前工作目录）下的配置文件。"""
    return Path.cwd() / filename


def _load_json(path: Path) -> dict:
    if path.exists():
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    return {}


# 个股规格：(代码, 名称, 股息率或None, 权重或None, 行业分组)
StockSpec = tuple[str, str, Optional[float], Optional[float], str]


def _parse_stock_arg(raw: str) -> StockSpec:
    """解析 '代码:名称[:股息率%[:权重[:分组]]]'。股息率可省略（akshare 数据源时）。"""
    parts = raw.split(":")
    if len(parts) < 2:
        raise ValueError(f"--stocks 格式错误: {raw}（至少需要 代码:名称）")
    code, name = parts[0], parts[1]
    dy = float(parts[2]) if len(parts) >= 3 and parts[2] != "" else None
    weight = float(parts[3]) if len(parts) >= 4 and parts[3] != "" else None
    group = parts[4] if len(parts) >= 5 and parts[4] != "" else "default"
    return code, name, dy, weight, group


def _load_watchlist(path: Path) -> list[StockSpec]:
    """读取监控清单文件。支持三种格式：
    - {"代码": "名称", ...}
    - {"代码": {"name": "名称", "group": "行业", "weight": 可选}, ...}
    - [{"code": "代码", "name": "名称", "group": "行业", "weight": 可选}, ...]
    股息率一律留空（由数据源获取）。
    """
    data = _load_json(path)
    out: list[StockSpec] = []
    if isinstance(data, dict):
        for code, val in data.items():
            if isinstance(val, dict):
                name = str(val.get("name", code))
                group = str(val.get("group", "default"))
                weight = float(val["weight"]) if val.get("weight") is not None else None
            else:
                name, group, weight = str(val), "default", None
            out.append((str(code), name, None, weight, group))
    elif isinstance(data, list):
        for item in data:
            code = str(item["code"])
            name = str(item.get("name", code))
            group = str(item.get("group", "default"))
            weight = float(item["weight"]) if item.get("weight") is not None else None
            out.append((code, name, None, weight, group))
    else:
        raise ValueError(f"监控清单格式不支持: {path}")
    return out


def _load_groups(path: Path) -> dict:
    """读取分组阈值配置。返回 {分组名: GroupThresholds}，始终包含 default。"""
    from .grid import GroupThresholds, DEFAULT_BUY, DEFAULT_SELL

    raw = _load_json(path)
    groups: dict[str, GroupThresholds] = {}
    for name, cfg in raw.items():
        buy = cfg.get("buy")
        sell = cfg.get("sell")
        if not (buy and sell and len(buy) == 3 and len(sell) == 3):
            raise ValueError(f"分组 {name} 配置需含 3 个 buy 与 3 个 sell 阈值")
        groups[name] = GroupThresholds(
            buy=(float(buy[0]), float(buy[1]), float(buy[2])),
            sell=(float(sell[0]), float(sell[1]), float(sell[2])),
        )
    groups.setdefault("default", GroupThresholds(buy=DEFAULT_BUY, sell=DEFAULT_SELL))
    return groups


def _parse_holdings_arg(items: list[str]) -> dict[str, float]:
    result: dict[str, float] = {}
    for it in items:
        code, _, shares = it.partition(":")
        if not shares:
            raise ValueError(f"--holdings 格式错误: {it}（应为 代码:份数）")
        result[code] = float(shares)
    return result


def _coverage_for(code: str, dividend_yield: float, metrics, cfg: CoverageConfig) -> CoverageMetrics:
    """抓取分红可持续性指标（FCF 覆盖 / 银行监管指标）。

    取数失败不致命：降级为 unknown 并记录原因，保证定时任务仍然出表。
    """
    try:
        # Fiscal DPS is not a trailing cash-flow denominator. Require dated annual totals.
        return evaluate_coverage(code, None, metrics.market_cap if metrics else None, cfg)
    except Exception as e:  # noqa: BLE001
        return CoverageMetrics(kind="unknown", flag="unknown", note=f"覆盖度取数失败: {e}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="dividend-grid",
        description="红利网格仓位管理与提醒（纯个股版）",
    )
    p.add_argument("--json", action="store_true", help="输出结构化策略结果")
    p.add_argument(
        "--stocks",
        nargs="+",
        default=None,
        help="个股清单，格式 代码:名称[:股息率%%[:权重]]，可多个。"
        "manual 数据源必须带股息率；akshare 数据源可省略股息率。"
        "与 --stocks-file 至少提供一个。",
    )
    p.add_argument(
        "--stocks-file",
        default=None,
        help="监控清单文件（JSON，{\"代码\": \"名称\"}），akshare 数据源自动取股息率。"
        "用于定时任务，避免把标的写死在命令里。",
    )
    p.add_argument(
        "--holdings",
        nargs="*",
        default=None,
        help="当前持仓，格式 代码:份数(0-12)。不传则读 holdings.json。",
    )
    p.add_argument("--holdings-file", default=None, help="持仓配置文件（默认 ./holdings.json）")
    p.add_argument("--account-file", default=None, help="实际股数和现金账户；默认自动检测 account.json")
    p.add_argument(
        "--yield-overrides-file",
        default=None,
        help="股息率覆盖文件（默认 ./dividend_overrides.json）",
    )
    p.add_argument("--pool", type=float, default=0.12, help="单只满仓净值比例（默认 0.12）")
    p.add_argument("--portfolio-file", default="portfolio_rules.json", help="统一资金限制")
    p.add_argument("--etf-holdings-file", default="rsi6_holdings.json", help="ETF 当前层数")
    p.add_argument(
        "--groups-file",
        default=None,
        help="行业分组阈值配置（默认 ./strategy_groups.json）。未配置的分组回退到 default。",
    )
    p.add_argument(
        "--quality-file",
        default=None,
        help="分红质量规则配置（默认 ./quality_rules.json）。含派息率阈值与是否否决买入。",
    )
    p.add_argument(
        "--source",
        choices=["manual", "akshare"],
        default="manual",
        help="股息率数据源（默认 manual）",
    )
    p.add_argument(
        "--notify",
        choices=["none", "console", "wecom"],
        default="none",
        help="提醒通道（默认 none，仅打印建议表）",
    )
    p.add_argument(
        "--notify-on",
        choices=["signal", "always"],
        default="signal",
        help="推送触发条件：signal=有买卖信号才推（默认），always=每次都推",
    )
    p.add_argument(
        "--no-coverage",
        action="store_true",
        help="跳过分红可持续性取数（FCF 覆盖 / 银行监管指标），用于离线或快速运行",
    )
    return p


def _main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    # 行业分组阈值配置
    groups_path = (
        Path(args.groups_file)
        if args.groups_file
        else _default_path("strategy_groups.json")
    )
    groups = _load_groups(groups_path)

    # 分红质量规则
    quality_path = (
        Path(args.quality_file)
        if args.quality_file
        else _default_path("quality_rules.json")
    )
    qcfg = load_quality_config(_load_json(quality_path))
    ccfg = CoverageConfig()
    limits = load_limits(Path(args.portfolio_file))
    actual_path = account_path(args.account_file, legacy_override=(args.holdings is not None or args.holdings_file is not None))
    etf_layers = 0 if actual_path else load_etf_layers(Path(args.etf_holdings_file))

    cfg = GridConfig(
        default_weight=args.pool,
        groups=groups,
        veto_buy_on_risk=qcfg.veto_buy_on_risk,
        veto_buy_on_unknown=qcfg.veto_buy_on_unknown,
        veto_buy_on_coverage_risk=qcfg.veto_buy_on_coverage_risk,
        veto_buy_on_coverage_unknown=qcfg.veto_buy_on_coverage_unknown,
        exit_reasons=qcfg.exit_reasons or {},
        min_cash_pct=limits.min_cash_pct,
        max_industry_pct=limits.max_industry_pct,
        industry_limits=limits.industry_limits,
        external_position_pct=etf_layers * limits.layer_pct,
        reserved_cash_pct=(10 - etf_layers) * limits.layer_pct,
    )

    # 解析个股：--stocks（命令行）+ --stocks-file（监控清单文件）
    parsed: list[StockSpec] = []
    if args.stocks:
        parsed.extend(_parse_stock_arg(s) for s in args.stocks)
    if args.stocks_file:
        parsed.extend(_load_watchlist(Path(args.stocks_file)))
    if not parsed:
        print("错误：请通过 --stocks 或 --stocks-file 至少提供一个标的。", file=sys.stderr)
        return 2
    manual_yields = {c: dy for c, _, dy, _, _ in parsed if dy is not None}

    # 数据源
    source = make_source(args.source, manual_yields)
    account = value_account(actual_path, parsed, source.get_price, args.pool) if actual_path else None
    if account:
        cfg.external_position_pct = account.external_position_pct
        cfg.external_industry_pct = account.external_industry_pct
        cfg.reserved_cash_pct = max(limits.etf_budget_pct - account.etf_pct, 0.0)
        # Existing excess exposure is reported; new buys still face the cash floor.

    # 覆盖文件
    overrides_path = (
        Path(args.yield_overrides_file)
        if args.yield_overrides_file
        else _default_path("dividend_overrides.json")
    )
    overrides = _load_json(overrides_path)

    # 持仓
    if account:
        holdings = account.holdings
    elif args.holdings is not None:
        holdings = _parse_holdings_arg(args.holdings)
    else:
        holdings_path = (
            Path(args.holdings_file)
            if args.holdings_file
            else _default_path("holdings.json")
        )
        if not holdings_path.exists():
            raise ValueError(f"持仓文件不存在，不能假设空仓：{holdings_path}")
        holdings = {str(k): float(v) for k, v in _load_json(holdings_path).items()}

    if any(c == "sh512890" for c, *_ in parsed) or "sh512890" in holdings:
        raise ValueError("512890 必须通过独立层数文件计入，不能重复放入个股清单或持仓")

    # 构建 Stock 列表（取数 + 覆盖）。单只取数失败时跳过并告警，保证定时任务健壮。
    stocks: list[Stock] = []
    for code, name, dy, weight, group in parsed:
        try:
            price = source.get_price(code)
            base_yield, base_source = ((0.0, "人工分红") if code in overrides else source.get_yield(code))
            y, src, overridden = apply_overrides(
                code, base_yield, base_source, overrides, price=price,
            )
            quote_yield = None
            quote_getter = getattr(source, "get_quote_yield", None)
            if callable(quote_getter) and not overridden:
                quote_yield = quote_getter(code)
        except DividendUnavailable as e:
            stocks.append(Stock(code=code, name=name, dividend_yield=0.0,
                                weight=weight if weight is not None else args.pool,
                                group=group, price=price, dividend_available=False,
                                dividend_note=str(e)))
            continue
        except Exception as e:  # noqa: BLE001
            print(f"[警告] {name}({code}) 数据不可用，保留持仓占用: {e}", file=sys.stderr)
            stocks.append(Stock(code=code, name=name, dividend_yield=0.0,
                                weight=weight if weight is not None else args.pool,
                                group=group, data_available=False, quality_note=str(e)))
            continue
        metrics = source.get_quality(code)
        # 口径展示：仅在未走人工覆盖时显示财年口径的财年与每股分红
        snapshot_of = getattr(source, "get_dividend_snapshot", None)
        snapshot = snapshot_of(code) if callable(snapshot_of) else None
        if metrics is not None and price is not None:
            from dataclasses import replace
            dps = price * y / 100.0
            payout = metrics.payout_ratio
            if payout is not None and metrics.eps and metrics.eps > 0:
                payout = dps / metrics.eps * 100.0
            metrics = replace(metrics, dividend_per_share=dps, payout_ratio=payout)
        qflag, qnote = assess_quality(metrics, qcfg)
        coverage = _coverage_for(code, y, metrics, ccfg) if not args.no_coverage else CoverageMetrics(
            kind="unknown", flag="unknown", note="已按 --no-coverage 跳过")
        coverage = apply_coverage_review(code, coverage, qcfg.coverage_reviews)
        stocks.append(
            Stock(
                code=code,
                name=name,
                dividend_yield=y,
                weight=weight if weight is not None else args.pool,
                yield_source=src,
                overridden=overridden,
                group=group,
                payout_ratio=metrics.payout_ratio if metrics else None,
                quality_flag=qflag,
                quality_note=qnote,
                coverage_kind=coverage.kind,
                coverage_flag=coverage.flag,
                coverage_note=coverage.note,
                fcf_coverage=coverage.ratio,
                fcf_avg_coverage=coverage.fcf_avg_ratio,
                fcf_avg_years=coverage.fcf_avg_years,
                price=price,
                dividend_fiscal_year=None if overridden else getattr(snapshot, "fiscal_year", None),
                dividend_dps=None if overridden else getattr(snapshot, "dps", None),
                quote_yield=quote_yield,
            )
        )

    if not stocks:
        print("错误：所有标的取数失败，无可计算数据。", file=sys.stderr)
        return 1

    result = evaluate_portfolio(stocks, holdings, cfg)
    result.notes.extend(account.notes() if account else [
        "手工仓位估算：当前份数/层数需按最新净值校准；可通过 account.json 启用实际股数估值"])
    result.warnings.extend(f"{s.name}：{s.dividend_note}" for s in stocks if not s.dividend_available)
    result.warnings.extend(quote_yield_warnings(stocks))

    # 输出建议表
    print(json.dumps(asdict(result), ensure_ascii=False) if args.json else render_table(result))

    # 提醒
    if args.notify != "none":
        should = args.notify_on == "always" or bool(result.triggered) or bool(result.warnings)
        if not should:
            print("\n[提醒] 无触发信号，按 --notify-on=signal 未推送。")
            return 0
        title, content = build_dividend_message(result)
        if args.notify == "console":
            ConsoleNotifier().send(title, content)
        elif args.notify == "wecom":
            try:
                WeComWebhookNotifier().send(title, content)
                print("\n[提醒] 已推送到企业微信。")
            except Exception as e:  # noqa: BLE001
                print(f"\n[提醒] 企业微信推送失败: {e}", file=sys.stderr)
                return 1
    return 0 if all(s.data_available and s.dividend_available for s in stocks) else 1


def main(argv: Optional[list[str]] = None) -> int:
    try:
        return _main(argv)
    except (ValueError, TypeError, KeyError, OSError) as exc:
        print(f"[错误] 网格配置或持仓无效：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

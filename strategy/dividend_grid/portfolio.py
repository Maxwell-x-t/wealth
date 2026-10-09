"""Shared capital limits for the dividend grid and ETF sleeve."""

from dataclasses import dataclass, field
from datetime import date
import json
import math
from pathlib import Path
import re
import sqlite3


@dataclass(frozen=True)
class PortfolioLimits:
    min_cash_pct: float = 10.0
    max_industry_pct: float = 30.0
    industry_limits: dict[str, float] = field(default_factory=dict)
    etf_budget_pct: float = 20.0

    def __post_init__(self) -> None:
        for value in (self.min_cash_pct, self.max_industry_pct, self.etf_budget_pct,
                      *self.industry_limits.values()):
            if not math.isfinite(value) or not 0 <= value <= 100:
                raise ValueError("Portfolio limits must be finite percentages in [0, 100]")
        if self.etf_budget_pct <= 0 or self.etf_budget_pct + self.min_cash_pct > 100:
            raise ValueError("ETF budget must be positive and fit alongside the cash reserve")

    @property
    def layer_pct(self) -> float:
        return self.etf_budget_pct / 10.0

    def etf_capacity(self, stock_position_pct: float) -> int:
        if not math.isfinite(stock_position_pct) or not 0 <= stock_position_pct <= 100:
            raise ValueError("Invalid stock position percentage")
        available = max(100.0 - self.min_cash_pct - stock_position_pct, 0.0)
        return min(10, math.floor((available + 1e-9) / self.layer_pct))


def load_limits(path: Path) -> PortfolioLimits:
    with path.open(encoding="utf-8") as handle:
        raw = json.load(handle)
    return PortfolioLimits(**raw)


def save_limits(path: Path, min_cash_pct: float, etf_budget_pct: float) -> PortfolioLimits:
    """只更新现金底线和 ETF 资金上限，保留行业限额。"""
    with path.open(encoding="utf-8") as handle:
        raw = json.load(handle)
    raw["min_cash_pct"] = round(float(min_cash_pct), 2)
    raw["etf_budget_pct"] = round(float(etf_budget_pct), 2)
    try:
        limits = PortfolioLimits(
            min_cash_pct=raw["min_cash_pct"],
            max_industry_pct=float(raw.get("max_industry_pct", 30)),
            industry_limits=dict(raw.get("industry_limits") or {}),
            etf_budget_pct=raw["etf_budget_pct"],
        )
    except ValueError as exc:
        raise ValueError("现金底线与 512890 资金上限须在 0 到 100 之间，资金上限须大于 0，且两者相加不能超过 100%") from exc
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return limits


def load_etf_layers(path: Path) -> int:
    with path.open(encoding="utf-8") as handle:
        raw = json.load(handle)
    value = raw["sh512890"]
    if isinstance(value, bool) or not math.isfinite(float(value)) or int(value) != float(value):
        raise ValueError("ETF holdings must be an integer layer count")
    if not 0 <= int(value) <= 10:
        raise ValueError("ETF holdings must be in [0, 10]")
    return int(value)


def stock_position_pct(holdings: dict, specs: list, default_weight: float = 0.12) -> float:
    weights = {code: weight if weight is not None else default_weight
               for code, _name, _yield, weight, _group in specs}
    total = 0.0
    for code, value in holdings.items():
        shares = float(value)
        if not math.isfinite(shares) or shares < 0:
            raise ValueError(f"Invalid holding: {code}")
        if shares == 0:
            continue
        if code not in weights:
            raise ValueError(f"Holding missing from watchlist: {code}")
        weight = weights[code]
        if not math.isfinite(weight) or not 0 < weight <= 1:
            raise ValueError(f"Invalid weight: {code}")
        total += shares * weight / 12.0 * 100.0
    return total


@dataclass(frozen=True)
class AccountValuation:
    as_of: str
    cash: float
    equity: float
    values: dict[str, float]
    quantities: dict[str, float]
    holdings: dict[str, float]
    etf_layers: int
    etf_industry_weights: dict[str, float]
    external_assets: dict[str, str] = field(default_factory=dict)

    @property
    def etf_pct(self) -> float:
        return self.values.get("sh512890", 0.0) / self.equity * 100

    @property
    def stock_pct(self) -> float:
        return sum(value for code, value in self.values.items()
                   if code != "sh512890" and code not in self.external_assets) / self.equity * 100

    @property
    def other_pct(self) -> float:
        return sum(self.values.get(code, 0) for code in self.external_assets) / self.equity * 100

    @property
    def external_position_pct(self) -> float:
        return self.etf_pct + self.other_pct

    @property
    def external_industry_pct(self) -> dict[str, float]:
        return {group: fraction * self.etf_pct for group, fraction in self.etf_industry_weights.items()}

    def notes(self) -> list[str]:
        result = [f"按实际股数估值：资产{self.equity:,.2f}元，现金{self.cash:,.2f}元；持仓登记日{self.as_of}",
                  "成交、分红到账和送转后需更新账户登记；策略层数不等于实时仓位比例"]
        if self.etf_pct and not self.etf_industry_weights:
            result.append("ETF 行业权重未提供，行业限额尚未穿透 ETF")
        elif self.etf_pct:
            result.append("ETF 已计入行业占用：" + " / ".join(
                f"{group}{pct:.2f}%" for group, pct in self.external_industry_pct.items()))
        if self.other_pct:
            result.append("其他持仓已计入资产与资金占用：" + " / ".join(
                f"{name}({code}) {self.quantities[code]:g}份，市值{self.values[code]:,.2f}元"
                for code, name in self.external_assets.items() if code in self.quantities))
            result.append(f"512890实际仓位{self.etf_pct:.2f}%；其他持仓{self.other_pct:.2f}%；"
                          "其他持仓仅估值，不生成买卖信号，尚未纳入行业穿透")
        return result


def account_path(explicit: str | None, *, legacy_override: bool = False) -> Path | None:
    if explicit and legacy_override:
        raise ValueError("实际账户与手工持仓参数不能同时使用")
    if explicit:
        return Path(explicit)
    candidate = Path("account.json")
    return candidate if candidate.exists() and not legacy_override else None


def read_account(path: Path) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if "wealth_ledger" in raw:
        reference = raw["wealth_ledger"]
        database = (path.parent / reference["database"]).resolve()
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
            row = connection.execute("SELECT current_json FROM strategy_accounts WHERE account_id = ?",
                                     (reference["account_id"],)).fetchone()
        if not row:
            raise ValueError("Wealth 策略账户不存在，停止计算")
        raw = json.loads(row[0])
    return raw


def value_account(path: Path, specs: list, get_price, default_weight: float = 0.12) -> AccountValuation:
    return value_account_data(read_account(path), specs, get_price, default_weight)


def value_account_data(raw: dict, specs: list, get_price, default_weight: float = 0.12) -> AccountValuation:
    as_of = date.fromisoformat(raw["as_of"])
    if as_of > date.today():
        raise ValueError("账户登记日期不能在未来")

    def nonnegative(value, name):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError(f"{name} 必须填写真实的非负数值")
        return float(value)

    cash = nonnegative(raw["cash"], "账户现金")
    weights = {code: weight if weight is not None else default_weight
               for code, _name, _yield, weight, _group in specs}
    if len(weights) != len(specs) or "sh512890" in weights:
        raise ValueError("个股清单不得重复，也不能包含 ETF")
    if any(not math.isfinite(w) or not 0 < w <= 1 for w in weights.values()):
        raise ValueError("个股满仓权重无效")
    external_assets = raw.get("external_assets", {})
    if not isinstance(external_assets, dict) or any(
        not re.fullmatch(r"(?:sh|sz|bj)[0-9]{6}", code)
        or not isinstance(name, str) or not name.strip()
        for code, name in external_assets.items()
    ):
        raise ValueError("其他持仓需按带交易所前缀的代码填写名称")
    if any(code in weights or code == "sh512890" for code in external_assets):
        raise ValueError("其他持仓不能与个股清单或512890策略重复")
    positions = raw["positions"]
    if not isinstance(positions, dict):
        raise ValueError("positions 必须按代码填写实际股数")
    values, quantities = {}, {}
    for code, quantity in positions.items():
        quantity = nonnegative(quantity, f"{code}股数")
        if quantity == 0:
            continue
        if code not in weights and code != "sh512890" and code not in external_assets:
            raise ValueError(f"实际持仓 {code} 未纳入清单，不能漏算资产")
        price = get_price(code)
        if price is None or not math.isfinite(price) or price <= 0:
            raise ValueError(f"{code} 缺少有效估值，停止计算资金限额")
        quantities[code], values[code] = quantity, quantity * price
    equity = cash + sum(values.values())
    if equity <= 0:
        raise ValueError("账户总资产必须大于零")
    layers = raw["etf_layers"]
    if isinstance(layers, bool) or not isinstance(layers, int) or not 0 <= layers <= 10:
        raise ValueError("etf_layers 必须是 0 到 10 的整数策略层数")
    if bool(quantities.get("sh512890")) != bool(layers):
        raise ValueError("ETF 实际份额与策略层数的空仓状态不一致")
    industry = raw.get("etf_industry_weights", {})
    if not isinstance(industry, dict):
        raise ValueError("ETF 行业权重必须是对象")
    industry = {group: nonnegative(fraction, f"ETF {group}权重") for group, fraction in industry.items()}
    if industry and not math.isclose(sum(industry.values()), 1, abs_tol=1e-6):
        raise ValueError("ETF 行业权重必须完整且合计为 1")
    holdings = {code: values.get(code, 0) / equity * 12 / weight for code, weight in weights.items()}
    return AccountValuation(as_of.isoformat(), cash, equity, values, quantities, holdings, layers,
                            industry, external_assets)


def account_etf_capacity(account: AccountValuation, limits: PortfolioLimits, specs: list) -> int:
    maximum = min(limits.etf_budget_pct,
                  max(100 - limits.min_cash_pct - account.stock_pct - account.other_pct, 0))
    for group, fraction in account.etf_industry_weights.items():
        if fraction <= 0:
            continue
        stock_pct = sum(account.values.get(code, 0) for code, _name, _yield, _weight, industry in specs
                        if industry == group) / account.equity * 100
        cap = limits.industry_limits.get(group, limits.max_industry_pct)
        maximum = min(maximum, max(cap - stock_pct, 0) / fraction)
    return min(10, math.floor((maximum + 1e-9) / limits.layer_pct))

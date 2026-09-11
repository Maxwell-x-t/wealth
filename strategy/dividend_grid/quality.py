"""分红质量评估（Phase 1：派息率）。

目的：规避"价值陷阱"——高股息但不可持续（盈利虚增、亏损分红、派息率过高）。

flag 取值：
- "ok"       ✅ 健康
- "warn"     🟡 警示（派息率偏高）
- "risk"     🔴 高风险（派息率>100% 或 亏损分红）
- "unknown"  ⚪ 数据不足（如 manual 数据源）
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Optional

from .datasource import QualityMetrics

FLAG_EMOJI = {"ok": "✅", "warn": "🟡", "risk": "🔴", "unknown": "⚪"}


@dataclass
class QualityConfig:
    payout_warn: float = 80.0   # 派息率 >= 此值 -> 警示
    payout_risk: float = 100.0  # 派息率 > 此值 -> 高风险
    flag_negative_eps: bool = True  # EPS<=0 仍分红 -> 高风险
    veto_buy_on_risk: bool = True  # 高风险暂停买入，等待复核
    veto_buy_on_unknown: bool = True
    veto_buy_on_coverage_risk: bool = True
    veto_buy_on_coverage_unknown: bool = True
    exit_reasons: dict[str, str] | None = None
    coverage_reviews: dict = field(default_factory=dict)


def assess(metrics: Optional[QualityMetrics], cfg: QualityConfig) -> tuple[str, str]:
    """评估分红质量，返回 (flag, note)。"""
    if metrics is None:
        return "unknown", "质量数据不足"

    eps = metrics.eps
    payout = metrics.payout_ratio

    if cfg.flag_negative_eps and eps is not None and eps <= 0:
        return "risk", f"亏损分红(EPS={_fmt_num(eps)})"

    if payout is None or not math.isfinite(payout) or payout < 0:
        return "unknown", "无派息率数据"

    if payout > cfg.payout_risk:
        return "risk", f"派息率{payout:.2f}%>{cfg.payout_risk:.0f}%"
    if payout >= cfg.payout_warn:
        return "warn", f"派息率{payout:.2f}%偏高"
    return "ok", f"派息率{payout:.2f}%"


def emoji(flag: str) -> str:
    return FLAG_EMOJI.get(flag, "")


def _fmt_num(x: float) -> str:
    if abs(x - round(x)) < 1e-9:
        return str(int(round(x)))
    return f"{x:.2f}"


def load_quality_config(raw: dict) -> QualityConfig:
    """从 JSON dict 构造 QualityConfig（缺省用默认值）。"""
    if not raw:
        return QualityConfig()
    reasons = raw.get("exit_reasons", {})
    reviews = raw.get("coverage_reviews", {})
    if not isinstance(reviews, dict) or any(not isinstance(review, dict) for review in reviews.values()):
        raise ValueError("coverage_reviews 必须按代码填写复核对象")
    if not isinstance(reasons, dict) or any(
        not isinstance(reason, str) or not reason.strip() for reason in reasons.values()
    ):
        raise ValueError("exit_reasons 必须逐股填写已确认的退出原因")
    return QualityConfig(
        payout_warn=float(raw.get("payout_warn", 80.0)),
        payout_risk=float(raw.get("payout_risk", 100.0)),
        flag_negative_eps=bool(raw.get("flag_negative_eps", True)),
        veto_buy_on_risk=bool(raw.get("veto_buy_on_risk", True)),
        veto_buy_on_unknown=bool(raw.get("veto_buy_on_unknown", True)),
        veto_buy_on_coverage_risk=bool(raw.get("veto_buy_on_coverage_risk", True)),
        veto_buy_on_coverage_unknown=bool(raw.get("veto_buy_on_coverage_unknown", True)),
        exit_reasons=reasons,
        coverage_reviews=reviews,
    )

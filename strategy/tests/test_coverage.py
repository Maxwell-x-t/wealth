import pytest

from dividend_grid.coverage import (
    BankMetrics, CoverageConfig, assess_bank, assess_fcf, digits, evaluate_coverage,
    fetch_dividends, fiscal_year_fcf, grade_ratio, multi_year_ratio, secucode, ttm_value,
)
from dividend_grid.datasource import AkShareDataSource, parse_tencent_quote


def test_secucode_maps_exchange_prefixes():
    assert secucode("sh601166") == "601166.SH"
    assert secucode("sz000001") == "000001.SZ"
    assert secucode("bj430047") == "430047.BJ"
    assert secucode("600519") == "600519.SH"


def test_digits_strips_exchange_prefix():
    assert digits("sh601166") == "601166"
    assert digits("sz000001") == "000001"
    assert digits("600519") == "600519"
    assert digits("SH600887") == "600887"


def test_ttm_value_takes_annual_report_directly():
    assert ttm_value({"20251231": 130.0}, "20251231") == 130.0


def test_ttm_value_rolls_forward_from_interim():
    records = {"20241231": 100.0, "20250630": 40.0, "20251231": 130.0, "20260630": 55.0}
    assert ttm_value(records, "20260630") == pytest.approx(145.0)


def test_ttm_value_requires_matching_history():
    assert ttm_value({"20260630": 55.0}, "20260630") is None
    assert ttm_value({}, "20260630") is None
    assert ttm_value({"20241231": 100.0, "20260630": 55.0}, "20260630") is None


def test_assess_fcf_grades_against_thresholds():
    cfg = CoverageConfig()
    assert assess_fcf(200.0, 100.0, cfg)[1] == "ok"
    assert assess_fcf(120.0, 100.0, cfg)[1] == "warn"
    assert assess_fcf(80.0, 100.0, cfg)[1] == "risk"


def test_assess_fcf_handles_missing_inputs():
    cfg = CoverageConfig()
    assert assess_fcf(None, 100.0, cfg)[1] == "unknown"
    assert assess_fcf(200.0, None, cfg)[1] == "unknown"
    assert assess_fcf(200.0, 0.0, cfg)[1] == "unknown"


def test_assess_fcf_negative_fcf_is_risk():
    ratio, flag, _ = assess_fcf(-30.0, 90.0, CoverageConfig())
    assert flag == "risk" and ratio == pytest.approx(-30.0 / 90.0)


def test_assess_bank_reports_the_worst_of_three_metrics():
    assert assess_bank(BankMetrics(cet1=16.59, npl=0.94, provision=385.1))[0] == "ok"
    assert assess_bank(BankMetrics(cet1=8.0, npl=0.94, provision=385.1))[0] == "risk"
    assert assess_bank(BankMetrics(cet1=11.0, npl=1.2, provision=180.0))[0] == "warn"
    assert assess_bank(BankMetrics(cet1=11.0, npl=1.2, provision=385.1))[0] == "ok"
    assert assess_bank(BankMetrics())[0] == "unknown"


def test_evaluate_coverage_prefers_bank_branch(monkeypatch):
    monkeypatch.setattr("dividend_grid.coverage.fetch_bank_metrics",
                        lambda code, **kw: BankMetrics(report="2026中报", announced="2026-08-28",
                                                       car=14.21, cet1=10.66, npl=1.08, provision=225.67))
    cashflow_calls: list[str] = []
    monkeypatch.setattr("dividend_grid.coverage.fetch_cashflow",
                        lambda code, **kw: cashflow_calls.append(code) or {})
    result = evaluate_coverage("sh601166", 5.84, 3.86011e11)
    assert result.kind == "bank"
    assert result.flag == "ok"
    assert "核心一级 10.66%" in result.note
    assert cashflow_calls == []  # 银行不应再去抓现金流


def test_evaluate_coverage_computes_fcf_ratio(monkeypatch):
    monkeypatch.setattr("dividend_grid.coverage.fetch_bank_metrics", lambda code, **kw: None)
    monkeypatch.setattr("dividend_grid.coverage.fetch_cashflow", lambda code, **kw: {
        "20241231": {"ocf": 100.0, "capex": 20.0},
        "20250630": {"ocf": 40.0, "capex": 10.0},
        "20251231": {"ocf": 130.0, "capex": 30.0},
        "20260630": {"ocf": 55.0, "capex": 15.0},
    })
    # 无多年分红数据时回退单窗口 TTM：
    # TTM ocf = 55 + 130 - 40 = 145；TTM capex = 15 + 30 - 10 = 35 → FCF 110
    # 分红 = 股息率 5% × 总市值 2000 = 100 → 1.10x → warn
    monkeypatch.setattr("dividend_grid.coverage.fetch_dividends", lambda code, **kw: {})
    result = evaluate_coverage("sz000423", 5.0, 2000.0)
    assert result.kind == "fcf"
    assert result.fcf_ttm == pytest.approx(110.0)
    assert result.dividend_ttm == pytest.approx(100.0)
    assert result.ratio == pytest.approx(1.1)
    assert result.fcf_avg_ratio is None
    assert result.flag == "warn"


def test_evaluate_coverage_without_market_cap_is_unknown(monkeypatch):
    monkeypatch.setattr("dividend_grid.coverage.fetch_bank_metrics", lambda code, **kw: None)
    monkeypatch.setattr("dividend_grid.coverage.fetch_cashflow", lambda code, **kw: {
        "20251231": {"ocf": 130.0, "capex": 30.0}})
    monkeypatch.setattr("dividend_grid.coverage.fetch_dividends", lambda code, **kw: {})
    result = evaluate_coverage("sz000423", 5.0, None)
    assert result.kind == "fcf"
    assert result.ratio is None
    assert result.flag == "unknown"


# --- 多年合计 FCF ÷ 分红 ---------------------------------------------------

def _cashflow(*pairs):
    """pairs: (year, ocf, capex) → {YYYY1231: {...}}。"""
    return {f"{y}1231": {"ocf": ocf, "capex": capex} for y, ocf, capex in pairs}


def test_fiscal_year_fcf_subtracts_capex():
    cf = _cashflow((2024, 100.0, 20.0), (2025, 130.0, 30.0))
    assert fiscal_year_fcf(cf, 2025) == pytest.approx(100.0)
    assert fiscal_year_fcf(cf, 2099) is None


def test_multi_year_ratio_sums_window():
    # 3 年：FCF 各 80/90/100，分红各 40/45/50 → 合计 270 / 135 = 2.0
    cf = _cashflow((2023, 100.0, 20.0), (2024, 110.0, 20.0), (2025, 130.0, 30.0))
    div = {2023: 40.0, 2024: 45.0, 2025: 50.0}
    ratio, years, detail = multi_year_ratio(cf, div, years=3, min_years=3)
    assert ratio == pytest.approx(270.0 / 135.0)
    assert years == 3
    assert detail[2025] == (100.0, 50.0)


def test_multi_year_ratio_counts_confirmed_zero_dividend_year():
    # Explicitly confirmed zero; missing records must not be substituted with zero.
    cf = _cashflow((2023, 100.0, 20.0), (2024, 10.0, 20.0), (2025, 130.0, 30.0))
    div = {2023: 40.0, 2024: 0.0, 2025: 50.0}
    ratio, years, detail = multi_year_ratio(cf, div, years=3, min_years=2)
    # ΣFCF = 80 - 10 + 100 = 170；Σ分红 = 90
    assert ratio == pytest.approx(170.0 / 90.0)
    assert years == 3
    assert detail[2024] == (-10.0, 0.0)


def test_multi_year_ratio_requires_min_years_and_positive_dividend():
    cf = _cashflow((2024, 100.0, 20.0), (2025, 130.0, 30.0))
    assert multi_year_ratio(cf, {2024: 10.0}, years=5, min_years=3) == (None, 1, {})
    assert multi_year_ratio(cf, {}, years=5, min_years=3) == (None, 0, {})
    assert multi_year_ratio({}, {2025: 10.0}, years=5, min_years=3) == (None, 0, {})


def test_multi_year_ratio_drops_latest_year_when_dividend_pending():
    # 2030 财年刚结束不久且无分红记录 → 视为「尚未公布」，从窗口剔除
    cf = _cashflow((2028, 100.0, 20.0), (2029, 100.0, 20.0), (2030, 100.0, 20.0))
    ratio, years, detail = multi_year_ratio(cf, {2028: 40.0, 2029: 40.0}, years=3, min_years=2)
    assert 2030 not in detail
    assert years == 2
    assert ratio == pytest.approx(160.0 / 80.0)


def test_multi_year_ratio_keeps_old_zero_dividend_year():
    # Old records still require an explicit zero distribution.
    cf = _cashflow((2014, 100.0, 20.0), (2015, 100.0, 20.0), (2016, 100.0, 20.0))
    ratio, years, detail = multi_year_ratio(cf, {2014: 40.0, 2015: 0.0, 2016: 40.0}, years=3, min_years=2)
    assert years == 3 and 2015 in detail
    assert ratio == pytest.approx(240.0 / 80.0)


def test_grade_ratio_thresholds_and_label():
    cfg = CoverageConfig()
    assert grade_ratio(2.0, cfg)[0] == "ok"
    assert grade_ratio(1.2, cfg)[0] == "warn"
    assert grade_ratio(0.5, cfg)[0] == "risk"
    assert "5年均FCF/分红" in grade_ratio(2.0, cfg, "5年均FCF/分红")[1]


def test_evaluate_coverage_uses_multi_year_as_primary(monkeypatch):
    monkeypatch.setattr("dividend_grid.coverage.fetch_bank_metrics", lambda code, **kw: None)
    monkeypatch.setattr("dividend_grid.coverage.fetch_cashflow", lambda code, **kw: _cashflow(
        (2023, 130.0, 30.0), (2024, 130.0, 30.0), (2025, 130.0, 30.0),
        (2026, 130.0, 30.0), (2027, 130.0, 30.0)))
    monkeypatch.setattr("dividend_grid.coverage.fetch_dividends", lambda code, **kw: {
        2023: 40.0, 2024: 40.0, 2025: 40.0, 2026: 40.0, 2027: 40.0})
    # 5 年合计 FCF = 500，分红 = 200 → 2.50x（TTM 亦为 2.00x，但主判据取多年）
    result = evaluate_coverage("sh600887", 5.0, 1000.0)
    assert result.fcf_avg_ratio == pytest.approx(2.5)
    assert result.fcf_avg_years == 5
    assert result.ratio == pytest.approx(2.0)   # 单窗口 TTM 仍保留
    assert result.flag == "ok"
    assert "5年均FCF/分红 2.50x" in result.note


def test_evaluate_coverage_notes_when_multi_year_unavailable(monkeypatch):
    monkeypatch.setattr("dividend_grid.coverage.fetch_bank_metrics", lambda code, **kw: None)
    monkeypatch.setattr("dividend_grid.coverage.fetch_cashflow", lambda code, **kw: _cashflow(
        (2025, 130.0, 30.0)))

    def _boom(code, **kw):
        raise RuntimeError("无分红记录")

    monkeypatch.setattr("dividend_grid.coverage.fetch_dividends", _boom)
    result = evaluate_coverage("sh600887", 5.0, 1000.0)
    assert result.fcf_avg_ratio is None
    assert result.flag == "ok"          # 回退 TTM：FCF 100 / 分红 50 = 2.0x
    assert "回退单窗口" in result.note


# --- 分红明细抓取（东方财富） -----------------------------------------------

class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _dividend_payload(rows):
    return {"result": {"data": rows}}


def test_fetch_dividends_aggregates_by_fiscal_year(monkeypatch):
    calls = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        calls["filter"] = params.get("filter")
        return _FakeResponse(_dividend_payload([
            # 2025 年度 10 派 9 元 + 2025 中期 10 派 4.8 元 → 同一财年合并
            {"REPORT_DATE": "2025-12-31 00:00:00", "PRETAX_BONUS_RMB": 9,
             "NOTICE_DATE": "2026-05-01", "EX_DIVIDEND_DATE": "2026-06-01",
             "TOTAL_SHARES": 1000, "ASSIGN_PROGRESS": "实施分配"},
            {"REPORT_DATE": "2025-09-30 00:00:00", "PRETAX_BONUS_RMB": 4.8,
             "NOTICE_DATE": "2025-11-01", "EX_DIVIDEND_DATE": "2025-12-01",
             "TOTAL_SHARES": 1000, "ASSIGN_PROGRESS": "实施分配"},
            # 未实施 → 不计
            {"REPORT_DATE": "2024-12-31 00:00:00", "PRETAX_BONUS_RMB": 12,
             "TOTAL_SHARES": 1000, "ASSIGN_PROGRESS": "董事会预案"},
            # 纯送股无现金，明确零现金分配。
            {"REPORT_DATE": "2023-12-31 00:00:00", "PRETAX_BONUS_RMB": None,
             "NOTICE_DATE": "2024-05-01", "EX_DIVIDEND_DATE": "2024-06-01", "BONUS_RATIO": 1,
             "TOTAL_SHARES": 1000, "ASSIGN_PROGRESS": "实施分配"},
            {"REPORT_DATE": "2022-12-31 00:00:00", "PRETAX_BONUS_RMB": 5,
             "NOTICE_DATE": "2023-05-01", "EX_DIVIDEND_DATE": "2023-06-01",
             "TOTAL_SHARES": 1000, "ASSIGN_PROGRESS": "实施分配"},
        ]))

    monkeypatch.setattr("dividend_grid.coverage.requests.get", fake_get)
    result = fetch_dividends("sh600887")
    assert calls["filter"] == '(SECURITY_CODE="600887")'
    assert result == {2025: pytest.approx((9 + 4.8) / 10 * 1000), 2023: 0.0, 2022: pytest.approx(500.0)}


def test_fetch_dividends_raises_without_implemented_records(monkeypatch):
    monkeypatch.setattr("dividend_grid.coverage.requests.get", lambda *a, **kw: _FakeResponse(
        _dividend_payload([{"REPORT_DATE": "2024-12-31 00:00:00", "PRETAX_BONUS_RMB": 12,
                            "TOTAL_SHARES": 1000, "ASSIGN_PROGRESS": "股东大会通过"}])))
    with pytest.raises(RuntimeError):
        fetch_dividends("sh600887")


def test_parse_tencent_quote_extracts_market_cap():
    parts = [""] * 88
    parts[1], parts[2], parts[3] = "兴业银行", "601166", "18.24"
    parts[30] = "20260910150000"
    parts[39], parts[45], parts[46], parts[64] = "5.12", "3860.11", "0.46", "5.84"
    kv = parse_tencent_quote('v_sh601166="' + "~".join(parts) + '";')
    assert kv["总市值(亿)"] == pytest.approx(3860.11)
    assert kv["股息率(TTM)"] == pytest.approx(5.84)


def test_quality_metrics_convert_market_cap_to_yuan(monkeypatch):
    source = AkShareDataSource()
    monkeypatch.setattr(source, "_fetch", lambda code: {
        "每股收益": 1.0, "股息(TTM)": 0.6, "市盈率(TTM)": 5.0, "市净率": 0.46,
        "总市值(亿)": 3860.11, "股息率(TTM)": 5.84, "_source": "tencent",
    })
    monkeypatch.setattr("dividend_grid.datasource.fetch_dividend_records",
                        lambda _: (_ for _ in ()).throw(RuntimeError("offline")))
    metrics = source.get_quality("sh601166")
    assert metrics.market_cap == pytest.approx(3860.11e8)
    assert metrics.payout_ratio == pytest.approx(60.0)

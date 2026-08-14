"""네트워크 없이 파이프라인 로직을 검증하는 오프라인 테스트.

실행: python tests/test_pipeline.py   (pytest 로도 실행 가능)
"""
from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
import market_calendar as mcal  # noqa: E402
import step1_candidates as s1  # noqa: E402
import step2_returns as s2  # noqa: E402
import step3_stats as s3  # noqa: E402

ET = ZoneInfo(config.MARKET_TZ)


def _bar(day: date, hh: int, mm: int, o: float, h: float, l: float, c: float, v: float) -> dict:
    ts = datetime(day.year, day.month, day.day, hh, mm, tzinfo=ET)
    return {"t": int(ts.timestamp() * 1000), "o": o, "h": h, "l": l, "c": c, "v": v}


# ------------------------------------------------------------------ 거래일

def test_calendar() -> None:
    h2024 = mcal.holidays(2024)
    for expected in ["2024-01-01", "2024-01-15", "2024-02-19", "2024-03-29",
                     "2024-05-27", "2024-06-19", "2024-07-04", "2024-09-02",
                     "2024-11-28", "2024-12-25"]:
        assert date.fromisoformat(expected) in h2024, expected

    # 2024년 정규 거래일은 252일.
    days = mcal.trading_days(date(2024, 1, 1), date(2024, 12, 31))
    assert len(days) == 252, len(days)

    # 2022년 새해는 토요일 -> NYSE 는 12/31 을 쉬지 않는다.
    assert date(2021, 12, 31) not in mcal.holidays(2021)

    # 금요일 다음 거래일은 월요일, 휴일은 건너뛴다.
    assert mcal.next_trading_day(date(2024, 7, 3)) == date(2024, 7, 5)
    print("✓ market_calendar")


# ------------------------------------------------------------------ 2단계

def _sample_payload(day: date, nxt: date, *, ah_volume: float = 100.0,
                    pm_volume: float = 100.0) -> dict:
    return {"results": [
        # 정규장: 마지막 봉(15:59) 종가 10.0 이 RC
        _bar(day, 9, 30, 6.0, 6.5, 5.9, 6.2, 1_000),
        _bar(day, 15, 59, 9.9, 10.2, 9.8, 10.0, 2_000),
        # 애프터: 최저 8.0 (16:00~20:00)
        _bar(day, 16, 1, 9.9, 10.0, 9.0, 9.2, ah_volume),
        _bar(day, 19, 30, 9.0, 9.1, 8.0, 8.5, ah_volume),
        # 20:00 봉은 애프터 구간 밖 -> 최저가에 반영되면 안 된다
        _bar(day, 20, 0, 8.5, 8.6, 1.0, 8.5, 50),
        # 프리마켓: 최고 12.0 (04:00~09:30)
        _bar(nxt, 4, 0, 8.6, 9.0, 8.5, 8.9, pm_volume),
        _bar(nxt, 9, 29, 11.0, 12.0, 10.9, 11.5, pm_volume),
        # 익일 시가
        _bar(nxt, 9, 30, 11.0, 11.5, 10.5, 11.2, 5_000),
    ]}


def test_measure() -> None:
    day, nxt = date(2024, 3, 5), date(2024, 3, 6)
    row = {"ticker": "TEST", "date": day.isoformat(), "prev_close": 5.0,
           "close": 10.0, "volume": 9_000_000, "gain_ratio": 2.0,
           "shares_outstanding": 1_000_000}

    out = s2.measure(row, s2.to_frame(_sample_payload(day, nxt)), nxt)
    assert out["status"] == "ok", out["status"]
    assert out["rc"] == 10.0, out["rc"]
    assert out["ah_low"] == 8.0, out["ah_low"]     # 20:00 봉의 1.0 은 제외
    assert out["pm_high"] == 12.0, out["pm_high"]
    assert out["next_open"] == 11.0, out["next_open"]
    assert abs(out["A"] - (-0.20)) < 1e-9, out["A"]
    assert abs(out["B"] - 0.50) < 1e-9, out["B"]
    assert abs(out["C"] - 0.10) < 1e-9, out["C"]

    # 연장거래 거래량이 0인 날은 표본에서 제외
    zero_ah = s2.measure(row, s2.to_frame(_sample_payload(day, nxt, ah_volume=0)), nxt)
    assert zero_ah["status"] == "no_extended_volume", zero_ah["status"]
    zero_pm = s2.measure(row, s2.to_frame(_sample_payload(day, nxt, pm_volume=0)), nxt)
    assert zero_pm["status"] == "no_extended_volume", zero_pm["status"]

    # 분봉이 아예 없는 경우
    empty = s2.measure(row, s2.to_frame({"results": []}), nxt)
    assert empty["status"] == "no_regular_bars", empty["status"]
    print("✓ step2.measure")


def test_timezone_conversion() -> None:
    """UTC 밀리초가 ET 로 정확히 변환되는지 (DST 경계 포함)."""
    day = date(2024, 11, 4)  # 표준시 전환 직후
    df = s2.to_frame({"results": [_bar(day, 16, 0, 1, 1, 1, 1, 1)]})
    assert df.iloc[0]["clock"].hour == 16, df.iloc[0]["clock"]
    assert df.iloc[0]["day"] == day
    print("✓ timezone")


# ------------------------------------------------------------------ 1단계

def test_scan(monkeypatched: bool = True) -> None:
    d1, d2 = date(2024, 3, 4), date(2024, 3, 5)
    fake = {
        d1.isoformat(): {"results": [
            {"T": "GAIN", "c": 5.0, "v": 100_000},
            {"T": "QUIET", "c": 5.0, "v": 100_000},
            {"T": "CHEAP", "c": 0.50, "v": 100_000},
            {"T": "THIN", "c": 5.0, "v": 100_000},
        ]},
        d2.isoformat(): {"results": [
            {"T": "GAIN", "c": 10.0, "v": 9_000_000},    # +100%, 통과
            {"T": "QUIET", "c": 6.0, "v": 9_000_000},    # +20%, 탈락
            {"T": "CHEAP", "c": 1.50, "v": 9_000_000},   # 종가 $2 미만, 탈락
            {"T": "THIN", "c": 10.0, "v": 1_000_000},    # 거래량 부족, 탈락
        ]},
    }
    original = s1.pc.grouped_daily
    s1.pc.grouped_daily = lambda iso: fake.get(iso, {"results": []})
    try:
        raw = s1.scan_raw_candidates([d1, d2])
    finally:
        s1.pc.grouped_daily = original

    assert list(raw["ticker"]) == ["GAIN"], list(raw["ticker"])
    assert raw.iloc[0]["gain_ratio"] == 2.0
    assert raw.iloc[0]["date"] == d2.isoformat()
    print("✓ step1.scan_raw_candidates")


# ------------------------------------------------------------------ 3단계

def test_describe_and_summary() -> None:
    stats = s3.describe(pd.Series([-0.4, -0.2, -0.1, 0.1]))
    assert stats["n"] == 4
    assert abs(stats["p_negative"] - 0.75) < 1e-9
    assert abs(stats["mean"] - (-0.15)) < 1e-9
    assert abs(stats["median"] - (-0.15)) < 1e-9
    assert stats["min"] == -0.4 and stats["max"] == 0.1
    assert s3.describe(pd.Series([], dtype=float))["n"] == 0

    df = pd.DataFrame({
        "ticker": ["A", "B", "C", "D"],
        "shares_outstanding": [1_000_000, 50_000_000, None, 2_999_999],
        "gain_ratio": [2.0, 3.0, 1.9, 2.5],
        "A": [-0.3, -0.1, -0.25, 0.05],
        "B": [0.2, 0.1, 0.4, 0.0],
        "C": [-0.05, 0.02, -0.15, 0.01],
        "reverse_split_bucket": ["역분할 이력 있음", "역분할 이력 없음",
                                 "역분할 이력 있음", "역분할 이력 없음"],
        "float_bucket": ["유통주식 300만주 미만", "유통주식 300만주 이상",
                         "미상", "유통주식 300만주 미만"],
        "gain_bucket": ["정규장 +80~150%", "정규장 +150% 초과",
                        "정규장 +80~150%", "정규장 +80~150%"],
    })
    summary = s3.build_summary(df)
    assert list(summary.columns) == s3.SUMMARY_COLUMNS
    overall = summary[(summary["group"] == "전체") & (summary["metric"] == "A")].iloc[0]
    assert overall["n"] == 4
    assert abs(overall["p_negative"] - 0.75) < 1e-9
    # 3개 그룹 × 각 하위그룹 × 3개 지표 + 전체 3개
    assert len(summary) == 3 + 3 * (2 + 3 + 2), len(summary)
    print("✓ step3.describe / build_summary")


def test_plot(tmp: Path) -> None:
    original = config.HIST_PNG
    config.HIST_PNG = tmp / "hist.png"
    try:
        s3.plot_histogram(pd.Series([-0.4, -0.2, -0.1, 0.0, 0.1, -0.35]))
        assert config.HIST_PNG.exists() and config.HIST_PNG.stat().st_size > 1000
    finally:
        config.HIST_PNG = original
    print("✓ step3.plot_histogram")


def main() -> None:
    import tempfile

    test_calendar()
    test_measure()
    test_timezone_conversion()
    test_scan()
    test_describe_and_summary()
    with tempfile.TemporaryDirectory() as tmp:
        test_plot(Path(tmp))
    print("\n전체 통과")


if __name__ == "__main__":
    main()

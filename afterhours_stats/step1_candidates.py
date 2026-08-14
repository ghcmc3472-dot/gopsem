"""1단계 — 정규장 급등 마감 후보 종목 추출.

grouped 일봉을 하루씩 받아 전일 대비 상승률/거래량/종가 필터를 적용하고,
NASDAQ / NYSE American 상장 여부를 참조 API 로 확인해
data/candidates.csv 를 만든다.
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta

import pandas as pd
from tqdm import tqdm

import config
import market_calendar as mcal
import polygon_client as pc

CANDIDATE_COLUMNS = [
    "ticker",
    "date",
    "prev_close",
    "close",
    "volume",
    "gain_ratio",
    "primary_exchange",
    "shares_outstanding",
]


def _daily_map(payload: dict) -> dict[str, dict]:
    """grouped 응답을 {ticker: bar} 로 변환."""
    return {bar["T"]: bar for bar in (payload.get("results") or []) if "T" in bar}


def scan_raw_candidates(days: list[date]) -> pd.DataFrame:
    """거래소 필터를 적용하기 전의 급등 마감 후보를 모은다."""
    rows: list[dict] = []
    prev_map: dict[str, dict] = {}
    prev_day: date | None = None

    for day in tqdm(days, desc="1단계 grouped 일봉", unit="일"):
        iso = day.isoformat()
        try:
            payload = pc.grouped_daily(iso)
        except pc.PolygonError as exc:
            pc.log_skip("grouped", iso, str(exc))
            # 이 날을 건너뛰면 다음 날의 전일 기준이 어긋나므로 연결을 끊는다.
            prev_map, prev_day = {}, None
            continue

        cur_map = _daily_map(payload)
        if not cur_map:
            # 임시 휴장일. 전일 기준은 그대로 유지한다.
            continue

        # 직전 거래일과 실제로 인접한 경우에만 전일 대비를 계산한다.
        if prev_map and prev_day is not None and mcal.next_trading_day(prev_day) == day:
            for ticker, bar in cur_map.items():
                close = bar.get("c")
                volume = bar.get("v")
                if close is None or volume is None:
                    continue
                if close < config.MIN_CLOSE or volume < config.MIN_VOLUME:
                    continue
                prev = prev_map.get(ticker)
                if not prev or not prev.get("c"):
                    continue
                prev_close = prev["c"]
                ratio = close / prev_close
                if ratio < config.MIN_GAIN_RATIO:
                    continue
                rows.append(
                    {
                        "ticker": ticker,
                        "date": iso,
                        "prev_close": round(prev_close, 6),
                        "close": round(close, 6),
                        "volume": int(volume),
                        "gain_ratio": round(ratio, 6),
                    }
                )

        prev_map, prev_day = cur_map, day

    return pd.DataFrame(rows, columns=["ticker", "date", "prev_close", "close", "volume", "gain_ratio"])


def apply_exchange_filter(raw: pd.DataFrame) -> pd.DataFrame:
    """NASDAQ(XNAS) / NYSE American(XASE) 상장 종목만 남긴다."""
    if raw.empty:
        return raw.assign(primary_exchange=[], shares_outstanding=[])

    details: dict[str, tuple[str, float | None]] = {}
    for ticker in tqdm(sorted(raw["ticker"].unique()), desc="1단계 거래소 확인", unit="종목"):
        try:
            payload = pc.ticker_details(ticker)
        except pc.PolygonError as exc:
            pc.log_skip("ticker_details", ticker, str(exc))
            continue
        result = payload.get("results") or {}
        details[ticker] = (
            result.get("primary_exchange") or "",
            result.get("share_class_shares_outstanding"),
        )

    raw = raw.copy()
    raw["primary_exchange"] = raw["ticker"].map(lambda t: details.get(t, ("", None))[0])
    raw["shares_outstanding"] = raw["ticker"].map(lambda t: details.get(t, ("", None))[1])
    return raw[raw["primary_exchange"].isin(config.ALLOWED_EXCHANGES)].reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="1단계: 급등 마감 후보 추출")
    parser.add_argument("--start", help="시작일 YYYY-MM-DD (기본: 2년 전)")
    parser.add_argument("--end", help="종료일 YYYY-MM-DD (기본: 어제)")
    args = parser.parse_args()

    config.ensure_dirs()
    if not config.POLYGON_API_KEY:
        raise SystemExit(".env 에 POLYGON_API_KEY 를 설정하세요.")

    end = date.fromisoformat(args.end) if args.end else date.today() - timedelta(days=1)
    start = (
        date.fromisoformat(args.start)
        if args.start
        else end - timedelta(days=365 * config.HISTORY_YEARS)
    )

    # 첫날의 전일 종가가 필요하므로 하루 앞에서 시작한다.
    days = mcal.trading_days(start - timedelta(days=7), end)
    print(f"조회 구간: {days[0]} ~ {days[-1]} ({len(days)} 거래일)")

    raw = scan_raw_candidates(days)
    print(f"1차 필터 통과: {len(raw)}건 (고유 종목 {raw['ticker'].nunique() if not raw.empty else 0}개)")

    candidates = apply_exchange_filter(raw)
    candidates = candidates.reindex(columns=CANDIDATE_COLUMNS)
    candidates.to_csv(config.CANDIDATES_CSV, index=False)
    print(f"최종 후보 {len(candidates)}건 → {config.CANDIDATES_CSV}")


if __name__ == "__main__":
    main()

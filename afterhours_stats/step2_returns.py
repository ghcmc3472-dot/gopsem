"""2단계 — 후보별 애프터마켓/프리마켓 구간 수익률 측정.

각 후보의 급등일과 익 거래일 분봉을 받아 America/New_York 기준으로
RC / AH_LOW / PM_HIGH / NEXT_OPEN 을 뽑고 A, B, C 를 계산한다.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date, time

import pandas as pd
from tqdm import tqdm

import config
import market_calendar as mcal
import polygon_client as pc

MEASURE_COLUMNS = [
    "ticker",
    "date",
    "next_date",
    "prev_close",
    "close",
    "volume",
    "gain_ratio",
    "shares_outstanding",
    "rc",
    "ah_low",
    "pm_high",
    "next_open",
    "ah_volume",
    "pm_volume",
    "A",
    "B",
    "C",
    "status",
]

REGULAR_OPEN = time(*config.REGULAR_OPEN)
REGULAR_CLOSE = time(*config.REGULAR_CLOSE)
AH_END = time(*config.AFTERHOURS_END)
PM_START = time(*config.PREMARKET_START)


def to_frame(payload: dict) -> pd.DataFrame:
    """분봉 응답을 ET 로컬 시각 인덱스를 가진 DataFrame 으로 변환."""
    results = payload.get("results") or []
    if not results:
        return pd.DataFrame(columns=["o", "h", "l", "c", "v", "day", "clock"])

    df = pd.DataFrame(results)
    ts = pd.to_datetime(df["t"], unit="ms", utc=True).dt.tz_convert(config.MARKET_TZ)
    df = df.assign(ts=ts).sort_values("ts").reset_index(drop=True)
    df["day"] = df["ts"].dt.date
    df["clock"] = df["ts"].dt.time
    return df


def _window(df: pd.DataFrame, day: date, start: time, end: time) -> pd.DataFrame:
    """[start, end) 구간 분봉. 봉 타임스탬프는 해당 분의 시작 시각이다."""
    return df[(df["day"] == day) & (df["clock"] >= start) & (df["clock"] < end)]


def measure(row: dict, df: pd.DataFrame, next_day: date) -> dict:
    """한 후보에 대한 구간 지표를 계산한다."""
    day = date.fromisoformat(row["date"])

    out = {
        "ticker": row["ticker"],
        "date": row["date"],
        "next_date": next_day.isoformat(),
        "prev_close": row.get("prev_close"),
        "close": row.get("close"),
        "volume": row.get("volume"),
        "gain_ratio": row.get("gain_ratio"),
        "shares_outstanding": row.get("shares_outstanding"),
        "rc": None,
        "ah_low": None,
        "pm_high": None,
        "next_open": None,
        "ah_volume": 0.0,
        "pm_volume": 0.0,
        "A": None,
        "B": None,
        "C": None,
        "status": "ok",
    }

    regular = _window(df, day, REGULAR_OPEN, REGULAR_CLOSE)
    if regular.empty:
        out["status"] = "no_regular_bars"
        return out
    rc = float(regular.iloc[-1]["c"])
    if rc <= 0:
        out["status"] = "bad_rc"
        return out
    out["rc"] = rc

    after = _window(df, day, REGULAR_CLOSE, AH_END)
    pre = _window(df, next_day, PM_START, REGULAR_OPEN)
    out["ah_volume"] = float(after["v"].sum()) if not after.empty else 0.0
    out["pm_volume"] = float(pre["v"].sum()) if not pre.empty else 0.0

    if out["ah_volume"] <= 0 or out["pm_volume"] <= 0:
        out["status"] = "no_extended_volume"
        return out

    ah_low = float(after["l"].min())
    pm_high = float(pre["h"].max())
    if ah_low <= 0:
        out["status"] = "bad_ah_low"
        return out
    out["ah_low"] = ah_low
    out["pm_high"] = pm_high

    next_session = df[(df["day"] == next_day) & (df["clock"] >= REGULAR_OPEN)]
    if next_session.empty:
        out["status"] = "no_next_open"
        return out
    next_open = float(next_session.iloc[0]["o"])
    out["next_open"] = next_open

    out["A"] = (ah_low - rc) / rc
    out["B"] = (pm_high - ah_low) / ah_low
    out["C"] = (next_open - rc) / rc
    return out


def load_done() -> set[tuple[str, str]]:
    """이미 측정한 (ticker, date) — 중단 후 재실행 시 이어받기용."""
    if not config.MEASURES_CSV.exists():
        return set()
    done = pd.read_csv(config.MEASURES_CSV, usecols=["ticker", "date"], dtype=str)
    return set(zip(done["ticker"], done["date"]))


def main() -> None:
    parser = argparse.ArgumentParser(description="2단계: 구간별 수익률 측정")
    parser.add_argument("--limit", type=int, help="처리할 후보 수 제한 (테스트용)")
    args = parser.parse_args()

    config.ensure_dirs()
    if not config.CANDIDATES_CSV.exists():
        raise SystemExit("data/candidates.csv 가 없습니다. 먼저 1단계를 실행하세요.")

    candidates = pd.read_csv(config.CANDIDATES_CSV)
    candidates["date"] = candidates["date"].astype(str)
    if args.limit:
        candidates = candidates.head(args.limit)

    done = load_done()
    todo = [
        row
        for row in candidates.to_dict("records")
        if (row["ticker"], row["date"]) not in done
    ]
    print(f"후보 {len(candidates)}건 중 {len(done)}건 완료, {len(todo)}건 남음")

    new_file = not config.MEASURES_CSV.exists()
    with config.MEASURES_CSV.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=MEASURE_COLUMNS)
        if new_file:
            writer.writeheader()

        for row in tqdm(todo, desc="2단계 분봉 측정", unit="건"):
            day = date.fromisoformat(row["date"])
            next_day = mcal.next_trading_day(day)
            try:
                payload = pc.minute_aggs(row["ticker"], row["date"], next_day.isoformat())
            except pc.PolygonError as exc:
                pc.log_skip("minute_aggs", f"{row['ticker']}@{row['date']}", str(exc))
                continue

            record = measure(row, to_frame(payload), next_day)
            writer.writerow({k: record.get(k) for k in MEASURE_COLUMNS})
            fh.flush()  # 중단되어도 여기까지는 남는다

    measures = pd.read_csv(config.MEASURES_CSV)
    usable = (measures["status"] == "ok").sum()
    print(f"측정 완료 {len(measures)}건 (유효 {usable}건) → {config.MEASURES_CSV}")
    for status, count in measures["status"].value_counts().items():
        if status != "ok":
            print(f"  제외 {status}: {count}건")


if __name__ == "__main__":
    main()

"""3단계 — 통계 산출 및 출력.

A / B / C 각각의 분포 통계와 분기 집계를 results/summary.csv 와 콘솔에 내고,
A 값 히스토그램을 results/hist_afterhours.png 로 저장한다.
"""
from __future__ import annotations

import argparse

import matplotlib

matplotlib.use("Agg")  # 헤드리스 환경에서도 저장 가능하게

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from tqdm import tqdm

import config
import polygon_client as pc

METRICS = {
    "A": "애프터 하락률 (AH_LOW/RC-1)",
    "B": "프리마켓 되돌림률 (PM_HIGH/AH_LOW-1)",
    "C": "익일 시가 갭 (NEXT_OPEN/RC-1)",
}

SUMMARY_COLUMNS = [
    "group",
    "subgroup",
    "metric",
    "n",
    "p_negative",
    "mean",
    "median",
    "std",
    "p25",
    "p50",
    "p75",
    "min",
    "max",
]


def describe(series: pd.Series) -> dict:
    """표본 수, 하락 확률, 평균/중앙값/표준편차/분위수/최소최대."""
    values = pd.to_numeric(series, errors="coerce").dropna()
    if values.empty:
        return {k: np.nan for k in SUMMARY_COLUMNS[3:]} | {"n": 0}
    return {
        "n": int(values.size),
        "p_negative": float((values < 0).mean()),
        "mean": float(values.mean()),
        "median": float(values.median()),
        "std": float(values.std(ddof=1)) if values.size > 1 else 0.0,
        "p25": float(values.quantile(0.25)),
        "p50": float(values.quantile(0.50)),
        "p75": float(values.quantile(0.75)),
        "min": float(values.min()),
        "max": float(values.max()),
    }


def reverse_split_flags(tickers: list[str]) -> dict[str, bool]:
    """split_from > split_to 인 이력이 있으면 역분할 경험 종목."""
    flags: dict[str, bool] = {}
    for ticker in tqdm(tickers, desc="3단계 분할 이력", unit="종목"):
        try:
            payload = pc.ticker_splits(ticker)
        except pc.PolygonError as exc:
            pc.log_skip("splits", ticker, str(exc))
            continue  # 미확인 종목은 분기 집계에서 빠진다
        flags[ticker] = any(
            (item.get("split_from") or 0) > (item.get("split_to") or 0)
            for item in (payload.get("results") or [])
        )
    return flags


def add_buckets(df: pd.DataFrame) -> pd.DataFrame:
    """분기 집계용 라벨을 붙인다."""
    df = df.copy()

    shares = pd.to_numeric(df["shares_outstanding"], errors="coerce")
    df["float_bucket"] = np.where(
        shares.isna(),
        "미상",
        np.where(shares < config.SMALL_FLOAT_THRESHOLD, "유통주식 300만주 미만", "유통주식 300만주 이상"),
    )

    flags = reverse_split_flags(sorted(df["ticker"].unique()))
    df["reverse_split_bucket"] = df["ticker"].map(
        lambda t: {True: "역분할 이력 있음", False: "역분할 이력 없음"}.get(flags.get(t), "미상")
    )

    ratio = pd.to_numeric(df["gain_ratio"], errors="coerce")
    df["gain_bucket"] = np.where(
        ratio.isna(),
        "미상",
        np.where(ratio <= config.HIGH_GAIN_THRESHOLD, "정규장 +80~150%", "정규장 +150% 초과"),
    )
    return df


def build_summary(df: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict] = []

    for metric in METRICS:
        rows.append({"group": "전체", "subgroup": "전체", "metric": metric, **describe(df[metric])})

    buckets = {
        "유통주식수": "float_bucket",
        "역분할 이력": "reverse_split_bucket",
        "정규장 상승률": "gain_bucket",
    }
    for group_name, column in buckets.items():
        for subgroup in sorted(df[column].dropna().unique()):
            subset = df[df[column] == subgroup]
            for metric in METRICS:
                rows.append(
                    {
                        "group": group_name,
                        "subgroup": subgroup,
                        "metric": metric,
                        **describe(subset[metric]),
                    }
                )

    return pd.DataFrame(rows, columns=SUMMARY_COLUMNS)


def print_table(summary: pd.DataFrame) -> None:
    display = summary.copy()
    for col in ("p_negative", "mean", "median", "std", "p25", "p50", "p75", "min", "max"):
        display[col] = pd.to_numeric(display[col], errors="coerce").map(
            lambda v: "" if pd.isna(v) else f"{v * 100:>7.2f}%"
        )
    for group_name, block in display.groupby("group", sort=False):
        print(f"\n=== {group_name} ===")
        print(block.drop(columns=["group"]).to_string(index=False))


def plot_histogram(values: pd.Series) -> None:
    values = pd.to_numeric(values, errors="coerce").dropna() * 100
    fig, ax = plt.subplots(figsize=(9, 5.5))
    ax.hist(values, bins=40, color="#4C78A8", edgecolor="white")
    ax.axvline(0, color="#444444", linewidth=1)
    ax.axvline(
        values.mean(),
        color="#E45756",
        linewidth=1.5,
        linestyle="--",
        label=f"mean {values.mean():.2f}%",
    )
    ax.axvline(
        values.median(),
        color="#F58518",
        linewidth=1.5,
        linestyle=":",
        label=f"median {values.median():.2f}%",
    )
    # 기본 matplotlib 폰트에 한글 글리프가 없어 축 라벨은 영문으로 둔다.
    ax.set_xlabel("A = (AH_LOW - RC) / RC  [%]")
    ax.set_ylabel("Frequency")
    ax.set_title(f"After-hours low vs regular close (n={values.size})")
    ax.legend()
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(config.HIST_PNG, dpi=150)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description="3단계: 통계 출력")
    parser.add_argument(
        "--no-splits",
        action="store_true",
        help="역분할 조회를 건너뛴다 (API 콜 절약, 해당 분기는 '미상' 처리)",
    )
    args = parser.parse_args()

    config.ensure_dirs()
    if not config.MEASURES_CSV.exists():
        raise SystemExit("data/measures.csv 가 없습니다. 먼저 2단계를 실행하세요.")

    measures = pd.read_csv(config.MEASURES_CSV)
    total = len(measures)
    valid = measures[measures["status"] == "ok"].copy()

    print(f"측정 {total}건 / 유효 표본 {len(valid)}건")
    excluded = measures[measures["status"] != "ok"]["status"].value_counts()
    for status, count in excluded.items():
        print(f"  제외 {status}: {count}건")

    if valid.empty:
        raise SystemExit("유효 표본이 없습니다.")

    if args.no_splits:
        valid["reverse_split_bucket"] = "미상"
        shares = pd.to_numeric(valid["shares_outstanding"], errors="coerce")
        valid["float_bucket"] = np.where(
            shares.isna(),
            "미상",
            np.where(shares < config.SMALL_FLOAT_THRESHOLD, "유통주식 300만주 미만", "유통주식 300만주 이상"),
        )
        ratio = pd.to_numeric(valid["gain_ratio"], errors="coerce")
        valid["gain_bucket"] = np.where(
            ratio.isna(),
            "미상",
            np.where(ratio <= config.HIGH_GAIN_THRESHOLD, "정규장 +80~150%", "정규장 +150% 초과"),
        )
    else:
        valid = add_buckets(valid)

    summary = build_summary(valid)
    summary.to_csv(config.SUMMARY_CSV, index=False)
    print_table(summary)

    plot_histogram(valid["A"])
    print(f"\nsummary → {config.SUMMARY_CSV}")
    print(f"histogram → {config.HIST_PNG}")


if __name__ == "__main__":
    main()

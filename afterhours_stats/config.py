"""프로젝트 전역 설정.

경로, 필터 임계값, API 레이트리밋 등을 한 곳에서 관리한다.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent

load_dotenv(BASE_DIR / ".env")

# ---------------------------------------------------------------- API
POLYGON_API_KEY = os.getenv("POLYGON_API_KEY", "").strip()
POLYGON_BASE = "https://api.polygon.io"

# 무료 티어: 분당 5콜 -> 12초 간격
RATE_LIMIT_SECONDS = float(os.getenv("POLYGON_RATE_LIMIT_SECONDS", "12"))
MAX_RETRIES = 3
BACKOFF_BASE_SECONDS = 2.0  # 2s, 4s, 8s
REQUEST_TIMEOUT = 30

# ---------------------------------------------------------------- 경로
DATA_DIR = BASE_DIR / "data"
CACHE_DIR = DATA_DIR / "cache"
GROUPED_CACHE = CACHE_DIR / "grouped"
MINUTE_CACHE = CACHE_DIR / "minute"
REFERENCE_CACHE = CACHE_DIR / "reference"
SPLITS_CACHE = CACHE_DIR / "splits"

RESULTS_DIR = BASE_DIR / "results"

CANDIDATES_CSV = DATA_DIR / "candidates.csv"
MEASURES_CSV = DATA_DIR / "measures.csv"
SUMMARY_CSV = RESULTS_DIR / "summary.csv"
HIST_PNG = RESULTS_DIR / "hist_afterhours.png"
SKIPPED_LOG = BASE_DIR / "skipped.log"

# ---------------------------------------------------------------- 필터
MIN_GAIN_RATIO = 1.80        # 당일 종가 / 전일 종가
MIN_VOLUME = 5_000_000       # 당일 거래량
MIN_CLOSE = 2.00             # 당일 종가 (USD)

# NASDAQ / NYSE American (MIC 코드)
ALLOWED_EXCHANGES = {"XNAS", "XASE"}

# 히스토리 범위 (년)
HISTORY_YEARS = 2

# 분기 집계 기준
SMALL_FLOAT_THRESHOLD = 3_000_000    # 유통주식 300만주
HIGH_GAIN_THRESHOLD = 2.50           # 정규장 +150% (= 전일 대비 2.5배)

# ---------------------------------------------------------------- 세션 시간 (ET)
REGULAR_OPEN = (9, 30)
REGULAR_CLOSE = (16, 0)
AFTERHOURS_END = (20, 0)
PREMARKET_START = (4, 0)

MARKET_TZ = "America/New_York"


def ensure_dirs() -> None:
    """캐시/결과 디렉터리를 생성한다."""
    for d in (
        DATA_DIR,
        CACHE_DIR,
        GROUPED_CACHE,
        MINUTE_CACHE,
        REFERENCE_CACHE,
        SPLITS_CACHE,
        RESULTS_DIR,
    ):
        d.mkdir(parents=True, exist_ok=True)

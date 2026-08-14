"""Polygon.io 클라이언트.

- 분당 5콜 제한에 맞춘 전역 레이트 리미터 (기본 12초 간격)
- 모든 응답을 data/cache/ 에 JSON 으로 캐싱 (재실행 시 재요청 없음)
- 실패 시 3회 지수 백오프 재시도, 최종 실패는 skipped.log 기록
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

import config


class PolygonError(RuntimeError):
    """재시도를 모두 소진한 뒤에도 실패한 요청."""


class RateLimiter:
    """호출 간 최소 간격을 보장하는 스레드 안전 리미터."""

    def __init__(self, min_interval: float) -> None:
        self.min_interval = min_interval
        self._lock = threading.Lock()
        self._last_call = 0.0

    def wait(self) -> None:
        with self._lock:
            elapsed = time.monotonic() - self._last_call
            sleep_for = self.min_interval - elapsed
            if sleep_for > 0:
                time.sleep(sleep_for)
            self._last_call = time.monotonic()


_limiter = RateLimiter(config.RATE_LIMIT_SECONDS)
_session = requests.Session()


def log_skip(kind: str, key: str, reason: str) -> None:
    """실패 항목을 skipped.log 에 한 줄로 남긴다."""
    config.SKIPPED_LOG.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().isoformat(timespec="seconds")
    with config.SKIPPED_LOG.open("a", encoding="utf-8") as fh:
        fh.write(f"{stamp}\t{kind}\t{key}\t{reason}\n")


def _read_cache(path: Path) -> Any | None:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        # 손상된 캐시는 버리고 다시 받는다.
        path.unlink(missing_ok=True)
        return None


def _write_cache(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    tmp.replace(path)  # 중단 시 반쪽 파일이 남지 않도록 원자적 교체


def _request(url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """레이트리밋 + 재시도를 적용한 단일 GET."""
    params = dict(params or {})
    params["apiKey"] = config.POLYGON_API_KEY

    last_error = ""
    for attempt in range(config.MAX_RETRIES):
        _limiter.wait()
        try:
            resp = _session.get(url, params=params, timeout=config.REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            last_error = f"network: {exc}"
        else:
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 429:
                last_error = "429 rate limited"
            elif 500 <= resp.status_code < 600:
                last_error = f"{resp.status_code} server error"
            else:
                # 401/403/404 등은 재시도해도 동일하다.
                raise PolygonError(f"{resp.status_code}: {resp.text[:200]}")

        if attempt < config.MAX_RETRIES - 1:
            time.sleep(config.BACKOFF_BASE_SECONDS * (2**attempt))

    raise PolygonError(last_error or "unknown error")


def get_json(
    path: str,
    cache_path: Path,
    params: dict[str, Any] | None = None,
    paginate: bool = False,
) -> dict[str, Any]:
    """캐시 우선 GET. 캐시가 있으면 네트워크를 전혀 쓰지 않는다.

    paginate=True 이면 next_url 을 따라가며 results 를 합쳐서 캐싱한다.
    """
    cached = _read_cache(cache_path)
    if cached is not None:
        return cached

    payload = _request(config.POLYGON_BASE + path, params)

    if paginate:
        results = list(payload.get("results") or [])
        next_url = payload.get("next_url")
        while next_url:
            page = _request(next_url)
            results.extend(page.get("results") or [])
            next_url = page.get("next_url")
        payload["results"] = results
        payload.pop("next_url", None)

    _write_cache(cache_path, payload)
    return payload


# ------------------------------------------------------------------ 엔드포인트

def grouped_daily(date: str) -> dict[str, Any]:
    """특정 거래일의 전 종목 일봉."""
    return get_json(
        f"/v2/aggs/grouped/locale/us/market/stocks/{date}",
        config.GROUPED_CACHE / f"{date}.json",
        params={"adjusted": "true"},
    )


def minute_aggs(ticker: str, start_date: str, end_date: str) -> dict[str, Any]:
    """분봉 (연장거래 포함). 무료 티어 한도상 1회 5만건까지."""
    return get_json(
        f"/v2/aggs/ticker/{ticker}/range/1/minute/{start_date}/{end_date}",
        config.MINUTE_CACHE / f"{ticker}_{start_date}.json",
        params={"adjusted": "true", "sort": "asc", "limit": 50000},
    )


def ticker_details(ticker: str) -> dict[str, Any]:
    """상장 거래소, 유통주식수 등 참조 정보."""
    return get_json(
        f"/v3/reference/tickers/{ticker}",
        config.REFERENCE_CACHE / f"{ticker}.json",
    )


def ticker_splits(ticker: str) -> dict[str, Any]:
    """분할 이력 전체 (역분할 판정용)."""
    return get_json(
        "/v3/reference/splits",
        config.SPLITS_CACHE / f"{ticker}.json",
        params={"ticker": ticker, "limit": 1000},
        paginate=True,
    )

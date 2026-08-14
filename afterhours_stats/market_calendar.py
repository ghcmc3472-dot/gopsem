"""미국 주식시장 거래일 계산 (외부 의존성 없음).

주말과 NYSE 정기 휴장일을 제외한다. 임시 휴장(조문 휴장 등)은
grouped 응답의 resultsCount 가 0 인 것으로 감지해 1단계에서 건너뛴다.
"""
from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """해당 월의 n번째 weekday (월=0). n<0 이면 뒤에서부터."""
    if n > 0:
        d = date(year, month, 1)
        offset = (weekday - d.weekday()) % 7
        return d + timedelta(days=offset + 7 * (n - 1))
    # 마지막 주
    if month == 12:
        d = date(year, 12, 31)
    else:
        d = date(year, month + 1, 1) - timedelta(days=1)
    offset = (d.weekday() - weekday) % 7
    return d - timedelta(days=offset)


def _easter(year: int) -> date:
    """Anonymous Gregorian algorithm."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    lam = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * lam) // 451
    month, day = divmod(h + lam - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _observed(d: date) -> date | None:
    """토요일은 전 금요일, 일요일은 다음 월요일로 관측."""
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


@lru_cache(maxsize=None)
def holidays(year: int) -> frozenset[date]:
    """해당 연도의 NYSE 정기 휴장일."""
    days: set[date] = set()

    # 새해: 토요일이면 NYSE 는 전년 12/31 을 쉬지 않고 그냥 개장한다.
    new_year = date(year, 1, 1)
    if new_year.weekday() != 5:
        days.add(_observed(new_year))

    days.add(_nth_weekday(year, 1, 0, 3))    # MLK
    days.add(_nth_weekday(year, 2, 0, 3))    # Presidents' Day
    days.add(_easter(year) - timedelta(days=2))  # Good Friday
    days.add(_nth_weekday(year, 5, 0, -1))   # Memorial Day
    if year >= 2022:
        days.add(_observed(date(year, 6, 19)))   # Juneteenth
    days.add(_observed(date(year, 7, 4)))    # Independence Day
    days.add(_nth_weekday(year, 9, 0, 1))    # Labor Day
    days.add(_nth_weekday(year, 11, 3, 4))   # Thanksgiving
    days.add(_observed(date(year, 12, 25)))  # Christmas

    return frozenset(d for d in days if d is not None and d.year == year)


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in holidays(d.year)


def trading_days(start: date, end: date) -> list[date]:
    """start~end (양 끝 포함) 사이의 거래일 목록."""
    out: list[date] = []
    d = start
    while d <= end:
        if is_trading_day(d):
            out.append(d)
        d += timedelta(days=1)
    return out


def next_trading_day(d: date) -> date:
    nxt = d + timedelta(days=1)
    while not is_trading_day(nxt):
        nxt += timedelta(days=1)
    return nxt

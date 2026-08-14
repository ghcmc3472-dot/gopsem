"""1~3단계를 순서대로 실행한다.

모든 응답이 캐싱되므로 중단 후 다시 실행해도 이미 받은 구간은 재요청하지 않는다.
"""
from __future__ import annotations

import argparse
import sys

import config
import step1_candidates
import step2_returns
import step3_stats


def _run(module, argv: list[str]) -> None:
    saved = sys.argv
    sys.argv = [module.__name__, *argv]
    try:
        module.main()
    finally:
        sys.argv = saved


def main() -> None:
    parser = argparse.ArgumentParser(description="애프터마켓 하락률 통계 전체 실행")
    parser.add_argument("--start", help="시작일 YYYY-MM-DD")
    parser.add_argument("--end", help="종료일 YYYY-MM-DD")
    parser.add_argument("--limit", type=int, help="2단계 후보 수 제한 (테스트용)")
    parser.add_argument("--no-splits", action="store_true", help="3단계 역분할 조회 생략")
    args = parser.parse_args()

    config.ensure_dirs()
    if not config.POLYGON_API_KEY:
        raise SystemExit(".env 에 POLYGON_API_KEY 를 설정하세요.")

    step1_argv = []
    if args.start:
        step1_argv += ["--start", args.start]
    if args.end:
        step1_argv += ["--end", args.end]

    print("\n########## 1단계: 후보 종목 추출 ##########")
    _run(step1_candidates, step1_argv)

    print("\n########## 2단계: 구간별 수익률 측정 ##########")
    _run(step2_returns, ["--limit", str(args.limit)] if args.limit else [])

    print("\n########## 3단계: 통계 출력 ##########")
    _run(step3_stats, ["--no-splits"] if args.no_splits else [])


if __name__ == "__main__":
    main()

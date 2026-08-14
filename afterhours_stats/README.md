# 급등주 애프터마켓 하락률 통계 수집기

정규장에서 +80% 이상 급등 마감한 미국 마이크로캡이 애프터마켓에서 하락하는 확률과
평균 하락률, 그리고 프리마켓 되돌림 비율을 Polygon.io 실측 데이터로 산출한다.

## 측정하는 값

| 기호 | 정의 | 의미 |
|------|------|------|
| `RC` | 16:00 ET 직전 마지막 분봉 종가 | 정규장 종가 |
| `AH_LOW` | 16:00~20:00 ET 최저가 | 애프터마켓 저점 |
| `PM_HIGH` | 익 거래일 04:00~09:30 ET 최고가 | 프리마켓 고점 |
| `NEXT_OPEN` | 익 거래일 09:30 ET 시가 | 익일 시가 |
| **A** | `(AH_LOW - RC) / RC` | 애프터 하락률 |
| **B** | `(PM_HIGH - AH_LOW) / AH_LOW` | 프리마켓 되돌림률 |
| **C** | `(NEXT_OPEN - RC) / RC` | 익일 시가 갭 |

애프터 또는 프리마켓 거래량이 0인 날은 표본에서 제외하고 `status` 컬럼에
`no_extended_volume` 으로 별도 집계한다.

## 후보 필터 (1단계)

- 당일 종가 / 전일 종가 ≥ 1.80 (정규장 +80% 이상)
- 당일 거래량 ≥ 5,000,000
- 당일 종가 ≥ 2.00 USD
- 상장 거래소가 NASDAQ(`XNAS`) 또는 NYSE American(`XASE`)

전일 종가는 직전 **거래일**의 grouped 일봉에서 가져오며, 분할 왜곡을 피하기 위해
`adjusted=true` 로 조회한다.

## 설치

```bash
cd afterhours_stats
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux
pip install -r requirements.txt

copy .env.example .env          # Windows  (cp .env.example .env)
# .env 를 열어 POLYGON_API_KEY 를 채운다
```

Python 3.11 기준. 의존성: requests, pandas, numpy, matplotlib, python-dotenv, tqdm.

## 실행 순서

```bash
# 전체를 한 번에
python run_all.py

# 또는 단계별로
python step1_candidates.py            # data/candidates.csv
python step2_returns.py               # data/measures.csv
python step3_stats.py                 # results/summary.csv, results/hist_afterhours.png
```

유용한 옵션:

```bash
python step1_candidates.py --start 2024-01-01 --end 2024-03-31   # 구간 한정
python step2_returns.py --limit 20                               # 앞 20건만 (연결 확인용)
python step3_stats.py --no-splits                                # 역분할 조회 생략
```

**먼저 짧은 구간으로 한 번 돌려 API 키와 응답을 확인한 뒤 전체 2년을 실행하는 것을 권한다.**

## 예상 소요 시간

무료 티어는 분당 5콜이라 **모든 요청에 12초 간격**이 적용된다. 소요 시간은 사실상
API 콜 수 × 12초이다.

| 단계 | 콜 수 (2년 기준) | 예상 시간 |
|------|------------------|-----------|
| 1단계 grouped 일봉 | 약 505콜 (거래일 1일 1콜) | **약 1시간 40분** |
| 1단계 거래소/유통주식 확인 | 고유 후보 종목 수 (대략 300~600) | 약 1~2시간 |
| 2단계 분봉 | 후보 건수 1건당 1콜 (대략 400~800) | 약 1시간 20분~2시간 40분 |
| 3단계 분할 이력 | 고유 후보 종목 수 | 약 1~2시간 |
| **합계** | — | **대략 5~8시간** |

후보 종목 수는 시장 상황에 따라 달라지므로 2~4단계는 범위로 표기했다.
`.env` 의 `POLYGON_RATE_LIMIT_SECONDS` 를 낮추면 (유료 티어) 비례해서 짧아진다.

## 중단 후 이어받기

- 모든 API 응답은 `data/cache/` 에 JSON 으로 저장되고, 캐시가 있으면 네트워크를
  **전혀 쓰지 않는다.** 중간에 끊겨도 같은 명령을 다시 실행하면 남은 구간부터 진행한다.
- 캐시 파일은 임시 파일에 쓴 뒤 원자적으로 교체하므로 강제 종료해도 반쪽 파일이 남지 않는다.
- 2단계는 측정할 때마다 `data/measures.csv` 에 즉시 append + flush 하고, 재실행 시
  이미 처리한 `(ticker, date)` 조합을 건너뛴다.
- 특정 날짜/종목만 다시 받고 싶으면 해당 캐시 JSON 파일만 지우면 된다.

## 실패 처리

- 네트워크 오류, 429, 5xx 는 **3회 지수 백오프**(2s → 4s → 8s)로 재시도한다.
- 401/403/404 처럼 재시도해도 같은 응답은 즉시 실패 처리한다.
- 최종 실패한 항목은 `skipped.log` 에 `시각 / 종류 / 키 / 사유` 로 기록되고,
  나머지 처리는 계속 진행한다.

## 출력물

```
data/candidates.csv          후보 (ticker, date, prev_close, close, volume, gain_ratio, ...)
data/measures.csv            구간 지표 원본 (RC, AH_LOW, PM_HIGH, NEXT_OPEN, A, B, C, status)
results/summary.csv          통계 요약 (전체 + 분기 집계)
results/hist_afterhours.png  A 값 히스토그램
skipped.log                  실패 항목
```

`summary.csv` 의 각 행은 `group / subgroup / metric` 조합에 대해
표본 수(`n`), 하락 발생 확률(`p_negative`, 값 < 0 인 비중), 평균, 중앙값, 표준편차,
25/50/75 분위수, 최소/최대를 담는다. 동일한 내용을 콘솔 테이블로도 출력한다.

분기 집계 기준:

- **유통주식수**: 300만주 미만 vs 이상 (`share_class_shares_outstanding`)
- **역분할 이력**: `/v3/reference/splits` 에서 `split_from > split_to` 이력 유/무
- **정규장 상승률**: +80~150% vs +150% 초과

참조 데이터를 받지 못한 종목은 해당 분기에서 `미상` 으로 분류되어 결과를 왜곡하지 않는다.

## 테스트

네트워크 없이 거래일 계산, 구간 추출, 통계 로직을 검증한다.

```bash
python tests/test_pipeline.py
```

## 주의사항

- `share_class_shares_outstanding` 은 유통주식(float)이 아니라 해당 클래스의 발행주식 수다.
  Polygon 무료 티어에서 얻을 수 있는 가장 가까운 값이라 이를 사용했다.
- 참조 정보(거래소, 발행주식수)는 조회 시점의 **현재** 값이며 과거 시점 값이 아니다.
  오래된 표본일수록 오차가 생길 수 있다.
- 무료 티어는 약 2년 히스토리만 제공하므로 그 이전 구간은 조회되지 않는다.

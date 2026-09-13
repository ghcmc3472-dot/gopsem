# 법무사 시험 기출문제·해설 크롤러

`crawl_beopmusa_exam.py` 는 아래 사이트에서 **법무사 시험** 문제지·정답·총평·해설 첨부파일(PDF/HWP/HWPX/ZIP)을 찾아 내려받습니다.
기본값은 1차 자료만 받고, `--kind` 로 2차 또는 전체를 선택할 수 있습니다.

| 소스 | 설명 |
|------|------|
| `scourt` | 대법원 시험정보 홈페이지 (https://exam.scourt.go.kr). 법무사 안내 페이지에서 `기출문제` 링크를 자동 탐색합니다. |
| `lawschool` | 합격의법학원 법무사 시험정보 게시판. 매 회차 1·2차 문제, 정답가안, 기출해설이 첨부되어 있습니다. |
| `pmg` | 박문각 법무사 기출문제 게시판(bbsid=4282)과 2차 자료실(bbsid=2488). 첨부는 att.pmg.co.kr 정적 PDF 직링크입니다. |

## 실행 방법 (사용자 PC에서)

```bash
pip install -r crawler/requirements.txt
python3 crawler/crawl_beopmusa_exam.py                # 모든 소스, 1차 자료만
python3 crawler/crawl_beopmusa_exam.py --kind 2cha    # 2차 자료만
python3 crawler/crawl_beopmusa_exam.py --kind all     # 1차·2차·총평·해설 전부
python3 crawler/crawl_beopmusa_exam.py --source pmg --dry-run   # 박문각만, 목록만 확인
python3 crawler/crawl_beopmusa_exam.py --source scourt --start-url "<기출문제 게시판 URL>"
```

결과는 기본적으로 `data/beopmusa_gichul/제NN회/{1차|2차|기타}/` 아래에 저장되고, 게시글·첨부 목록은
`manifest.json` 에 기록됩니다. 파일명 앞에는 소스 이름(`scourt_`, `lawschool_`, `pmg_`)이 붙습니다.

### 옵션

| 옵션 | 기본값 | 설명 |
|------|--------|------|
| `--source` | `all` | `scourt`, `lawschool`, `pmg`, `all` |
| `--kind` | `1cha` | `1cha`: 1차 자료만, `2cha`: 2차 자료만, `all`: 시험 관련 글 전부(총평·해설 포함) |
| `--start-url` | (자동 탐색) | 게시판 목록 URL 을 직접 지정 (`--source` 하나일 때) |
| `--out` | `data/beopmusa_gichul` | 저장 폴더 |
| `--max-pages` | 30 | 목록 페이지 최대 순회 수 |
| `--delay` | 1.0 | 요청 간 대기 시간(초) |
| `--dry-run` | 꺼짐 | 다운로드 없이 목록만 출력 |

### 게시글 선별 규칙

- 제목에서 `1차`/`2차` 를 찾아 종류를 정합니다. 법무사 전용 게시판(`lawschool`, `pmg`)은 제목에 "법무사"가 없어도
  수집하고, 그 외 게시판은 "법무사"가 있는 글만 봅니다.
- `--kind all` 은 제목에 기출·문제·정답·해설·총평·시험·답안 중 하나가 있는 글만 받습니다(정오표·이벤트 제외).
- 페이지네이션 파라미터(`pageNo`, `page` 등)는 목록 페이지의 링크에서 자동 감지합니다.

### 대법원 사이트 게시판 URL 을 못 찾는 경우

사이트 개편으로 메뉴 구조가 바뀌면 자동 탐색이 실패할 수 있습니다. 브라우저에서
https://exam.scourt.go.kr 접속 → `법무사` → `기출문제` 게시판 목록 URL 을 복사해
`--source scourt --start-url "<URL>"` 로 넘기면 됩니다.

## 참고

이 스크립트는 네트워크 외부 접속이 차단된 원격 환경에서 작성·로컬 모의 서버로 검증되었고,
실제 사이트를 상대로는 사용자 PC에서 실행해야 합니다.

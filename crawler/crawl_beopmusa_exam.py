#!/usr/bin/env python3
"""법무사 제1차 시험 기출문제 크롤러.

대법원 시험정보 홈페이지(exam.scourt.go.kr)와, 보조 소스로 합격의법학원
법무사 게시판(judicial.lawschool.co.kr)에서 "법무사 제1차 시험" 문제지/정답
첨부파일(PDF/HWP/HWPX/ZIP)을 찾아 내려받는다.

사용 예:
    python3 crawler/crawl_beopmusa_exam.py                  # 모든 소스, 기본 출력 폴더
    python3 crawler/crawl_beopmusa_exam.py --source scourt  # 대법원 사이트만
    python3 crawler/crawl_beopmusa_exam.py --dry-run        # 다운로드 없이 목록만 출력
    python3 crawler/crawl_beopmusa_exam.py --start-url "https://exam.scourt.go.kr/wex/..."  # 게시판 URL 직접 지정

의존성: requests, beautifulsoup4, lxml  (pip install -r crawler/requirements.txt)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qs, urlencode, urlunparse, unquote

import requests
from bs4 import BeautifulSoup

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
ATTACH_EXT = (".pdf", ".hwp", ".hwpx", ".zip", ".doc", ".docx")
ATTACH_HREF_HINT = re.compile(r"(down|file|attach|atch)", re.I)

# 게시글 제목 필터: "법무사" 와 "1차" 가 모두 들어간 글만 대상으로 한다.
TITLE_RE = re.compile(r"법무사.*(제\s*1\s*차|1\s*차)|(제\s*1\s*차|1\s*차).*법무사", re.S)
ROUND_RE = re.compile(r"제\s*(\d{1,3})\s*회")
YEAR_RE = re.compile(r"(20\d{2})\s*년")


@dataclass
class Source:
    name: str
    home_url: str
    list_url: str | None = None          # 알려진 목록 URL (없으면 home 에서 '기출문제' 링크 탐색)
    page_param: str = "pageNo"           # 페이지네이션 쿼리 파라미터
    encoding: str | None = None          # 강제 인코딩 (예: euc-kr)


SOURCES: dict[str, Source] = {
    # 대법원 시험정보 홈페이지. 기출문제 게시판 URL 은 개편으로 바뀔 수 있어
    # 홈/법무사 안내 페이지에서 '기출문제' 링크를 찾아 들어간다.
    "scourt": Source(
        name="scourt",
        home_url="https://exam.scourt.go.kr/wex/exinfo/info03.jsp",
        list_url=None,
        page_param="pageNo",
    ),
    # 합격의법학원 법무사 시험정보 게시판 (매 회차 1차 문제 및 정답가안 첨부).
    "lawschool": Source(
        name="lawschool",
        home_url="https://judicial.lawschool.co.kr/",
        list_url=(
            "https://judicial.lawschool.co.kr/nlawschool/board/infomation/"
            "list.asp?field=12&mnNum=5&subMnNum=1&bbsCode=303"
        ),
        page_param="pageNo",
        encoding="euc-kr",
    ),
}


@dataclass
class Post:
    source: str
    title: str
    url: str
    round_no: int | None = None
    year: int | None = None
    attachments: list[dict] = field(default_factory=list)


class Crawler:
    def __init__(self, out_dir: Path, delay: float = 1.0, dry_run: bool = False,
                 max_pages: int = 30, verbose: bool = True):
        self.out_dir = out_dir
        self.delay = delay
        self.dry_run = dry_run
        self.max_pages = max_pages
        self.verbose = verbose
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "ko,en;q=0.8"})
        self.posts: list[Post] = []

    # ---------- HTTP ----------
    def get(self, url: str, encoding: str | None = None, **kw) -> requests.Response:
        time.sleep(self.delay)
        r = self.session.get(url, timeout=30, **kw)
        r.raise_for_status()
        if encoding:
            r.encoding = encoding
        elif not r.encoding or r.encoding.lower() == "iso-8859-1":
            r.encoding = r.apparent_encoding
        return r

    def soup(self, url: str, encoding: str | None = None) -> BeautifulSoup:
        return BeautifulSoup(self.get(url, encoding).text, "lxml")

    def log(self, *a):
        if self.verbose:
            print(*a, file=sys.stderr)

    # ---------- discovery ----------
    def find_list_url(self, src: Source) -> str | None:
        """홈/안내 페이지에서 '기출문제' 링크를 찾는다."""
        if src.list_url:
            return src.list_url
        try:
            page = self.soup(src.home_url, src.encoding)
        except requests.RequestException as e:
            self.log(f"[{src.name}] 홈 페이지 접근 실패: {e}")
            return None
        for a in page.find_all("a", href=True):
            text = a.get_text(" ", strip=True)
            if "기출문제" in text or "기출" in a.get("title", ""):
                href = a["href"]
                if href.startswith(("javascript:", "#")):
                    continue
                return urljoin(src.home_url, href)
        # 메뉴가 JS 로 그려지는 경우를 대비해 소스 내 URL 패턴도 훑는다.
        m = re.search(r"['\"]([^'\"]*(?:past|gichul|exam_?q|qbank)[^'\"]*)['\"]", page.decode(), re.I)
        if m:
            return urljoin(src.home_url, m.group(1))
        return None

    # ---------- list pages ----------
    @staticmethod
    def with_page(url: str, param: str, page: int) -> str:
        p = urlparse(url)
        q = parse_qs(p.query, keep_blank_values=True)
        q[param] = [str(page)]
        return urlunparse(p._replace(query=urlencode(q, doseq=True)))

    def iter_posts(self, src: Source, list_url: str):
        seen: set[str] = set()
        for page in range(1, self.max_pages + 1):
            url = self.with_page(list_url, src.page_param, page)
            self.log(f"[{src.name}] 목록 {page}페이지: {url}")
            try:
                doc = self.soup(url, src.encoding)
            except requests.RequestException as e:
                self.log(f"[{src.name}] 목록 페이지 실패: {e}")
                break
            new = 0
            for a in doc.find_all("a", href=True):
                title = a.get_text(" ", strip=True)
                if not title or not TITLE_RE.search(title):
                    continue
                href = a["href"]
                if href.startswith(("javascript:", "#")):
                    href = self._href_from_js(href, doc)
                    if not href:
                        continue
                post_url = urljoin(url, href)
                if post_url in seen:
                    continue
                seen.add(post_url)
                new += 1
                rm = ROUND_RE.search(title)
                ym = YEAR_RE.search(title)
                yield Post(
                    source=src.name,
                    title=title,
                    url=post_url,
                    round_no=int(rm.group(1)) if rm else None,
                    year=int(ym.group(1)) if ym else None,
                )
            if new == 0:
                # 이 페이지에 새 글이 없으면 마지막 페이지로 본다.
                break

    @staticmethod
    def _href_from_js(href: str, doc: BeautifulSoup) -> str | None:
        """javascript:view('123') 형태에서 숫자 인자를 뽑아 view URL 로 만든다."""
        m = re.search(r"\(\s*['\"]?([\w-]+)['\"]?", href)
        if not m:
            return None
        form = doc.find("form")
        action = form.get("action") if form else None
        if not action:
            return None
        return f"{action}?num={m.group(1)}"

    # ---------- detail / attachments ----------
    def find_attachments(self, post: Post, src: Source) -> list[dict]:
        try:
            doc = self.soup(post.url, src.encoding)
        except requests.RequestException as e:
            self.log(f"[{src.name}] 상세 페이지 실패 ({post.title}): {e}")
            return []
        found: list[dict] = []
        for a in doc.find_all("a", href=True):
            href = a["href"]
            text = a.get_text(" ", strip=True)
            low_href, low_text = href.lower(), text.lower()
            is_attach = (
                low_href.endswith(ATTACH_EXT)
                or low_text.endswith(ATTACH_EXT)
                or ATTACH_HREF_HINT.search(href) is not None
            )
            if not is_attach or href.startswith(("javascript:", "#")):
                continue
            found.append({"name": text or Path(urlparse(href).path).name, "url": urljoin(post.url, href)})
        # 중복 제거 (같은 URL)
        uniq = {}
        for f in found:
            uniq.setdefault(f["url"], f)
        return list(uniq.values())

    @staticmethod
    def filename_from_response(r: requests.Response, fallback: str) -> str:
        cd = r.headers.get("Content-Disposition", "")
        m = re.search(r"filename\*\s*=\s*([^']*)''([^;]+)", cd, re.I)
        if m:
            return unquote(m.group(2))
        m = re.search(r'filename\s*=\s*"?([^";]+)"?', cd, re.I)
        if m:
            raw = m.group(1).strip()
            # 한글 파일명이 latin-1 로 깨져 온 경우 복원 시도
            for enc in ("utf-8", "euc-kr", "cp949"):
                try:
                    return raw.encode("latin-1").decode(enc)
                except (UnicodeEncodeError, UnicodeDecodeError):
                    continue
            return unquote(raw)
        return fallback

    def download(self, post: Post, att: dict) -> Path | None:
        sub = self.out_dir
        if post.round_no:
            sub = sub / f"제{post.round_no:02d}회"
        elif post.year:
            sub = sub / str(post.year)
        else:
            sub = sub / "기타"
        sub.mkdir(parents=True, exist_ok=True)
        try:
            r = self.get(att["url"], stream=True, headers={"Referer": post.url})
        except requests.RequestException as e:
            self.log(f"  다운로드 실패 {att['url']}: {e}")
            return None
        name = self.filename_from_response(r, att["name"] or Path(urlparse(att["url"]).path).name or "file")
        name = re.sub(r'[\\/:*?"<>|]', "_", name).strip() or "file"
        if not name.lower().endswith(ATTACH_EXT):
            ctype = r.headers.get("Content-Type", "")
            if "pdf" in ctype:
                name += ".pdf"
            elif "hwp" in ctype or "haansoft" in ctype:
                name += ".hwp"
        dest = sub / f"{post.source}_{name}"
        if dest.exists() and dest.stat().st_size > 0:
            self.log(f"  이미 있음: {dest}")
            return dest
        with open(dest, "wb") as fh:
            for chunk in r.iter_content(1 << 16):
                fh.write(chunk)
        self.log(f"  저장: {dest} ({dest.stat().st_size:,} bytes)")
        return dest

    # ---------- run ----------
    def run(self, src: Source, start_url: str | None = None) -> int:
        list_url = start_url or self.find_list_url(src)
        if not list_url:
            self.log(f"[{src.name}] 기출문제 게시판 URL 을 찾지 못했습니다. --start-url 로 직접 지정하세요.")
            return 0
        count = 0
        for post in self.iter_posts(src, list_url):
            self.log(f"[{src.name}] 게시글: {post.title} -> {post.url}")
            post.attachments = self.find_attachments(post, src)
            for att in post.attachments:
                self.log(f"  첨부: {att['name']} ({att['url']})")
                if not self.dry_run:
                    saved = self.download(post, att)
                    att["saved_to"] = str(saved) if saved else None
                    if saved:
                        count += 1
            self.posts.append(post)
        return count

    def write_manifest(self):
        self.out_dir.mkdir(parents=True, exist_ok=True)
        path = self.out_dir / "manifest.json"
        with open(path, "w", encoding="utf-8") as fh:
            json.dump([asdict(p) for p in self.posts], fh, ensure_ascii=False, indent=2)
        self.log(f"manifest: {path}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="법무사 제1차 시험 기출문제 다운로드")
    ap.add_argument("--source", choices=["all", *SOURCES], default="all")
    ap.add_argument("--start-url", help="게시판 목록 URL 을 직접 지정 (source 하나일 때만 적용)")
    ap.add_argument("--out", default="data/beopmusa_1cha_gichul", help="저장 폴더")
    ap.add_argument("--max-pages", type=int, default=30)
    ap.add_argument("--delay", type=float, default=1.0, help="요청 간 대기(초)")
    ap.add_argument("--dry-run", action="store_true", help="다운로드 없이 목록만 출력")
    args = ap.parse_args(argv)

    names = list(SOURCES) if args.source == "all" else [args.source]
    if args.start_url and len(names) != 1:
        ap.error("--start-url 은 --source 를 하나로 지정했을 때만 사용할 수 있습니다.")

    crawler = Crawler(Path(args.out), delay=args.delay, dry_run=args.dry_run, max_pages=args.max_pages)
    total = 0
    for n in names:
        total += crawler.run(SOURCES[n], args.start_url)
    crawler.write_manifest()
    print(f"게시글 {len(crawler.posts)}건, 파일 {total}개 저장 (폴더: {args.out})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

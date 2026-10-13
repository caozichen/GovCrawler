"""Official community recruitment discovery, evidence and incremental state.

Uses only the standard library. Attachment contents and personal eligibility
are deliberately left to a separate analysis step.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlsplit

if __package__ == "crawlers.lib":
    from .. import beijing_hr_recruitment as common
else:
    import beijing_hr_recruitment as common

TZ = common.BEIJING_TIMEZONE
COMMUNITY = re.compile(r"社区(?:专职)?(?:党务)?工作者|专职社区工作者")
CANDIDATE = re.compile(r"招聘|招考|招录|补招|补录|报名|延期|更正|取消|笔试|面试|资格复审")
EXCLUDED = re.compile(r"培训|职业资格考试|职业水平考试|能力提升|表彰|先进事迹")
DATE_TOKEN = re.compile(
    r"(?:(?P<year>20\d{2})年)?(?:(?P<month>\d{1,2})月)?(?P<day>\d{1,2})日"
    r"(?:[（(][^）)]+[）)])?"
    r"(?:(?P<period>上午|下午|晚上)?(?P<hour>\d{1,2})[:：时](?P<minute>\d{1,2})?(?:分)?)?"
)
SLOT = re.compile(r"(上午|下午|晚上)?(\d{1,2})[:：时](\d{1,2})(?:分)?[—－\-~～至]+(\d{1,2})[:：时](\d{1,2})")


@dataclass(frozen=True)
class Site:
    id: str
    district: str
    url: str
    sections: tuple[str, ...]

    def allowed(self, url: str, kind: str) -> bool:
        parsed = urlsplit(url)
        expected = urlsplit(self.url)
        if (parsed.scheme != expected.scheme or parsed.hostname != expected.hostname
                or parsed.port not in (None, 80 if parsed.scheme == "http" else 443)
                or parsed.username or parsed.password or parsed.fragment):
            return False
        if kind == "attachment":
            prefixes = self.sections if self.id == "dongcheng" else ("/userfiles/file/",)
            return (any(parsed.path.lower().startswith(p.lower()) for p in prefixes)
                    and Path(parsed.path).suffix.lower() in common.DOWNLOAD_EXTENSIONS)
        if parsed.query:
            return False
        for section in self.sections:
            if not parsed.path.startswith(section):
                continue
            suffix = parsed.path[len(section):]
            if kind == "list" and re.fullmatch(r"(?:index(?:_\d+)?\.html)?", suffix):
                return True
            pattern = r"\d{6}/t\d{8}_\d+\.html" if self.id == "dongcheng" else r"[0-9a-f]{32}\.html"
            if kind == "article" and re.fullmatch(pattern, suffix, re.I):
                return True
        return False


DONGCHENG = Site("dongcheng", "东城区", "https://www.bjdch.gov.cn/zwgk/tzgg/",
                 ("/zwgk/tzgg/", "/zwgk/rsxx/sydwzlxx/"))
CHAOYANG = Site("chaoyang", "朝阳区", "http://www.bjchy.gov.cn/affair/civilemployed/zhk/",
               ("/affair/civilemployed/zhk/",))
SEARCH_ENDPOINT = "https://www.bjdch.gov.cn/so/ss/query/s"
SEARCH_PAGE = "https://www.bjdch.gov.cn/so/s"
SEARCH_QUERY = ('("社区工作者" OR "社区专职工作者" OR "社区党务工作者" '
                'OR "社区专职党务工作者" OR "专职社区工作者")')


class CommunityHttpClient(common.HttpClient):
    """Reuse the existing GET retry/throttle logic for the official form search."""

    def search(self, params: dict) -> common.HttpResponse:
        return self.get(SEARCH_ENDPOINT + "?" + urlencode(params), referer=SEARCH_PAGE)

    def _request(self, url: str, referer: str | None):
        if url.startswith(SEARCH_ENDPOINT + "?"):
            request = super()._request(SEARCH_ENDPOINT, referer)
            request.add_header("Content-Type", "application/x-www-form-urlencoded; charset=UTF-8")
            request.data = urlsplit(url).query.encode("ascii")
            return request
        return super()._request(url, referer)


@dataclass(frozen=True)
class Entry:
    title: str
    url: str
    published_date: str | None
    list_page_url: str
    pinned: bool = False


class ListParser(HTMLParser):
    def __init__(self, site: Site, url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.site, self.url = site, url
        self.found = False
        self.depth = 0
        self.item: dict | None = None
        self.link_depth = 0
        self.entries: list[Entry] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        attr = dict(attrs)
        classes = (attr.get("class") or "").split()
        if not self.depth:
            if tag == "ul" and ("list" in classes or "news_list" in classes):
                self.depth, self.found = 1, True
            return
        if tag not in common.VOID_TAGS:
            self.depth += 1
        if tag == "li":
            self.item = {"parts": [], "title": [], "href": "", "pinned": bool(attr.get("data-zd"))}
        if self.item is not None and tag == "a":
            href = urljoin(self.url, (attr.get("href") or "").strip())
            if self.site.allowed(href, "article"):
                self.item["href"] = href
                self.item["full_title"] = attr.get("title")
                self.link_depth = 1
        elif self.link_depth and tag not in common.VOID_TAGS:
            self.link_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if self.link_depth:
            self.link_depth -= 1
        if tag == "li" and self.item is not None:
            item = self.item
            title = item.get("full_title") or "".join(item["title"])
            published = common.parse_iso_date("".join(item["parts"]))
            if item["href"] and title.strip():
                self.entries.append(Entry(common.normalize_inline_text(title), item["href"],
                                          published.isoformat() if published else None,
                                          self.url, item["pinned"]))
            self.item, self.link_depth = None, 0
        if self.depth and tag not in common.VOID_TAGS:
            self.depth -= 1

    def handle_data(self, text: str) -> None:
        if self.item is not None:
            self.item["parts"].append(text)
            if self.link_depth:
                self.item["title"].append(text)

    def handle_startendtag(self, tag: str, attrs: list) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in common.VOID_TAGS:
            self.handle_endtag(tag)


def parse_list(html: str, site: Site, url: str) -> tuple[list[Entry], int]:
    parser = ListParser(site, url)
    parser.feed(html)
    parser.close()
    if not parser.found:
        raise ValueError(f"招聘列表容器缺失，页面可能改版：{url}")
    count = re.search(r"Pager\(\s*\{\s*size\s*:\s*(\d+)", html)
    if not count:
        count = re.search(r"共\s*(\d+)\s*页", html)
    if not count:
        raise ValueError(f"无法确认分页数量：{url}")
    total = int(count.group(1))
    if total < 1 or not parser.entries:
        raise ValueError(f"列表没有有效公告，无法确认采集完整性：{url}")
    return parser.entries, total


class ArticleParser(HTMLParser):
    def __init__(self, site: Site, url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.site, self.url = site, url
        self.meta: dict[str, str] = {}
        self.depth, self.ignore = 0, 0
        self.found = False
        self.parts: list[str] = []
        self.links: list[tuple[str, str]] = []
        self.link: tuple[str, list[str]] | None = None

    def handle_starttag(self, tag: str, attrs: list) -> None:
        attr = dict(attrs)
        if tag == "meta":
            self.meta[(attr.get("name") or "").lower()] = attr.get("content") or ""
        if not self.depth:
            if attr.get("id", "").lower() == "maintext" or "content_article" in (attr.get("class") or "").split():
                self.depth, self.found = 1, True
            return
        if tag not in common.VOID_TAGS:
            self.depth += 1
        if self.ignore:
            if tag not in common.VOID_TAGS:
                self.ignore += 1
            return
        if tag in common.IGNORED_BODY_TAGS or attr.get("id", "").lower() in ("qrbox", "zc"):
            self.ignore = 1
            return
        if tag in common.BLOCK_TAGS or tag == "br":
            self.parts.append("\n")
        if tag == "a":
            self.link = (urljoin(self.url, (attr.get("href") or "").strip()), [])

    def handle_endtag(self, tag: str) -> None:
        if not self.depth:
            return
        if self.ignore:
            if tag not in common.VOID_TAGS:
                self.ignore -= 1
        else:
            if tag in common.BLOCK_TAGS:
                self.parts.append("\n")
            if tag == "a" and self.link:
                self.links.append((self.link[0], common.normalize_inline_text("".join(self.link[1]))))
                self.link = None
        if tag not in common.VOID_TAGS:
            self.depth -= 1

    def handle_data(self, text: str) -> None:
        if self.depth and not self.ignore:
            self.parts.append(text)
            if self.link:
                self.link[1].append(text)

    def handle_startendtag(self, tag: str, attrs: list) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in common.VOID_TAGS:
            self.handle_endtag(tag)


def parse_article(html: str, site: Site, entry: Entry) -> tuple[common.ArticleData, list[str]]:
    parser = ArticleParser(site, entry.url)
    parser.feed(html)
    parser.close()
    text = common.normalize_multiline_text("".join(parser.parts))
    if not parser.found or not text:
        raise ValueError(f"招聘正文缺失，页面可能改版：{entry.url}")
    links = dict(parser.links)
    attachments = [common.AttachmentLink(label or Path(urlsplit(url).path).stem, url,
                                        Path(urlsplit(url).path).name)
                   for url, label in links.items() if site.allowed(url, "attachment")]
    article = common.ArticleData(parser.meta.get("articletitle") or entry.title,
                                 common.parse_iso_date(parser.meta.get("pubdate", ""))
                                 or common.parse_iso_date(entry.published_date or ""),
                                 parser.meta.get("contentsource") or site.district + "人民政府",
                                 text, attachments)
    return article, list(links)


def classify(title: str, text: str) -> str | None:
    if not COMMUNITY.search(title + "\n" + text) or EXCLUDED.search(title):
        return None
    if re.search(r"面向.*(?:社区工作者|社区党组织书记).*定向招聘.*(?:事业编制|事业单位)", title):
        return None  # Recruitment FROM existing community staff is a different job type.
    for pattern, kind in ((r"取消|撤销", "cancellation"), (r"延期|延长", "extension"),
                          (r"更正|调整|变更", "correction"), (r"拟聘|拟录用|录用人员|聘用人员", "result"),
                          (r"成绩", "scores"), (r"面试", "interview"),
                          (r"笔试", "exam"), (r"资格复审|资格审核", "qualification")):
        if re.search(pattern, title):
            return kind
    if re.search(r"体检|考察", title):
        return "assessment"
    if re.search(r"补录|递补", title):
        return "recruitment" if re.search(r"重新报名|接受.*报名|提交报考申请|报名时间", text) else "replacement"
    return "recruitment" if re.search(r"招聘|招考|招录|补招", title) else None


def recruitment_year(title: str) -> int | None:
    found = re.findall(r"(20\d{2})年(?:度)?", title)
    return int(found[-1]) if found else None


def parse_windows(text: str, published: date | None, year_hint: int | None = None) -> list[dict]:
    """Retain precision and evidence; never invent a deadline clock time."""
    windows: list[dict] = []
    for line in text.splitlines():
        if not re.search(r"报名|报考申请", line):
            continue
        if re.search(r"打印|准考证|笔试|面试|成绩查询", line) and not re.search(r"报名时间|报名截止", line):
            continue
        if re.search(r"查询.*(?:资格|审核)|资格(?:审核|复审|审查)(?:时间|安排)|体检安排", line):
            continue
        compact = re.sub(r"\s+", "", line)
        compact = re.sub(r"(20\d{2})-(\d{1,2})-(\d{1,2})", r"\1年\2月\3日", compact)
        matches = list(DATE_TOKEN.finditer(compact))
        if not matches:
            continue
        if year_hint and published and year_hint != published.year and not any(m["year"] for m in matches):
            continue  # Recruitment year and publication year differ: do not guess.
        slots: list[dict] = []
        for period, sh, sm, eh, em in SLOT.findall(compact):
            start_hour, end_hour = int(sh), int(eh)
            if period in ("下午", "晚上"):
                start_hour += 12 if start_hour < 12 else 0
                end_hour += 12 if end_hour < 12 else 0
            if start_hour < 24 and end_hour < 24 and int(sm) < 60 and int(em) < 60:
                slots.append({"start": f"{start_hour:02d}:{int(sm):02d}", "end": f"{end_hour:02d}:{int(em):02d}"})
        daily = bool(slots and re.search(r"上午|下午|每天|每日", compact))
        values: list[dict] = []
        year = published.year if published else None
        month = None
        for match in matches:
            year = int(match["year"]) if match["year"] else year
            month = int(match["month"]) if match["month"] else month
            if year is None or month is None:
                continue
            try:
                day = date(year, month, int(match["day"]))
                hour = int(match["hour"]) if match["hour"] and not daily else None
                minute = int(match["minute"] or 0)
                if hour is not None and match["period"] in ("下午", "晚上") and hour < 12:
                    hour += 12
                stamp = datetime(day.year, day.month, day.day, hour or 0, minute if hour is not None else 0, tzinfo=TZ)
                values.append({"value": stamp.isoformat() if hour is not None else day.isoformat(),
                               "precision": "minute" if hour is not None else "date"})
            except ValueError:
                continue
        if len(values) == 2:
            if values[1]["value"][:10] < values[0]["value"][:10]:
                continue  # An omitted year across December/January needs confirmation.
            windows.append({"start": values[0], "end": values[1], "daily_hours": slots if daily else [], "evidence": line})
        elif len(values) == 1:
            endpoint = "end" if re.search(r"截止|截至|延长至|延期至", compact) else "start"
            windows.append({"start": None, "end": None, endpoint: values[0], "daily_hours": [], "evidence": line})
    # Separate, explicitly named opening/deadline lines can form one window.
    starts = [w for w in windows if w["start"] and not w["end"]]
    ends = [w for w in windows if w["end"] and not w["start"]]
    if len(windows) == 2 and len(starts) == len(ends) == 1:
        windows = [{"start": starts[0]["start"], "end": ends[0]["end"], "daily_hours": [],
                    "evidence": starts[0]["evidence"] + "\n" + ends[0]["evidence"]}]
    unique = {json.dumps(w, sort_keys=True, ensure_ascii=False): w for w in windows}
    return list(unique.values())


def application_status(windows: list[dict], now: datetime) -> str:
    statuses = []
    for window in windows:
        start, end = window.get("start"), window.get("end")
        today = now.date().isoformat()
        if end and (end["value"][:10] < today or (end["precision"] == "minute" and datetime.fromisoformat(end["value"]) < now)):
            statuses.append("closed")
        elif start and (start["value"][:10] > today or (start["precision"] == "minute" and datetime.fromisoformat(start["value"]) > now)):
            statuses.append("upcoming")
        elif not start or not end or (end["precision"] == "date" and end["value"] == today and not window.get("daily_hours")):
            statuses.append("unknown")
        elif window.get("daily_hours"):
            clock = now.strftime("%H:%M")
            hours = window["daily_hours"]
            if any(slot["start"] <= clock <= slot["end"] for slot in hours):
                statuses.append("open")
            elif today < end["value"][:10] or any(clock < slot["start"] for slot in hours):
                statuses.append("upcoming")
            else:
                statuses.append("closed")
        else:
            statuses.append("open")
    for status in ("open", "unknown", "upcoming"):
        if status in statuses:
            return status
    return "closed" if statuses else "unknown"


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class State:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS sources (url TEXT PRIMARY KEY, watermark TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS articles (
                url TEXT PRIMARY KEY, entry TEXT NOT NULL, record TEXT, fingerprint TEXT,
                pending INTEGER NOT NULL DEFAULT 1, attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS run_lock (id INTEGER PRIMARY KEY, pid INTEGER NOT NULL);
        """)
        try:
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                lock = self.db.execute("SELECT pid FROM run_lock WHERE id=1").fetchone()
                if lock:
                    try:
                        os.kill(lock["pid"], 0)
                    except ProcessLookupError:
                        pass
                    else:
                        raise ValueError("该地区社区爬虫已有运行进程，请等待完成。")
                self.db.execute("INSERT OR REPLACE INTO run_lock VALUES (1, ?)", (os.getpid(),))
        except BaseException:
            self.db.close()
            raise

    def close(self) -> None:
        with self.db:
            self.db.execute("DELETE FROM run_lock WHERE id=1 AND pid=?", (os.getpid(),))
        self.db.close()

    def discover(self, entry: Entry, now: str) -> None:
        with self.db:
            self.db.execute("""INSERT INTO articles(url,entry,first_seen,last_seen) VALUES (?,?,?,?)
                ON CONFLICT(url) DO UPDATE SET entry=excluded.entry,pending=1,last_seen=excluded.last_seen""",
                (entry.url, json.dumps(asdict(entry), ensure_ascii=False), now, now))

    def rows(self) -> list[sqlite3.Row]:
        return list(self.db.execute("SELECT * FROM articles ORDER BY url"))

    def save(self, url: str, record: dict | None, fingerprint: str | None, pending: bool, error: str | None = None) -> None:
        with self.db:
            self.db.execute("""UPDATE articles SET record=?,fingerprint=?,pending=?,last_error=?,
                attempts=attempts+? WHERE url=?""",
                (json.dumps(record, ensure_ascii=False) if record else None, fingerprint, int(pending), error, int(pending), url))

    def watermark(self, url: str) -> date | None:
        row = self.db.execute("SELECT watermark FROM sources WHERE url=?", (url,)).fetchone()
        return date.fromisoformat(row["watermark"]) if row else None

    def advance(self, url: str, through: date) -> None:
        with self.db:
            self.db.execute("""INSERT INTO sources VALUES (?,?) ON CONFLICT(url)
                DO UPDATE SET watermark=MAX(watermark,excluded.watermark)""", (url, through.isoformat()))


def scan(client, logger, site: Site, source: str, cutoff: date, through: date,
         max_pages: int, output: Path, state: State, now: str) -> dict:
    seen_pages, seen_urls = set(), set()
    total, old_pages, invalid_dates = 1, 0, 0
    previous_date, ordered = None, True
    result = {"url": source, "from_date": cutoff.isoformat(), "until_date": through.isoformat(),
              "kind": "column", "coverage_scope": "available_column_pages",
              "pages_fetched": 0, "candidates": 0, "coverage_complete": False, "stop_reason": "max_pages",
              "earliest_date_seen": None}
    for index in range(max_pages):
        base = source.rsplit("/", 1)[0] + "/" if source.endswith(".html") else source
        url = source if index == 0 else urljoin(base, f"index_{index}.html")
        logger.info("list.fetch.start", "扫描社区招聘栏目。", url=url, page=index + 1)
        response = client.get(url, referer=source)
        if not site.allowed(response.final_url, "list"):
            raise ValueError(f"列表跳转到非目标栏目：{response.final_url}")
        html = common.decode_html(response)
        entries, total = parse_list(html, site, response.final_url)
        signature = digest("\n".join(e.url for e in entries))
        if signature in seen_pages:
            result["stop_reason"] = "pagination_loop"
            break
        seen_pages.add(signature)
        (output / "list-pages").mkdir(parents=True, exist_ok=True)
        common.write_json(output / "list-pages" / f"{digest(source)[:8]}-{index + 1:03d}.json", {"url": url})
        raw = output / "list-pages" / f"{digest(source)[:8]}-{index + 1:03d}.html"
        raw.write_text(html, encoding="utf-8")
        result["pages_fetched"] += 1
        old = []
        for entry in entries:
            if entry.url in seen_urls:
                continue
            seen_urls.add(entry.url)
            published = date.fromisoformat(entry.published_date) if entry.published_date else None
            if published is None:
                invalid_dates += 1
            elif result["earliest_date_seen"] is None or published.isoformat() < result["earliest_date_seen"]:
                result["earliest_date_seen"] = published.isoformat()
            if not entry.pinned:
                old.append(published is not None and published < cutoff)
                if published and previous_date and published > previous_date:
                    ordered = False
                if published:
                    previous_date = published
            if published and not cutoff <= published <= through:
                continue
            if COMMUNITY.search(entry.title) or CANDIDATE.search(entry.title):
                state.discover(entry, now)
                result["candidates"] += 1
        old_pages = old_pages + 1 if old and all(old) else 0
        if index + 1 >= total:
            result.update(coverage_complete=not invalid_dates, stop_reason="last_page")
            break
        if old_pages >= 2 and not invalid_dates and ordered:
            result.update(coverage_complete=True, stop_reason="date_cutoff")
            break
    result["unknown_dates"] = invalid_dates
    result["total_pages_reported"] = total
    return result


def parse_search(response: common.HttpResponse) -> tuple[dict, list[dict]]:
    if response.final_url != SEARCH_ENDPOINT:
        raise ValueError("官网搜索跳转到未知页面。")
    payload = json.loads(common.decode_html(response))
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise ValueError("官网搜索未返回成功结果，可能需要验证或接口已变更。")
    total, current, documents = payload.get("totalHits"), payload.get("currentHits"), payload.get("resultDocs")
    if (type(total) is not int or total < 0 or type(current) is not int
            or not isinstance(documents, list) or current != len(documents) or total < current):
        raise ValueError("官网搜索数量或结果结构异常。")
    if any(not isinstance(d, dict) or not isinstance(d.get("data"), dict)
           or not isinstance(d["data"].get("url"), str)
           or not isinstance(d["data"].get("titleO"), str) for d in documents):
        raise ValueError("官网搜索条目结构异常。")
    return payload, documents


def scan_search(client, logger, site: Site, cutoff: date, through: date,
                max_pages: int, output: Path, state: State, now: str) -> dict:
    result = {"url": SEARCH_ENDPOINT, "kind": "official_search",
              "coverage_scope": "indexed_community_titles_in_target_columns",
              "query": SEARCH_QUERY, "from_date": cutoff.isoformat(), "until_date": through.isoformat(),
              "pages_fetched": 0, "candidates": 0, "coverage_complete": False, "stop_reason": "max_pages"}
    seen, seen_urls, total, invalid_dates = set(), set(), None, 0
    for page in range(1, max_pages + 1):
        params = {"qt": SEARCH_QUERY, "siteCode": "1101010020", "tab": "all", "adv": "1",
                  "keyPlace": "1", "sort": "dateDesc", "page": str(page),
                  "timeOption": "2", "startDateStr": cutoff.isoformat(), "endDateStr": through.isoformat()}
        logger.info("search.fetch.start", "检索官网归档的社区招聘公告。", page=page)
        payload, documents = parse_search(client.search(params))
        raw = output / "search-pages" / f"{page:03d}.json"
        raw.parent.mkdir(parents=True, exist_ok=True)
        common.write_json(raw, {"url": SEARCH_ENDPOINT, "params": params, "response": payload})
        result["pages_fetched"] += 1
        if total is not None and total != payload["totalHits"]:
            result["stop_reason"] = "search_results_changed"
            break
        total = payload["totalHits"]
        identities = [str(d.get("id") or d["data"]["url"]) for d in documents]
        if (not documents and total) or len(set(identities)) != len(identities) or any(i in seen for i in identities):
            result["stop_reason"] = "pagination_loop_or_missing_results"
            break
        seen.update(identities)
        for document in documents:
            data = document["data"]
            url = data["url"]
            title = common.normalize_inline_text(data["titleO"])
            if not site.allowed(url, "article") or not COMMUNITY.search(title) or url in seen_urls:
                continue
            seen_urls.add(url)
            published = common.parse_iso_date(str(data.get("docDate") or ""))
            if not published:
                invalid_dates += 1
            if published and not cutoff <= published <= through:
                continue
            state.discover(Entry(title, url, published.isoformat() if published else None,
                                 SEARCH_PAGE + "?" + urlencode(params)), now)
            result["candidates"] += 1
        if len(seen) >= total:
            result.update(coverage_complete=not invalid_dates, stop_reason="last_page")
            break
    result["total_hits_reported"] = total
    result["results_seen"] = len(seen)
    result["unknown_dates"] = invalid_dates
    return result


def build_record(site: Site, entry: Entry, article, links: list[str], attachments: list[dict],
                 args: argparse.Namespace, now: datetime, output: Path) -> dict:
    published = article.published_date
    kind = classify(article.title, article.text)
    windows = parse_windows(article.text, published, recruitment_year(article.title)) if kind in ("recruitment", "extension", "correction") else []
    warnings = ["attachment_" + str(a.get("status")) for a in attachments if a.get("status") != "downloaded"]
    if kind == "recruitment" and not windows:
        warnings.append("application_time_unknown")
    urls = [url for line in article.text.splitlines() if re.search(r"报名|报考申请", line)
            for url in re.findall(r"https?://[^\s<>（）()，。；\"']+", line)]
    urls += [url for url in links if urlsplit(url).hostname in ("www.gongzhao.net", "gongzhao.net", "www.365sheq.com")]
    application_urls = list(dict.fromkeys(u for u in urls if urlsplit(u).hostname
                                         and not site.allowed(u, "attachment") and not site.allowed(u, "article")))
    return {
        "schema_version": "1.0", "record_id": digest(entry.url), "run_id": args.run_id,
        "task_id": site.id + "_community_recruitment", "source_url": entry.url,
        "published_date": published.isoformat() if published else None, "publisher": article.source,
        "title": article.title, "text": article.text, "content_text": article.text,
        "status": "completed_with_warnings" if warnings else "ok", "attachments": attachments,
        "source": {"url": entry.url, "list_page_url": entry.list_page_url,
                   "published_at": published.isoformat() if published else None, "fetched_at": now.isoformat()},
        "provenance": {"crawler": args.script_name, "raw_html_path": f"articles/{digest(entry.url)[:20]}.html",
                       "content_hash": "sha256:" + digest(article.text), "output_dir": str(output)},
        "quality": {"parse_ok": True, "warnings": warnings},
        "recruitment": {"district": site.district, "recruitment_year": recruitment_year(article.title),
                        "announcement_type": kind, "campaign_id": digest(entry.url) if kind == "recruitment" else None,
                        "application_windows": windows,
                        "application_status": application_status(windows, now) if kind == "recruitment" else None,
                        "application_urls": application_urls, "related_urls": links,
                        "position_count": None, "vacancy_count": None},
    }


def build_catalog(records: list[dict], now: datetime) -> dict:
    plans, duplicates, changes = [], {}, {}
    for record in records:
        rec = record["recruitment"]
        if rec["announcement_type"] != "recruitment":
            continue
        key = digest(record["title"] + record["text"])
        if key in duplicates:
            duplicates[key]["sources"].append(record["source_url"])
            continue
        plan = {"campaign_id": record["record_id"], "title": record["title"],
                "published_date": record["published_date"], "sources": [record["source_url"]],
                "recruitment_year": rec["recruitment_year"], "district": rec["district"],
                "application_windows": rec["application_windows"], "application_urls": rec["application_urls"],
                "application_status": application_status(rec["application_windows"], now),
                "events": [], "warnings": list(record["quality"]["warnings"]),
                "last_verified_at": record["source"]["fetched_at"], "position_count": None, "vacancy_count": None}
        duplicates[key] = plan
        plans.append(plan)
    unlinked = []
    for record in sorted(records, key=lambda r: (r["published_date"] or "", r["source_url"])):
        rec = record["recruitment"]
        kind = rec["announcement_type"]
        if kind == "recruitment":
            continue
        candidates = [p for p in plans if any(url in rec["related_urls"] for url in p["sources"])]
        if not candidates:
            candidates = [p for p in plans if rec["recruitment_year"] == p["recruitment_year"]
                          and re.sub(r"公告$", "", p["title"]) in record["title"]]
        if len(candidates) != 1:
            unlinked.append({"url": record["source_url"], "title": record["title"], "type": kind})
            if kind in ("extension", "correction", "cancellation"):
                for candidate in candidates:
                    candidate["warnings"].append("ambiguous_application_change")
            continue
        plan = candidates[0]
        event = {"url": record["source_url"], "title": record["title"], "type": kind,
                 "published_date": record["published_date"]}
        plan["events"].append(event)
        rec["campaign_id"] = plan["campaign_id"]
        if kind == "cancellation":
            plan["application_status"] = "cancelled"
        elif kind in ("extension", "correction"):
            windows = rec["application_windows"]
            change_key = (plan["campaign_id"], record["published_date"])
            signature = json.dumps(windows, sort_keys=True)
            if change_key in changes and changes[change_key] != signature:
                plan["warnings"].append("conflicting_application_changes")
            changes[change_key] = signature
            if len(windows) == 1 and len(plan["application_windows"]) == 1:
                previous = plan["application_windows"][0]
                revised = dict(windows[0])
                revised["start"] = revised["start"] or previous["start"]
                revised["end"] = revised["end"] or previous["end"]
                plan["application_windows"] = [revised]
                plan["application_status"] = application_status([revised], now)
            else:
                plan["warnings"].append("application_change_requires_review")
                plan["application_status"] = "unknown"
    for plan in plans:
        if any(event["type"] == "cancellation" for event in plan["events"]):
            plan["application_status"] = "cancelled"
        elif any(w in plan["warnings"] for w in ("ambiguous_application_change", "conflicting_application_changes",
                                                  "application_change_requires_review")):
            plan["application_status"] = "unknown"
    return {"as_of": now.isoformat(), "campaigns": plans, "unlinked_events": unlinked,
            "counts": {status: sum(p["application_status"] == status for p in plans)
                       for status in ("open", "upcoming", "closed", "cancelled", "unknown")}}


def build_argument_parser(site: Site) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=site.district + "社区工作者招聘（跨年增量采集）")
    parser.add_argument("--url", default=site.url)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--mode", choices=("auto", "incremental", "backfill"), default="auto")
    parser.add_argument("--lookback-days", type=int, default=730)
    parser.add_argument("--overlap-days", type=int, default=7)
    parser.add_argument("--from-date", type=date.fromisoformat)
    parser.add_argument("--until-date", type=date.fromisoformat)
    parser.add_argument("--max-pages", type=int, default=200)
    parser.add_argument("--state-dir", type=Path, default=Path(__file__).resolve().parents[2] / ".crawler-console/community-recruitment")
    parser.add_argument("--request-delay", type=float, default=0.5)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--max-attachment-mb", type=int, default=20)
    parser.add_argument("--skip-attachments", action="store_true")
    parser.add_argument("--log-level", choices=tuple(common.LOG_LEVELS), default="INFO")
    return parser


def validate_arguments(site: Site, args: argparse.Namespace) -> None:
    if not site.allowed(args.url, "list"):
        raise ValueError("任务网址必须为本地区官方目标栏目。")
    if args.mode == "incremental" and args.from_date:
        raise ValueError("--from-date只能用于auto或backfill模式。")
    if args.from_date and args.until_date and args.from_date > args.until_date:
        raise ValueError("起始日期不能晚于截止日期。")
    if args.from_date and args.from_date > datetime.now(TZ).date():
        raise ValueError("起始日期不能在未来。")
    if args.until_date and args.until_date > datetime.now(TZ).date():
        raise ValueError("不能采集未来日期。")
    if not args.state_dir.is_absolute():
        raise ValueError("--state-dir必须使用绝对路径。")
    if not (1 <= args.lookback_days <= 3660 and 0 <= args.overlap_days <= 365
            and 1 <= args.max_pages <= 2000 and 1 <= args.timeout <= 300
            and 0 <= args.retries <= 10 and 0 <= args.request_delay <= 60
            and 1 <= args.max_attachment_mb <= 500):
        raise ValueError("回溯、分页、超时或附件参数超出允许范围。")


def run(site: Site, args: argparse.Namespace, logger, client=None, now: datetime | None = None) -> int:
    now = now or datetime.now(TZ)
    output = Path(args.output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    logger.attach_event_log(output / "crawler-events.jsonl")
    args.script_name = site.id + "_community_recruitment.py"
    client = client or CommunityHttpClient(args.timeout, args.retries, args.request_delay, logger,
                                         lambda u: site.allowed(u, "attachment"))
    state = State(args.state_dir / (site.id + ".sqlite"))
    records, errors, scans = [], [], []
    new_count = updated_count = 0
    try:
        sources = [args.url]
        if site.id == "dongcheng" and len(site.sections) > 1 and urlsplit(args.url).path.startswith(site.sections[0]):
            sources.append(urljoin(site.url, site.sections[1]))
        if site.id == "dongcheng":
            sources.append(SEARCH_ENDPOINT)
        through = args.until_date or now.date()
        for source in sources:
            watermark = state.watermark(source) if args.mode != "backfill" and not args.from_date else None
            cutoff = args.from_date or ((watermark - timedelta(days=args.overlap_days)) if watermark
                                       else through - timedelta(days=args.lookback_days))
            try:
                if source == SEARCH_ENDPOINT:
                    result = scan_search(client, logger, site, cutoff, through, args.max_pages, output, state, now.isoformat())
                else:
                    result = scan(client, logger, site, source, cutoff, through, args.max_pages, output, state, now.isoformat())
                scans.append(result)
                if result["coverage_complete"] and through == now.date() and args.mode != "backfill" and not args.from_date:
                    state.advance(source, through)
                if not result["coverage_complete"]:
                    errors.append({"kind": "coverage", "url": source, "message": result["stop_reason"]})
            except Exception as error:
                scans.append({"url": source, "coverage_complete": False, "stop_reason": "failed"})
                errors.append({"kind": "list", "url": source, "message": str(error)})
                logger.exception("list.fetch.failed", "栏目采集失败，将保留水位并处理已发现公告。", url=source)
        rows = state.rows()
        prior_catalog = build_catalog([json.loads(r["record"]) for r in rows if r["record"]], now)
        refresh_urls = set()
        for plan in prior_catalog["campaigns"]:
            recent_end = any(w["end"] and w["end"]["value"][:10] >= (now.date() - timedelta(days=30)).isoformat()
                             for w in plan["application_windows"])
            if plan["application_status"] in ("open", "upcoming", "unknown") or recent_end:
                refresh_urls.update(plan["sources"])
                refresh_urls.update(e["url"] for e in plan["events"])
        for row in rows:
            previous = json.loads(row["record"]) if row["record"] else None
            if not row["pending"] and row["url"] not in refresh_urls:
                continue
            entry = Entry(**json.loads(row["entry"]))
            try:
                logger.info("article.fetch.start", "读取社区招聘候选公告。", title=entry.title, url=entry.url)
                response = client.get(entry.url, referer=entry.list_page_url)
                if not site.allowed(response.final_url, "article"):
                    raise ValueError("公告跳转到非目标栏目。")
                html = common.decode_html(response)
                article, links = parse_article(html, site, entry)
                if classify(article.title, article.text) is None:
                    state.save(entry.url, None, digest(article.text), False)
                    continue
                raw = output / "articles" / f"{digest(entry.url)[:20]}.html"
                raw.parent.mkdir(parents=True, exist_ok=True)
                raw.write_text(html, encoding="utf-8")
                if args.skip_attachments:
                    attachments = common.describe_skipped_attachments(article.attachments)
                else:
                    attachments = common.download_attachments(client, logger, article.attachments, output, digest(entry.url)[:20],
                                                               entry.url, args.max_attachment_mb * 1024 * 1024, errors)
                record = build_record(site, entry, article, links, attachments, args, now, output)
                old_hashes = {a["url"]: a.get("sha256") or a.get("previous_sha256")
                              for a in previous.get("attachments", [])} if previous else {}
                for attachment in attachments:
                    if not attachment.get("sha256") and old_hashes.get(attachment["url"]):
                        attachment["previous_sha256"] = old_hashes[attachment["url"]]
                fingerprint = digest(json.dumps([article.title, article.text,
                    [(a["url"], a.get("sha256") or old_hashes.get(a["url"])) for a in attachments]], ensure_ascii=False))
                change = "new" if not previous else "updated" if fingerprint != row["fingerprint"] else "unchanged"
                record["change_type"] = change
                new_count += change == "new"
                updated_count += change == "updated"
                retry = any(a.get("status") == "failed" for a in attachments)
                state.save(entry.url, record, fingerprint, retry)
                records.append(record)
            except Exception as error:
                state.save(entry.url, previous, row["fingerprint"], True, str(error))
                errors.append({"kind": "article", "url": entry.url, "message": str(error)})
                logger.exception("article.fetch.failed", "公告读取失败，已加入下次重试。", url=entry.url)
        stored = []
        for row in state.rows():
            if row["record"]:
                saved = json.loads(row["record"])
                if row["last_error"]:
                    saved["quality"]["warnings"].append("latest_fetch_failed")
                stored.append(saved)
        catalog = build_catalog(stored, now)
        common.write_jsonl(output / "records.jsonl", records)
        common.write_jsonl(output / "errors.jsonl", errors)
        common.write_json(output / "catalog.json", catalog)
        complete = all(s["coverage_complete"] for s in scans) and not errors
        attachments_complete = not any(a.get("status") != "downloaded" for r in stored for a in r["attachments"])
        summary = {"run_id": args.run_id, "district": site.district, "as_of": now.isoformat(),
                   "outcome": "completed" if complete else "completed_with_errors", "record_count": len(records),
                   "new_announcements": new_count, "updated_announcements": updated_count,
                   "coverage_complete": all(s["coverage_complete"] for s in scans),
                   "coverage_scope": "available_official_columns_and_search_index",
                   "data_complete": complete and attachments_complete, "sources": scans,
                   "campaign_counts": catalog["counts"], "errors": len(errors),
                   "position_count": None, "vacancy_count": None}
        common.write_json(output / "summary.json", summary)
        logger.info("crawler.completed", "社区招聘采集完成。", **summary)
        return 0 if complete else 1
    finally:
        state.close()


def main(site: Site) -> int:
    common.configure_console_encoding()
    logger = None
    try:
        args = build_argument_parser(site).parse_args()
        validate_arguments(site, args)
        logger = common.CrawlerLogger(args.log_level)
        return run(site, args, logger)
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        if logger:
            logger.exception("crawler.failed", "社区招聘爬虫运行失败。", error=str(error))
        else:
            print(f"Crawler failed: {error}")
        return 1
    finally:
        if logger:
            logger.close()

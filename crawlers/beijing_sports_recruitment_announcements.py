"""Crawl recent recruitment announcements from the Beijing Sports Bureau.

The crawler selects list entries from the recent date range only when their
titles contain both ``招聘`` and ``公告``. It stores raw pages, structured
records, detailed event logs, and attachments below the output directory
allocated by the PA console.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

if __package__:
    from . import beijing_hr_recruitment as common
else:
    import beijing_hr_recruitment as common


DEFAULT_LIST_URL = "https://tyj.beijing.gov.cn/bjsports/zfxxgk_/gkzp/"
SECTION_PATH = "/bjsports/zfxxgk_/gkzp/"
PAGE_MODULE_ID = "711e5c8c"
ALLOWED_HOSTS = {"tyj.beijing.gov.cn"}
REQUIRED_TITLE_KEYWORDS = ("招聘", "公告")
DEFAULT_REQUEST_DELAY_SECONDS = 0.35
DEFAULT_TIMEOUT_SECONDS = 45
DEFAULT_MAX_ATTACHMENT_MB = 5
DATE_PATTERN = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")
PAGE_COUNT_PATTERN = re.compile(
    r"\btotalpage\s*=\s*[\"'](\d+)[\"']",
    re.IGNORECASE | re.DOTALL,
)
LIST_PATH_PATTERN = re.compile(
    r"^/bjsports/zfxxgk_/gkzp/(?:index\.html|[a-f0-9]+-\d+\.html)?$",
    re.IGNORECASE,
)
ARTICLE_PATH_PATTERN = re.compile(
    r"^/bjsports/zfxxgk_/gkzp/\d+/index\.html$",
    re.IGNORECASE,
)

BEIJING_TIMEZONE = common.BEIJING_TIMEZONE
BLOCK_TAGS = common.BLOCK_TAGS
DOWNLOAD_EXTENSIONS = common.DOWNLOAD_EXTENSIONS
IGNORED_BODY_TAGS = common.IGNORED_BODY_TAGS
LOG_LEVELS = common.LOG_LEVELS
VOID_TAGS = common.VOID_TAGS
AttachmentLink = common.AttachmentLink
CrawlerLogger = common.CrawlerLogger
HttpClient = common.HttpClient
HttpResponse = common.HttpResponse
configure_console_encoding = common.configure_console_encoding
decode_html = common.decode_html
describe_skipped_attachments = common.describe_skipped_attachments
download_attachments = common.download_attachments
normalize_inline_text = common.normalize_inline_text
normalize_multiline_text = common.normalize_multiline_text
previous_calendar_month = common.previous_calendar_month
without_fragment = common.without_fragment
write_json = common.write_json
write_jsonl = common.write_jsonl


def article_identifier(url: str) -> str:
    path = Path(urlsplit(url).path)
    if path.name.lower() == "index.html" and path.parent.name.isdigit():
        return path.parent.name
    return common.article_identifier(url)


@dataclass(frozen=True)
class ListEntry:
    title: str
    url: str
    published_date: date
    list_page_url: str


@dataclass(frozen=True)
class ArticleData:
    title: str
    published_date: date | None
    source: str
    text: str
    attachments: list[AttachmentLink]
    invalid_attachment_urls: list[str]


@dataclass
class ScanResult:
    entries: list[ListEntry]
    pages_fetched: int = 0
    total_pages_reported: int = 1
    entries_found: int = 0
    invalid_entries: int = 0
    duplicate_entries: int = 0
    skipped_before_cutoff: int = 0
    skipped_after_today: int = 0
    skipped_by_keywords: int = 0
    stop_reason: str = ""


class ListPageParser(HTMLParser):
    def __init__(self, page_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.page_url = page_url
        self.entries: list[ListEntry] = []
        self.found_list = False
        self.invalid_candidates = 0
        self._team_depth = 0
        self._list_depth = 0
        self._li_depth = 0
        self._anchor_depth = 0
        self._date_depth = 0
        self._current: dict[str, object] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attributes = {key.lower(): value or "" for key, value in attrs}
        classes = {item.lower() for item in attributes.get("class", "").split()}
        if tag == "div":
            if self._team_depth:
                self._team_depth += 1
            elif "gzdt" in classes:
                self._team_depth = 1
                self.found_list = True
            return
        if tag == "ul":
            if self._list_depth:
                self._list_depth += 1
            elif self._team_depth:
                self._list_depth = 1
            return
        if not self._list_depth:
            return
        if tag == "li":
            if self._li_depth:
                self._li_depth += 1
            else:
                self._li_depth = 1
                self._current = {
                    "url": "",
                    "title_attribute": "",
                    "title_parts": [],
                    "date_parts": [],
                }
            return
        if not self._li_depth or self._current is None:
            return

        is_void = tag in VOID_TAGS
        if self._anchor_depth and not is_void:
            self._anchor_depth += 1
        elif (
            tag == "a"
            and not self._current["url"]
            and attributes.get("title", "").strip()
        ):
            candidate = without_fragment(urljoin(self.page_url, attributes.get("href", "")))
            if is_article_url(candidate):
                self._current["url"] = candidate
                self._current["title_attribute"] = attributes.get("title", "")
                self._anchor_depth = 1
        if self._date_depth and not is_void:
            self._date_depth += 1
        elif tag == "span" and "article-time" in classes:
            self._date_depth = 1

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._anchor_depth:
            self._anchor_depth -= 1
        if self._date_depth:
            self._date_depth -= 1
        if tag == "li" and self._li_depth:
            self._li_depth -= 1
            if self._li_depth == 0:
                self._finish_entry()
        if tag == "ul" and self._list_depth:
            self._list_depth -= 1
        if tag == "div" and self._team_depth:
            self._team_depth -= 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self._current is None:
            return
        if self._anchor_depth:
            title_parts = self._current["title_parts"]
            assert isinstance(title_parts, list)
            title_parts.append(data)
        if self._date_depth:
            date_parts = self._current["date_parts"]
            assert isinstance(date_parts, list)
            date_parts.append(data)

    def _finish_entry(self) -> None:
        assert self._current is not None
        title_parts = self._current["title_parts"]
        date_parts = self._current["date_parts"]
        assert isinstance(title_parts, list)
        assert isinstance(date_parts, list)
        title = normalize_inline_text(str(self._current["title_attribute"]))
        if not title:
            title = normalize_inline_text("".join(str(item) for item in title_parts))
        url = str(self._current["url"])
        published_date = parse_date("".join(str(item) for item in date_parts))
        if title and url and published_date:
            self.entries.append(ListEntry(title, url, published_date, self.page_url))
        else:
            self.invalid_candidates += 1
        self._current = None
        self._anchor_depth = 0
        self._date_depth = 0


class ArticlePageParser(HTMLParser):
    def __init__(self, article_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.article_url = article_url
        self.meta: dict[str, str] = {}
        self.title_parts: list[str] = []
        self.body_parts: list[str] = []
        self.attachments: list[AttachmentLink] = []
        self.invalid_attachment_urls: list[str] = []
        self.found_article_box = False
        self.found_body = False
        self._box_depth = 0
        self._body_depth = 0
        self._attachment_list_depth = 0
        self._h1_depth = 0
        self._ignored_depth = 0
        self._attachment_depth = 0
        self._attachment_url = ""
        self._attachment_parts: list[str] = []
        self._seen_attachment_urls: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attributes = {key.lower(): value or "" for key, value in attrs}
        classes = {item.lower() for item in attributes.get("class", "").split()}
        is_void = tag in VOID_TAGS
        if tag == "meta":
            name = attributes.get("name", "").strip().lower()
            content = attributes.get("content", "").strip()
            if name and content:
                self.meta[name] = content
            return

        if tag == "div":
            if self._box_depth:
                self._box_depth += 1
            elif attributes.get("id", "").lower() == "zoom":
                self._box_depth = 1
                self.found_article_box = True
            if self._body_depth:
                self._body_depth += 1
            elif self._box_depth and attributes.get("id", "").lower() == "zoom":
                self._body_depth = 1
                self.found_body = True

        if self._h1_depth and not is_void:
            self._h1_depth += 1
        elif tag in {"h1", "h2", "h4"} and self._box_depth and (
            tag in {"h1", "h2"} or "title" in classes
        ):
            self._h1_depth = 1

        if tag == "ul":
            if self._attachment_list_depth:
                self._attachment_list_depth += 1
            elif self._box_depth and "fujian" in classes:
                self._attachment_list_depth = 1

        if self._attachment_depth and not is_void:
            self._attachment_depth += 1
        elif tag == "a" and (
            self._attachment_list_depth or self._body_depth or self._box_depth
        ):
            candidate = without_fragment(urljoin(self.article_url, attributes.get("href", "")))
            if is_probable_attachment(candidate):
                if is_attachment_resource_url(candidate):
                    self._attachment_url = candidate
                    self._attachment_parts = []
                    self._attachment_depth = 1
                elif candidate not in self.invalid_attachment_urls:
                    self.invalid_attachment_urls.append(candidate)

        if not self._body_depth:
            return
        if self._ignored_depth and not is_void:
            self._ignored_depth += 1
        elif tag in IGNORED_BODY_TAGS and not is_void:
            self._ignored_depth = 1
        elif not self._ignored_depth and (tag in BLOCK_TAGS or tag == "br"):
            self.body_parts.append("\n")
        elif not self._ignored_depth and tag in {"td", "th"}:
            self.body_parts.append("\t")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._attachment_depth:
            self._attachment_depth -= 1
            if self._attachment_depth == 0:
                self._finish_attachment()
        if self._h1_depth:
            self._h1_depth -= 1
        if self._body_depth and not self._ignored_depth and tag in BLOCK_TAGS:
            self.body_parts.append("\n")
        if self._ignored_depth:
            self._ignored_depth -= 1
        if tag == "ul" and self._attachment_list_depth:
            self._attachment_list_depth -= 1
        if tag == "div":
            if self._body_depth:
                self._body_depth -= 1
            if self._box_depth:
                self._box_depth -= 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self._h1_depth:
            self.title_parts.append(data)
        if self._body_depth and not self._ignored_depth:
            self.body_parts.append(data)
        if self._attachment_depth:
            self._attachment_parts.append(data)

    def result(self) -> ArticleData:
        return ArticleData(
            title=normalize_inline_text(
                self.meta.get("articletitle", "") or "".join(self.title_parts)
            ),
            published_date=parse_date(self.meta.get("pubdate", "")),
            source=normalize_source(self.meta.get("contentsource", "")),
            text=normalize_multiline_text("".join(self.body_parts)),
            attachments=self.attachments,
            invalid_attachment_urls=self.invalid_attachment_urls,
        )

    def _finish_attachment(self) -> None:
        url = self._attachment_url
        self._attachment_url = ""
        if not url or url in self._seen_attachment_urls:
            self._attachment_parts = []
            return
        self._seen_attachment_urls.add(url)
        remote_filename = unquote(Path(urlsplit(url).path).name)
        display_name = normalize_inline_text("".join(self._attachment_parts))
        self.attachments.append(
            AttachmentLink(
                display_name=display_name or Path(remote_filename).stem or "附件",
                url=url,
                remote_filename=remote_filename,
            )
        )
        self._attachment_parts = []


def normalize_source(value: str) -> str:
    normalized = normalize_inline_text(value)
    parts = normalized.split()
    if parts and all(part == parts[0] for part in parts):
        return parts[0]
    return normalized


def parse_date(value: str) -> date | None:
    match = DATE_PATTERN.search(value)
    if not match:
        return None
    try:
        return date.fromisoformat(match.group(1))
    except ValueError:
        return None


def title_matches(title: str) -> bool:
    normalized = normalize_inline_text(title)
    return all(keyword in normalized for keyword in REQUIRED_TITLE_KEYWORDS)


def is_list_url(url: str) -> bool:
    parsed = urlsplit(url)
    return (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() in ALLOWED_HOSTS
        and bool(LIST_PATH_PATTERN.match(parsed.path))
        and not parsed.query
        and not parsed.fragment
    )


def is_article_url(url: str) -> bool:
    parsed = urlsplit(url)
    return (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() in ALLOWED_HOSTS
        and bool(ARTICLE_PATH_PATTERN.match(parsed.path))
        and not parsed.query
        and not parsed.fragment
    )


def is_probable_attachment(url: str) -> bool:
    return Path(urlsplit(url).path).suffix.lower() in DOWNLOAD_EXTENSIONS


def is_attachment_resource_url(url: str) -> bool:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    return (
        parsed.scheme == "https"
        and (host == "gov.cn" or host.endswith(".gov.cn"))
        and is_probable_attachment(url)
        and not parsed.fragment
    )


def list_page_url(base_url: str, page_number: int) -> str:
    if page_number < 1:
        raise ValueError("page_number must be at least 1")
    if page_number == 1:
        return base_url
    parsed = urlsplit(base_url)
    return (
        f"{parsed.scheme}://{parsed.netloc}{SECTION_PATH}"
        f"{PAGE_MODULE_ID}-{page_number}.html"
    )


def parse_list_page_diagnostics(
    html: str,
    page_url: str,
) -> tuple[ListPageParser, int]:
    parser = ListPageParser(page_url)
    parser.feed(html)
    parser.close()
    page_match = PAGE_COUNT_PATTERN.search(html)
    return parser, int(page_match.group(1)) if page_match else 1


def unique_entries(entries: list[ListEntry]) -> list[ListEntry]:
    result: list[ListEntry] = []
    seen_urls: set[str] = set()
    for entry in entries:
        if entry.url not in seen_urls:
            seen_urls.add(entry.url)
            result.append(entry)
    return result


def parse_list_page(html: str, page_url: str) -> tuple[list[ListEntry], int]:
    parser, page_count = parse_list_page_diagnostics(html, page_url)
    return unique_entries(parser.entries), page_count


def parse_article_page_diagnostics(
    html: str,
    article_url: str,
) -> tuple[ArticleData, ArticlePageParser]:
    parser = ArticlePageParser(article_url)
    parser.feed(html)
    parser.close()
    return parser.result(), parser


def parse_article_page(html: str, article_url: str) -> ArticleData:
    article, _parser = parse_article_page_diagnostics(html, article_url)
    return article


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def entry_filter_reason(
    entry: ListEntry,
    cutoff_date: date,
    through_date: date,
) -> str | None:
    if entry.published_date < cutoff_date:
        return "before_cutoff"
    if entry.published_date > through_date:
        return "after_today"
    if not title_matches(entry.title):
        return "keyword_mismatch"
    return None


def collect_entries(
    client: HttpClient,
    logger: CrawlerLogger,
    base_url: str,
    cutoff_date: date,
    through_date: date,
    output_dir: Path,
    max_pages: int = 0,
    max_articles: int = 0,
) -> ScanResult:
    result = ScanResult(entries=[])
    seen_urls: set[str] = set()
    page_number = 1
    while page_number <= result.total_pages_reported:
        if max_pages and result.pages_fetched >= max_pages:
            result.stop_reason = "max_pages"
            logger.warning(
                "list.scan.limit.max_pages",
                "已达到列表页上限，停止扫描。",
                max_pages=max_pages,
                pages_fetched=result.pages_fetched,
            )
            break
        current_url = list_page_url(base_url, page_number)
        logger.info(
            "list.page.fetch.start",
            "开始抓取北京市体育局招考招聘列表页。",
            page_number=page_number,
            known_page_count=result.total_pages_reported,
            url=current_url,
        )
        page_started_at = time.monotonic()
        response = client.get(current_url, referer=base_url)
        if not is_list_url(response.final_url):
            raise ValueError(f"List page redirected outside the configured section: {response.final_url}")
        html = decode_html(response)
        raw_path = output_dir / "list-pages" / f"page-{page_number:03d}.html"
        atomic_write_text(raw_path, html)
        parser, detected_page_count = parse_list_page_diagnostics(html, response.final_url)
        entries = unique_entries(parser.entries)
        result.pages_fetched += 1
        result.total_pages_reported = max(result.total_pages_reported, detected_page_count)
        result.entries_found += len(entries)
        result.invalid_entries += parser.invalid_candidates
        if not parser.found_list or not entries:
            raise ValueError(f"No valid personnel entries were found on list page {page_number}")

        page_selected = 0
        for entry in entries:
            if entry.url in seen_urls:
                result.duplicate_entries += 1
                continue
            seen_urls.add(entry.url)
            reason = entry_filter_reason(entry, cutoff_date, through_date)
            if reason:
                if reason == "before_cutoff":
                    result.skipped_before_cutoff += 1
                elif reason == "after_today":
                    result.skipped_after_today += 1
                else:
                    result.skipped_by_keywords += 1
                logger.debug(
                    "list.article.filtered",
                    "列表文章不符合本次抓取条件，已过滤。",
                    title=entry.title,
                    url=entry.url,
                    published_date=entry.published_date,
                    reason=reason,
                    cutoff_date=cutoff_date,
                    through_date=through_date,
                )
                continue
            result.entries.append(entry)
            page_selected += 1
            logger.info(
                "list.article.selected",
                "标题同时包含“招聘”和“公告”，已加入抓取队列。",
                selected_number=len(result.entries),
                title=entry.title,
                url=entry.url,
                published_date=entry.published_date,
                page_number=page_number,
            )
            if max_articles and len(result.entries) >= max_articles:
                result.stop_reason = "max_articles"
                logger.warning(
                    "list.scan.limit.max_articles",
                    "已达到文章数量上限，停止扫描。",
                    max_articles=max_articles,
                )
                return result

        newest_date = max(entry.published_date for entry in entries)
        oldest_date = min(entry.published_date for entry in entries)
        logger.info(
            "list.page.fetch.success",
            "北京市体育局招考招聘列表页抓取并筛选完成。",
            page_number=page_number,
            url=current_url,
            final_url=response.final_url,
            http_status=response.status,
            response_bytes=len(response.body),
            entries_found=len(entries),
            articles_selected=page_selected,
            newest_date=newest_date,
            oldest_date=oldest_date,
            total_pages=result.total_pages_reported,
            raw_html_path=raw_path.relative_to(output_dir).as_posix(),
            elapsed_ms=round((time.monotonic() - page_started_at) * 1000),
        )
        if oldest_date < cutoff_date:
            result.stop_reason = "cutoff_reached"
            logger.info(
                "list.scan.cutoff.reached",
                "当前列表页已出现早于本次日期边界的记录，停止翻页。",
                cutoff_date=cutoff_date,
                page_number=page_number,
                oldest_date=oldest_date,
            )
            break
        page_number += 1

    if not result.stop_reason:
        result.stop_reason = "all_pages_scanned"
    logger.info(
        "list.scan.completed",
        "近一个月招聘公告扫描完成。",
        pages_fetched=result.pages_fetched,
        total_pages=result.total_pages_reported,
        entries_found=result.entries_found,
        articles_selected=len(result.entries),
        skipped_before_cutoff=result.skipped_before_cutoff,
        skipped_after_today=result.skipped_after_today,
        skipped_by_keywords=result.skipped_by_keywords,
        duplicate_entries=result.duplicate_entries,
        invalid_entries=result.invalid_entries,
        stop_reason=result.stop_reason,
    )
    return result


def build_record(
    entry: ListEntry,
    article: ArticleData,
    attachments: list[dict[str, object]],
    raw_html_path: Path,
    output_dir: Path,
    run_id: str,
    fetched_at: str,
) -> dict[str, object]:
    warnings: list[str] = []
    published_date = article.published_date or entry.published_date
    publisher = article.source or "北京市体育局"
    if not article.text:
        warnings.append("article_body_empty")
    if article.published_date and article.published_date != entry.published_date:
        warnings.append("list_and_article_date_mismatch")
    if article.invalid_attachment_urls:
        warnings.append("unsafe_attachment_url_skipped")
    if any(item.get("status") == "failed" for item in attachments):
        warnings.append("attachment_download_failed")
    return {
        "schema_version": "1.0",
        "record_id": f"sha256:{hashlib.sha256(entry.url.encode('utf-8')).hexdigest()}",
        "run_id": run_id,
        "task_id": "beijing_sports_recruitment_announcements",
        "status": "ok" if not warnings else "completed_with_warnings",
        "source_url": entry.url,
        "published_date": published_date.isoformat(),
        "publisher": publisher,
        "content_text": article.text,
        "source": {
            "url": entry.url,
            "list_page_url": entry.list_page_url,
            "published_at": published_date.isoformat(),
            "fetched_at": fetched_at,
            "publisher": publisher,
        },
        "title": article.title or entry.title,
        "text": article.text,
        "attachments": attachments,
        "provenance": {
            "crawler": Path(__file__).name,
            "raw_html_path": raw_html_path.relative_to(output_dir).as_posix(),
            "content_hash": f"sha256:{hashlib.sha256(article.text.encode('utf-8')).hexdigest()}",
        },
        "quality": {"parse_ok": bool(article.text), "warnings": warnings},
    }


def determine_run_outcome(
    selected_count: int,
    succeeded_count: int,
    article_failure_count: int,
    error_count: int,
) -> tuple[str, int]:
    if selected_count and not succeeded_count and article_failure_count >= selected_count:
        return "failed", 1
    if error_count:
        return "completed_with_errors", 0
    return "completed", 0


def run(args: argparse.Namespace, logger: CrawlerLogger) -> int:
    started_at = time.monotonic()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    event_log_path = output_dir / "crawler-events.jsonl"
    logger.attach_event_log(event_log_path)
    through_date = datetime.now(BEIJING_TIMEZONE).date()
    cutoff_date = (
        through_date - timedelta(days=args.lookback_days)
        if args.lookback_days
        else previous_calendar_month(through_date)
    )
    client = HttpClient(
        timeout=args.timeout,
        retries=args.retries,
        request_delay=args.request_delay,
        logger=logger,
        attachment_url_validator=is_attachment_resource_url,
    )
    logger.info(
        "crawler.start",
        "北京市体育局招聘公告爬虫启动。",
        schema_version=1,
        run_id=args.run_id,
        source_url=args.url,
        output_dir=output_dir,
        cutoff_date=cutoff_date,
        through_date=through_date,
        required_title_keywords=list(REQUIRED_TITLE_KEYWORDS),
        max_pages=args.max_pages or None,
        max_articles=args.max_articles or None,
        max_attachment_mb=args.max_attachment_mb,
        skip_attachments=args.skip_attachments,
        request_delay_seconds=args.request_delay,
        timeout_seconds=args.timeout,
        retries=args.retries,
        python=sys.version.split()[0],
    )
    scan = collect_entries(
        client=client,
        logger=logger,
        base_url=args.url,
        cutoff_date=cutoff_date,
        through_date=through_date,
        output_dir=output_dir,
        max_pages=args.max_pages,
        max_articles=args.max_articles,
    )

    records: list[dict[str, object]] = []
    errors: list[dict[str, object]] = []
    attachments_found = 0
    attachments_downloaded = 0
    attachments_skipped = 0
    attachments_failed = 0
    article_failures = 0
    articles_skipped = 0
    logger.info(
        "article.batch.selected",
        "列表筛选完成，准备抓取符合条件的文章详情。",
        articles_selected=len(scan.entries),
        pages_fetched=scan.pages_fetched,
        cutoff_date=cutoff_date,
        through_date=through_date,
    )
    for index, entry in enumerate(scan.entries, start=1):
        article_started_at = time.monotonic()
        logger.info(
            "article.fetch.start",
            "开始抓取符合条件的文章详情。",
            article_number=index,
            article_count=len(scan.entries),
            title=entry.title,
            url=entry.url,
            list_page_url=entry.list_page_url,
            list_published_date=entry.published_date,
        )
        try:
            response = client.get(entry.url, referer=entry.list_page_url)
            if not is_article_url(response.final_url):
                raise ValueError(f"Article redirected outside the configured section: {response.final_url}")
            logger.info(
                "article.fetch.success",
                "文章详情页请求成功。",
                article_number=index,
                article_count=len(scan.entries),
                url=entry.url,
                final_url=response.final_url,
                http_status=response.status,
                response_bytes=len(response.body),
            )
            html = decode_html(response)
            article, parser = parse_article_page_diagnostics(html, response.final_url)
            effective_title = article.title or entry.title
            effective_date = article.published_date or entry.published_date
            logger.info(
                "article.parse.success",
                "文章详情解析完成。",
                article_number=index,
                article_count=len(scan.entries),
                title=effective_title,
                url=entry.url,
                published_date=effective_date,
                source=article.source or "北京市体育局",
                article_box_found=parser.found_article_box,
                article_body_found=parser.found_body,
                text_characters=len(article.text),
                attachments_found=len(article.attachments),
                invalid_attachment_urls=len(article.invalid_attachment_urls),
            )
            if not title_matches(effective_title):
                articles_skipped += 1
                errors.append(
                    {
                        "kind": "article_title",
                        "title": effective_title,
                        "url": entry.url,
                        "message": "Detail title no longer contains both required keywords",
                    }
                )
                logger.warning(
                    "article.skip.title_filter",
                    "详情页标题不再同时包含“招聘”和“公告”，已跳过。",
                    title=effective_title,
                    url=entry.url,
                )
                continue
            if effective_date < cutoff_date or effective_date > through_date:
                articles_skipped += 1
                message = (
                    f"Detail date {effective_date.isoformat()} is outside "
                    f"{cutoff_date.isoformat()} through {through_date.isoformat()}"
                )
                errors.append(
                    {
                        "kind": "article_date",
                        "title": effective_title,
                        "url": entry.url,
                        "published_date": effective_date.isoformat(),
                        "message": message,
                    }
                )
                logger.warning(
                    "article.skip.date_range",
                    "详情页日期不在本次抓取范围内，已跳过。",
                    title=effective_title,
                    url=entry.url,
                    published_date=effective_date,
                    cutoff_date=cutoff_date,
                    through_date=through_date,
                )
                continue
            if not parser.found_article_box or not parser.found_body or not article.text:
                raise ValueError("Article body container could not be parsed")

            article_id = article_identifier(entry.url)
            raw_html_path = output_dir / "articles" / f"{article_id}.html"
            atomic_write_text(raw_html_path, html)
            logger.info(
                "article.raw_html.saved",
                "文章原始 HTML 已保存。",
                article_url=entry.url,
                raw_html_path=raw_html_path.relative_to(output_dir).as_posix(),
                size_bytes=len(response.body),
            )
            for invalid_url in article.invalid_attachment_urls:
                errors.append(
                    {
                        "kind": "attachment_url",
                        "article_url": entry.url,
                        "url": invalid_url,
                        "message": "Attachment URL is outside HTTPS government hosts",
                    }
                )
                logger.warning(
                    "attachment.detected.invalid_url",
                    "检测到不符合安全规则的附件地址，已跳过。",
                    article_url=entry.url,
                    url=invalid_url,
                )
            for attachment_index, attachment in enumerate(article.attachments, start=1):
                logger.info(
                    "attachment.detected",
                    "检测到文章附件。",
                    article_url=entry.url,
                    attachment_number=attachment_index,
                    attachment_count=len(article.attachments),
                    display_name=attachment.display_name,
                    remote_filename=attachment.remote_filename,
                    url=attachment.url,
                )
            if args.skip_attachments:
                attachments = describe_skipped_attachments(article.attachments)
                for attachment in attachments:
                    attachment["skip_reason"] = "disabled"
                logger.warning(
                    "attachment.batch.skipped",
                    "已按运行参数跳过本文章的全部附件下载。",
                    article_url=entry.url,
                    attachments_skipped=len(attachments),
                )
            else:
                attachments = download_attachments(
                    client=client,
                    logger=logger,
                    links=article.attachments,
                    output_dir=output_dir,
                    article_id=article_id,
                    article_url=entry.url,
                    max_attachment_bytes=args.max_attachment_mb * 1024 * 1024,
                    errors=errors,
                )
            record = build_record(
                entry=entry,
                article=article,
                attachments=attachments,
                raw_html_path=raw_html_path,
                output_dir=output_dir,
                run_id=args.run_id,
                fetched_at=datetime.now(BEIJING_TIMEZONE).isoformat(timespec="seconds"),
            )
            records.append(record)
            attachments_found += len(attachments) + len(article.invalid_attachment_urls)
            article_downloaded = sum(item.get("status") == "downloaded" for item in attachments)
            article_skipped = sum(item.get("status") == "skipped" for item in attachments)
            article_failed = sum(item.get("status") == "failed" for item in attachments)
            attachments_downloaded += article_downloaded
            attachments_skipped += article_skipped + len(article.invalid_attachment_urls)
            attachments_failed += article_failed
            logger.info(
                "article.completed",
                "文章抓取与数据整理完成。",
                article_number=index,
                article_count=len(scan.entries),
                title=record["title"],
                url=entry.url,
                status=record["status"],
                text_characters=len(article.text),
                attachments_found=len(article.attachments),
                attachments_downloaded=article_downloaded,
                attachments_skipped=article_skipped,
                attachments_failed=article_failed,
                warnings=record["quality"]["warnings"],
                elapsed_ms=round((time.monotonic() - article_started_at) * 1000),
            )
        except Exception as error:
            article_failures += 1
            errors.append(
                {
                    "kind": "article",
                    "title": entry.title,
                    "url": entry.url,
                    "published_date": entry.published_date.isoformat(),
                    "error_type": type(error).__name__,
                    "message": str(error),
                }
            )
            logger.exception(
                "article.failed",
                "文章抓取失败，继续处理下一篇。",
                article_number=index,
                article_count=len(scan.entries),
                title=entry.title,
                url=entry.url,
                error_type=type(error).__name__,
                error=str(error),
                elapsed_ms=round((time.monotonic() - article_started_at) * 1000),
            )

    records_path = output_dir / "records.jsonl"
    errors_path = output_dir / "errors.jsonl"
    summary_path = output_dir / "summary.json"
    write_jsonl(records_path, records)
    if errors:
        write_jsonl(errors_path, errors)
    else:
        errors_path.unlink(missing_ok=True)
    status, exit_code = determine_run_outcome(
        selected_count=len(scan.entries),
        succeeded_count=len(records),
        article_failure_count=article_failures,
        error_count=len(errors),
    )
    summary = {
        "schema_version": 1,
        "run_id": args.run_id,
        "status": status,
        "source_url": args.url,
        "cutoff_date": cutoff_date.isoformat(),
        "through_date": through_date.isoformat(),
        "required_title_keywords": list(REQUIRED_TITLE_KEYWORDS),
        "pages_fetched": scan.pages_fetched,
        "total_pages_reported": scan.total_pages_reported,
        "entries_found": scan.entries_found,
        "articles_selected": len(scan.entries),
        "articles_succeeded": len(records),
        "articles_failed": article_failures,
        "articles_skipped": articles_skipped,
        "attachments_found": attachments_found,
        "attachments_downloaded": attachments_downloaded,
        "attachments_skipped": attachments_skipped,
        "attachments_failed": attachments_failed,
        "invalid_entries": scan.invalid_entries,
        "duplicate_entries": scan.duplicate_entries,
        "skipped_before_cutoff": scan.skipped_before_cutoff,
        "skipped_after_today": scan.skipped_after_today,
        "skipped_by_keywords": scan.skipped_by_keywords,
        "stop_reason": scan.stop_reason,
        "max_attachment_mb": args.max_attachment_mb,
        "error_count": len(errors),
        "event_log_path": event_log_path.relative_to(output_dir).as_posix(),
        "finished_at": datetime.now(BEIJING_TIMEZONE).isoformat(timespec="seconds"),
    }
    write_json(summary_path, summary)
    logger.info(
        "output.completed",
        "抓取结果文件已全部写入。",
        records_path=records_path,
        record_count=len(records),
        errors_path=errors_path if errors else None,
        error_count=len(errors),
        summary_path=summary_path,
        summary_status=status,
    )
    finish_fields = {
        **summary,
        "exit_code": exit_code,
        "elapsed_ms": round((time.monotonic() - started_at) * 1000),
    }
    if status == "failed":
        logger.error("crawler.finish", "爬虫运行失败，符合条件的文章均未成功保存。", **finish_fields)
    elif errors:
        logger.warning("crawler.finish", "爬虫运行完成，但存在已记录的局部错误。", **finish_fields)
    else:
        logger.info("crawler.finish", "北京市体育局招聘公告爬虫运行成功。", **finish_fields)
    return exit_code


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Crawl recent Beijing Sports Bureau notices whose titles contain 招聘 and 公告."
    )
    parser.add_argument("--url", default=DEFAULT_LIST_URL)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=0,
        help="Override the default same-day previous-month cutoff; 0 keeps the default.",
    )
    parser.add_argument("--max-pages", type=int, default=0, help="Smoke-test page limit; 0 is unlimited.")
    parser.add_argument("--max-articles", type=int, default=0, help="Smoke-test article limit; 0 is unlimited.")
    parser.add_argument("--request-delay", type=float, default=DEFAULT_REQUEST_DELAY_SECONDS)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument(
        "--max-attachment-mb",
        type=int,
        default=DEFAULT_MAX_ATTACHMENT_MB,
        help="Skip attachments larger than this many megabytes; default: 5.",
    )
    parser.add_argument("--skip-attachments", action="store_true")
    parser.add_argument("--log-level", choices=tuple(LOG_LEVELS), default="INFO")
    return parser


def validate_arguments(args: argparse.Namespace) -> None:
    if not is_list_url(args.url) or urlsplit(args.url).path.rstrip("/") != SECTION_PATH.rstrip("/"):
        raise ValueError(f"The list URL must be {DEFAULT_LIST_URL}")
    if args.lookback_days < 0 or args.lookback_days > 3660:
        raise ValueError("--lookback-days must be between 0 and 3660")
    if args.max_pages < 0 or args.max_articles < 0:
        raise ValueError("--max-pages and --max-articles cannot be negative")
    if args.request_delay < 0 or args.request_delay > 60:
        raise ValueError("--request-delay must be between 0 and 60 seconds")
    if args.timeout < 1 or args.retries < 0 or args.max_attachment_mb < 1:
        raise ValueError("Timeout, retries, and attachment size settings are invalid")


def main() -> int:
    configure_console_encoding()
    logger: CrawlerLogger | None = None
    try:
        args = build_argument_parser().parse_args()
        validate_arguments(args)
        logger = CrawlerLogger(args.log_level)
        return run(args, logger)
    except KeyboardInterrupt:
        if logger:
            logger.warning("crawler.interrupted", "爬虫已被用户停止。", exit_code=130)
        else:
            print("Crawler stopped by user.", file=sys.stderr)
        return 130
    except Exception as error:
        if logger:
            logger.exception(
                "crawler.failed",
                "爬虫运行失败。",
                error_type=type(error).__name__,
                error=str(error),
                exit_code=1,
            )
        else:
            print(f"Crawler failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    finally:
        if logger:
            logger.close()


if __name__ == "__main__":
    raise SystemExit(main())

"""Crawl recent Tongzhou recruitment announcements and attachments.

Only articles published within the configured recent range whose titles contain
both ``招聘`` and ``公告`` are fetched. The script follows the PA console
protocol and writes every artifact below the runner-provided output directory.
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
parse_iso_date = common.parse_iso_date
previous_calendar_month = common.previous_calendar_month
write_json = common.write_json
write_jsonl = common.write_jsonl
without_fragment = common.without_fragment


DEFAULT_LIST_URL = "https://www.bjtzh.gov.cn/bjtz/zwgk/tzgg/index.shtml"
DEFAULT_REQUEST_DELAY_SECONDS = 0.5
DEFAULT_TIMEOUT_SECONDS = 45
DEFAULT_MAX_ATTACHMENT_MB = 5
REQUIRED_TITLE_KEYWORDS = ("招聘", "公告")
SECTION_PATH = "/bjtz/zwgk/tzgg/"
PAGINATION_STEM = "2088c985"
ALLOWED_HOSTS = {"www.bjtzh.gov.cn", "bjtzh.gov.cn"}
LIST_PAGE_PATH_PATTERN = re.compile(
    rf"^/bjtz/zwgk/tzgg/(?:index|{PAGINATION_STEM}-\d+)\.shtml$",
    re.IGNORECASE,
)
ARTICLE_PATH_PATTERN = re.compile(
    r"^/bjtz/zwgk/tzgg/(\d{14,20})/index\.shtml$",
    re.IGNORECASE,
)
TOTAL_PAGE_PATTERN = re.compile(r"\btotalpage\s*=\s*['\"](\d+)['\"]", re.IGNORECASE)


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


@dataclass
class ScanResult:
    entries: list[ListEntry]
    pages_fetched: int = 0
    total_pages_reported: int = 0
    list_entries_found: int = 0
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
        self.found_container = False
        self.invalid_candidate_count = 0
        self._channel_depth = 0
        self._li_depth = 0
        self._anchor_depth = 0
        self._date_depth = 0
        self._current: dict[str, object] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attributes = {key.lower(): value or "" for key, value in attrs}
        if self._channel_depth == 0:
            if tag == "ul" and attributes.get("id", "").lower() == "channelnames":
                self._channel_depth = 1
                self.found_container = True
            return

        if tag not in VOID_TAGS:
            self._channel_depth += 1

        if self._current is None:
            if tag != "li":
                return
            self._current = {
                "url": "",
                "title_attribute": "",
                "title_parts": [],
                "date_parts": [],
            }
            self._li_depth = 1
            return

        if tag not in VOID_TAGS:
            self._li_depth += 1

        if self._anchor_depth > 0 and tag not in VOID_TAGS:
            self._anchor_depth += 1
        elif tag == "a" and not self._current["url"]:
            candidate = without_fragment(urljoin(self.page_url, attributes.get("href", "").strip()))
            if is_article_url(candidate):
                self._current["url"] = candidate
                self._current["title_attribute"] = attributes.get("title", "")
                self._anchor_depth = 1

        classes = {item.lower() for item in attributes.get("class", "").split()}
        if self._date_depth > 0 and tag not in VOID_TAGS:
            self._date_depth += 1
        elif tag == "span" and "m-time" in classes:
            self._date_depth = 1

    def handle_endtag(self, tag: str) -> None:
        if self._channel_depth == 0:
            return
        if self._anchor_depth > 0:
            self._anchor_depth -= 1
        if self._date_depth > 0:
            self._date_depth -= 1
        if self._current is not None and self._li_depth > 0:
            self._li_depth -= 1
            if self._li_depth == 0:
                self._finish_current()
                self._current = None
        self._channel_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._current is None:
            return
        if self._anchor_depth > 0:
            title_parts = self._current["title_parts"]
            assert isinstance(title_parts, list)
            title_parts.append(data)
        if self._date_depth > 0:
            date_parts = self._current["date_parts"]
            assert isinstance(date_parts, list)
            date_parts.append(data)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in VOID_TAGS:
            self.handle_endtag(tag)

    def _finish_current(self) -> None:
        if self._current is None:
            return
        url = str(self._current["url"])
        title_parts = self._current["title_parts"]
        date_parts = self._current["date_parts"]
        assert isinstance(title_parts, list)
        assert isinstance(date_parts, list)
        title = normalize_inline_text(
            str(self._current["title_attribute"]) or "".join(str(item) for item in title_parts)
        )
        published_date = parse_iso_date("".join(str(item) for item in date_parts))
        if not url or not title or not published_date:
            self.invalid_candidate_count += 1
            return
        self.entries.append(
            ListEntry(
                title=title,
                url=url,
                published_date=published_date,
                list_page_url=self.page_url,
            )
        )


class ArticlePageParser(HTMLParser):
    def __init__(self, article_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.article_url = article_url
        self.meta: dict[str, str] = {}
        self.attachments: list[AttachmentLink] = []
        self.found_main_text = False
        self._main_depth = 0
        self._ignored_depth = 0
        self._stop_body = False
        self._h1_depth = 0
        self._h1_text: list[str] = []
        self._body_text: list[str] = []
        self._attachment_depth = 0
        self._attachment_url = ""
        self._attachment_text: list[str] = []
        self._seen_attachment_urls: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attributes = {key.lower(): value or "" for key, value in attrs}
        if tag == "meta":
            name = attributes.get("name", "").strip().lower()
            if name:
                self.meta[name] = attributes.get("content", "").strip()
            return

        if self._main_depth == 0 and tag == "h1":
            self._h1_depth = 1
        elif self._h1_depth > 0 and tag not in VOID_TAGS:
            self._h1_depth += 1

        if self._main_depth == 0:
            if tag == "div" and attributes.get("id", "").lower() == "maintext":
                self._main_depth = 1
                self.found_main_text = True
            return

        if tag not in VOID_TAGS:
            self._main_depth += 1

        if self._stop_body:
            return
        element_id = attributes.get("id", "").lower()
        if tag == "div" and element_id == "qrbox":
            self._stop_body = True
            return

        if self._ignored_depth > 0:
            if tag not in VOID_TAGS:
                self._ignored_depth += 1
            return
        if tag in IGNORED_BODY_TAGS or (tag == "ul" and element_id == "zc"):
            self._ignored_depth = 1
            return

        if self._attachment_depth > 0 and tag not in VOID_TAGS:
            self._attachment_depth += 1
        elif tag == "a":
            candidate = without_fragment(
                urljoin(self.article_url, attributes.get("href", "").strip())
            )
            if is_attachment_resource_url(candidate):
                self._attachment_depth = 1
                self._attachment_url = candidate
                self._attachment_text = []

        if tag in BLOCK_TAGS or tag == "br":
            self._body_text.append("\n")
        elif tag in {"td", "th"}:
            self._body_text.append("\t")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._h1_depth > 0:
            self._h1_depth -= 1
        if self._main_depth == 0:
            return
        if not self._stop_body and self._ignored_depth == 0 and tag in BLOCK_TAGS:
            self._body_text.append("\n")
        if self._attachment_depth > 0:
            self._attachment_depth -= 1
            if self._attachment_depth == 0:
                self._finish_attachment()
        if self._ignored_depth > 0:
            self._ignored_depth -= 1
        self._main_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._h1_depth > 0:
            self._h1_text.append(data)
        if self._main_depth > 0 and not self._stop_body and self._ignored_depth == 0:
            self._body_text.append(data)
            if self._attachment_depth > 0:
                self._attachment_text.append(data)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in VOID_TAGS:
            self.handle_endtag(tag)

    def build(self) -> ArticleData:
        meta_title = clean_metadata_text(self.meta.get("articletitle", ""))
        return ArticleData(
            title=meta_title or normalize_inline_text("".join(self._h1_text)),
            published_date=parse_iso_date(self.meta.get("pubdate", "")),
            source=clean_metadata_text(self.meta.get("contentsource", "")),
            text=normalize_multiline_text("".join(self._body_text)),
            attachments=self.attachments,
        )

    def _finish_attachment(self) -> None:
        url = self._attachment_url
        self._attachment_url = ""
        if not url or url in self._seen_attachment_urls:
            return
        self._seen_attachment_urls.add(url)
        remote_filename = unquote(Path(urlsplit(url).path).name)
        display_name = normalize_inline_text("".join(self._attachment_text)) or remote_filename
        display_name = re.sub(r"^附件\s*[:：]\s*", "", display_name) or remote_filename
        self.attachments.append(
            AttachmentLink(
                display_name=display_name,
                url=url,
                remote_filename=remote_filename,
            )
        )


def is_configured_list_url(url: str) -> bool:
    parsed = urlsplit(url)
    return (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() in ALLOWED_HOSTS
        and parsed.path == urlsplit(DEFAULT_LIST_URL).path
        and not parsed.query
        and not parsed.fragment
    )


def is_list_page_url(url: str) -> bool:
    parsed = urlsplit(url)
    return (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() in ALLOWED_HOSTS
        and bool(LIST_PAGE_PATH_PATTERN.match(parsed.path))
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


def is_attachment_resource_url(url: str) -> bool:
    parsed = urlsplit(url)
    return (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() in ALLOWED_HOSTS
        and parsed.path.lower().startswith(SECTION_PATH)
        and Path(parsed.path).suffix.lower() in DOWNLOAD_EXTENSIONS
        and not parsed.fragment
    )


def list_page_url(base_url: str, page_number: int) -> str:
    if page_number < 1:
        raise ValueError("page_number must be at least 1")
    if page_number == 1:
        return base_url
    directory_url = base_url.rsplit("/", 1)[0] + "/"
    return urljoin(directory_url, f"{PAGINATION_STEM}-{page_number}.shtml")


def article_identifier(url: str) -> str:
    match = ARTICLE_PATH_PATTERN.match(urlsplit(url).path)
    if match:
        return match.group(1)
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def title_matches(title: str) -> bool:
    normalized = normalize_inline_text(title)
    return all(keyword in normalized for keyword in REQUIRED_TITLE_KEYWORDS)


def clean_metadata_text(value: str) -> str:
    return normalize_inline_text(re.sub(r"<[^>]+>", "", value))


def entry_filter_reason(entry: ListEntry, cutoff: date, through_date: date) -> str | None:
    if entry.published_date < cutoff:
        return "before_cutoff"
    if entry.published_date > through_date:
        return "after_today"
    if not title_matches(entry.title):
        return "missing_required_keywords"
    return None


def parse_list_page(html: str, page_url: str) -> tuple[ListPageParser, int | None]:
    parser = ListPageParser(page_url)
    parser.feed(html)
    parser.close()
    match = TOTAL_PAGE_PATTERN.search(html)
    total_pages = int(match.group(1)) if match else None
    return parser, total_pages


def parse_article_page(html: str, article_url: str) -> tuple[ArticleData, bool]:
    parser = ArticlePageParser(article_url)
    parser.feed(html)
    parser.close()
    return parser.build(), parser.found_main_text


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f"{path.name}.tmp")
    temporary_path.write_text(content, encoding="utf-8")
    os.replace(temporary_path, path)


def collect_entries(
    client: HttpClient,
    logger: CrawlerLogger,
    base_url: str,
    cutoff: date,
    through_date: date,
    output_dir: Path,
    max_pages: int = 0,
    max_articles: int = 0,
) -> ScanResult:
    result = ScanResult(entries=[])
    seen_urls: set[str] = set()
    page_number = 1
    total_pages: int | None = None

    while True:
        if max_pages and result.pages_fetched >= max_pages:
            result.stop_reason = "max_pages"
            logger.warning(
                "list.scan.limit.max_pages",
                "已达到列表页抓取上限，停止扫描。",
                max_pages=max_pages,
                pages_fetched=result.pages_fetched,
            )
            break

        page_url = list_page_url(base_url, page_number)
        page_started_at = time.monotonic()
        logger.info(
            "list.page.fetch.start",
            "开始抓取通州区通知公告列表页。",
            page_number=page_number,
            url=page_url,
        )
        response = client.get(page_url, referer=base_url)
        if not is_list_page_url(response.final_url):
            raise ValueError(f"List page redirected outside the configured section: {response.final_url}")
        html = decode_html(response)
        raw_path = output_dir / "list-pages" / f"page-{page_number:03d}.html"
        atomic_write_text(raw_path, html)
        parser, reported_pages = parse_list_page(html, response.final_url)
        if not parser.found_container:
            raise ValueError("List container ul#channelNames was not found")
        if page_number == 1:
            if not reported_pages:
                raise ValueError("Pagination totalpage attribute was not found")
            total_pages = reported_pages
            result.total_pages_reported = reported_pages

        result.pages_fetched += 1
        result.list_entries_found += len(parser.entries)
        result.invalid_entries += parser.invalid_candidate_count
        logger.info(
            "list.page.fetch.success",
            "列表页抓取并解析完成。",
            page_number=page_number,
            total_pages=total_pages,
            url=page_url,
            final_url=response.final_url,
            http_status=response.status,
            response_bytes=len(response.body),
            entries_found=len(parser.entries),
            invalid_entries=parser.invalid_candidate_count,
            raw_html_path=raw_path.relative_to(output_dir).as_posix(),
            elapsed_ms=round((time.monotonic() - page_started_at) * 1000),
        )

        if not parser.entries:
            result.stop_reason = "empty_page"
            logger.warning(
                "list.page.empty",
                "列表页没有解析到文章条目，停止扫描。",
                page_number=page_number,
                url=page_url,
            )
            break

        all_before_cutoff = True
        for entry in parser.entries:
            if entry.published_date >= cutoff:
                all_before_cutoff = False
            if entry.url in seen_urls:
                result.duplicate_entries += 1
                logger.debug(
                    "list.article.duplicate",
                    "检测到重复文章链接，已忽略。",
                    title=entry.title,
                    url=entry.url,
                    published_date=entry.published_date,
                )
                continue
            seen_urls.add(entry.url)
            reason = entry_filter_reason(entry, cutoff, through_date)
            if reason == "before_cutoff":
                result.skipped_before_cutoff += 1
                continue
            if reason == "after_today":
                result.skipped_after_today += 1
                logger.warning(
                    "list.article.filtered.future_date",
                    "文章日期晚于当前北京时间，已忽略。",
                    title=entry.title,
                    url=entry.url,
                    published_date=entry.published_date,
                    through_date=through_date,
                )
                continue
            if reason == "missing_required_keywords":
                result.skipped_by_keywords += 1
                logger.info(
                    "list.article.filtered.keywords",
                    "标题未同时包含“招聘”和“公告”，已忽略。",
                    title=entry.title,
                    url=entry.url,
                    published_date=entry.published_date,
                    required_keywords=list(REQUIRED_TITLE_KEYWORDS),
                )
                continue

            result.entries.append(entry)
            logger.info(
                "list.article.selected",
                "文章满足日期和标题条件，已加入抓取队列。",
                article_number=len(result.entries),
                title=entry.title,
                url=entry.url,
                published_date=entry.published_date,
            )
            if max_articles and len(result.entries) >= max_articles:
                result.stop_reason = "max_articles"
                break

        if result.stop_reason == "max_articles":
            break
        if all_before_cutoff:
            result.stop_reason = "cutoff_reached"
            logger.info(
                "list.scan.cutoff.reached",
                "当前列表页全部早于截止日期，停止扫描。",
                page_number=page_number,
                cutoff_date=cutoff,
            )
            break
        if total_pages is not None and page_number >= total_pages:
            result.stop_reason = "last_page"
            break
        page_number += 1

    logger.info(
        "list.scan.completed",
        "通知公告列表扫描完成。",
        pages_fetched=result.pages_fetched,
        total_pages_reported=result.total_pages_reported,
        list_entries_found=result.list_entries_found,
        articles_selected=len(result.entries),
        skipped_before_cutoff=result.skipped_before_cutoff,
        skipped_after_today=result.skipped_after_today,
        skipped_by_keywords=result.skipped_by_keywords,
        duplicate_entries=result.duplicate_entries,
        invalid_entries=result.invalid_entries,
        stop_reason=result.stop_reason,
        cutoff_date=cutoff,
        through_date=through_date,
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
    publisher = article.source or "北京市通州区人民政府"
    if not article.text:
        warnings.append("article_body_empty")
    if article.published_date and article.published_date != entry.published_date:
        warnings.append("list_and_article_date_mismatch")
    if any(item.get("status") == "failed" for item in attachments):
        warnings.append("attachment_download_failed")
    return {
        "schema_version": "1.0",
        "record_id": f"sha256:{hashlib.sha256(entry.url.encode('utf-8')).hexdigest()}",
        "run_id": run_id,
        "task_id": "tongzhou_recruitment_announcements",
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
        "selection": {
            "required_title_keywords": list(REQUIRED_TITLE_KEYWORDS),
            "matched": True,
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
    record_count: int,
    error_count: int,
) -> tuple[str, int]:
    if selected_count > 0 and record_count == 0:
        return "failed", 1
    if error_count > 0:
        return "completed_with_errors", 0
    return "completed", 0


def run(args: argparse.Namespace, logger: CrawlerLogger) -> int:
    started_at = time.monotonic()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "articles").mkdir(exist_ok=True)
    (output_dir / "attachments").mkdir(exist_ok=True)
    event_log_path = output_dir / "crawler-events.jsonl"
    logger.attach_event_log(event_log_path)

    now = datetime.now(BEIJING_TIMEZONE)
    through_date = now.date()
    cutoff = (
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
        "北京市通州区招聘公告爬虫开始运行。",
        schema_version=1,
        run_id=args.run_id,
        source_url=args.url,
        output_dir=output_dir,
        cutoff_date=cutoff,
        through_date=through_date,
        required_title_keywords=list(REQUIRED_TITLE_KEYWORDS),
        max_pages=args.max_pages,
        max_articles=args.max_articles,
        max_attachment_mb=args.max_attachment_mb,
        skip_attachments=args.skip_attachments,
        request_delay_seconds=args.request_delay,
        timeout_seconds=args.timeout,
        retries=args.retries,
    )

    scan = collect_entries(
        client=client,
        logger=logger,
        base_url=args.url,
        cutoff=cutoff,
        through_date=through_date,
        output_dir=output_dir,
        max_pages=args.max_pages,
        max_articles=args.max_articles,
    )
    if not scan.entries:
        logger.info(
            "list.scan.no_matches",
            "最近一个月内没有标题同时包含“招聘”和“公告”的文章。",
            cutoff_date=cutoff,
            through_date=through_date,
            required_title_keywords=list(REQUIRED_TITLE_KEYWORDS),
        )

    records: list[dict[str, object]] = []
    errors: list[dict[str, object]] = []
    filtered_after_fetch = 0
    attachments_found = 0
    attachments_downloaded = 0
    attachments_skipped = 0
    attachments_failed = 0

    for index, entry in enumerate(scan.entries, start=1):
        article_started_at = time.monotonic()
        logger.info(
            "article.fetch.start",
            "开始抓取符合条件的文章详情。",
            article_number=index,
            article_count=len(scan.entries),
            title=entry.title,
            url=entry.url,
            list_published_date=entry.published_date,
        )
        try:
            response = client.get(entry.url, referer=entry.list_page_url)
            if not is_article_url(response.final_url):
                raise ValueError(f"Article redirected outside the configured section: {response.final_url}")
            html = decode_html(response)
            article, found_main_text = parse_article_page(html, response.final_url)
            if not found_main_text:
                raise ValueError("Article container div#mainText was not found")

            effective_title = article.title or entry.title
            effective_date = article.published_date or entry.published_date
            if not title_matches(effective_title):
                filtered_after_fetch += 1
                logger.warning(
                    "article.filtered.detail_title",
                    "详情页标题未同时包含“招聘”和“公告”，已忽略。",
                    list_title=entry.title,
                    detail_title=article.title,
                    url=entry.url,
                )
                continue
            if effective_date < cutoff or effective_date > through_date:
                filtered_after_fetch += 1
                logger.warning(
                    "article.filtered.detail_date",
                    "详情页日期不在最近一个月范围内，已忽略。",
                    title=effective_title,
                    url=entry.url,
                    detail_published_date=effective_date,
                    cutoff_date=cutoff,
                    through_date=through_date,
                )
                continue

            logger.info(
                "article.parse.success",
                "文章详情抓取并解析完成。",
                article_number=index,
                article_count=len(scan.entries),
                title=effective_title,
                url=entry.url,
                final_url=response.final_url,
                http_status=response.status,
                response_bytes=len(response.body),
                published_date=effective_date,
                source=article.source or "北京市通州区人民政府",
                text_characters=len(article.text),
                attachments_found=len(article.attachments),
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

            if args.skip_attachments:
                attachments = describe_skipped_attachments(article.attachments)
                for attachment in attachments:
                    attachment["skip_reason"] = "disabled"
                logger.info(
                    "attachment.batch.skipped",
                    "已按运行参数跳过所有附件下载。",
                    article_url=entry.url,
                    attachments_found=len(attachments),
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
            attachments_found += len(attachments)
            attachments_downloaded += sum(item.get("status") == "downloaded" for item in attachments)
            attachments_skipped += sum(item.get("status") == "skipped" for item in attachments)
            attachments_failed += sum(item.get("status") == "failed" for item in attachments)
            logger.info(
                "article.completed",
                "文章正文和附件处理完成。",
                article_number=index,
                article_count=len(scan.entries),
                title=record["title"],
                url=entry.url,
                status=record["status"],
                attachments_found=len(attachments),
                attachments_downloaded=sum(item.get("status") == "downloaded" for item in attachments),
                attachments_skipped=sum(item.get("status") == "skipped" for item in attachments),
                attachments_failed=sum(item.get("status") == "failed" for item in attachments),
                elapsed_ms=round((time.monotonic() - article_started_at) * 1000),
            )
        except Exception as error:
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
                "文章抓取失败，已记录错误并继续下一篇。",
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
    write_jsonl(errors_path, errors)
    validated_candidate_count = max(0, len(scan.entries) - filtered_after_fetch)
    status, exit_code = determine_run_outcome(validated_candidate_count, len(records), len(errors))
    summary = {
        "schema_version": 1,
        "run_id": args.run_id,
        "status": status,
        "source_url": args.url,
        "cutoff_date": cutoff.isoformat(),
        "through_date": through_date.isoformat(),
        "required_title_keywords": list(REQUIRED_TITLE_KEYWORDS),
        "pages_fetched": scan.pages_fetched,
        "total_pages_reported": scan.total_pages_reported,
        "list_entries_found": scan.list_entries_found,
        "articles_selected": len(scan.entries),
        "articles_selected_after_detail_validation": validated_candidate_count,
        "articles_succeeded": len(records),
        "articles_failed": sum(error.get("kind") == "article" for error in errors),
        "articles_filtered_after_fetch": filtered_after_fetch,
        "skipped_before_cutoff": scan.skipped_before_cutoff,
        "skipped_after_today": scan.skipped_after_today,
        "skipped_by_keywords": scan.skipped_by_keywords,
        "duplicate_entries": scan.duplicate_entries,
        "invalid_entries": scan.invalid_entries,
        "scan_stop_reason": scan.stop_reason,
        "attachments_found": attachments_found,
        "attachments_downloaded": attachments_downloaded,
        "attachments_skipped": attachments_skipped,
        "attachments_failed": attachments_failed,
        "max_attachment_mb": args.max_attachment_mb,
        "event_log_path": event_log_path.relative_to(output_dir).as_posix(),
        "records_path": records_path.relative_to(output_dir).as_posix(),
        "errors_path": errors_path.relative_to(output_dir).as_posix(),
        "finished_at": datetime.now(BEIJING_TIMEZONE).isoformat(timespec="seconds"),
        "elapsed_ms": round((time.monotonic() - started_at) * 1000),
    }
    write_json(summary_path, summary)
    logger.info(
        "output.completed",
        "抓取结果文件写入完成。",
        records_path=records_path,
        errors_path=errors_path,
        summary_path=summary_path,
        record_count=len(records),
        error_count=len(errors),
        summary_status=status,
    )
    finish_message = {
        "completed": "北京市通州区招聘公告爬虫运行成功完成。",
        "completed_with_errors": "爬虫运行完成，但存在已记录的错误。",
        "failed": "爬虫运行失败，符合条件的文章均未成功保存。",
    }[status]
    log_finish = logger.error if status == "failed" else (logger.warning if errors else logger.info)
    log_finish(
        "crawler.finish",
        finish_message,
        **summary,
        exit_code=exit_code,
    )
    return exit_code


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Crawl Tongzhou notices from the previous calendar month whose titles contain "
            "both 招聘 and 公告."
        )
    )
    parser.add_argument("--url", default=DEFAULT_LIST_URL)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=0,
        help="Override the default previous-calendar-month range; 0 keeps the default.",
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
    if not is_configured_list_url(args.url):
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

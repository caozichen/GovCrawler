"""Crawl Beijing government portal recruitment announcements and attachments.

The target page is backed by a public JSON query endpoint. This crawler keeps
the website's official query semantics, then applies the stricter local rules:
the publication date must be within the recent range and the title must contain
both ``招聘`` and ``公告``. All artifacts stay below the runner-provided output
directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urljoin, urlsplit
from urllib.request import Request, urlopen

if __package__:
    from . import beijing_hr_recruitment as common
else:
    import beijing_hr_recruitment as common


DEFAULT_LIST_URL = "https://www.beijing.gov.cn/fwcj/quickQuery/page/zhaopin/"
QUERY_URL = "https://www.beijing.gov.cn/fwcj/quickQuery/mongo/v2/query"
CATEGORY_ID = "67f7341341ecec12898cb198"
COLLECTION_ID = "1003"
REQUIRED_TITLE_KEYWORDS = ("招聘", "公告")
SERVER_TITLE_KEYWORD = "公告"
DEFAULT_PAGE_SIZE = 100
DEFAULT_REQUEST_DELAY_SECONDS = 0.35
DEFAULT_TIMEOUT_SECONDS = 45
DEFAULT_MAX_ATTACHMENT_MB = 5
MAX_QUERY_RESPONSE_BYTES = 40 * 1024 * 1024
ATTACHMENT_FIELDS = (
    ("attachments", "t1"),
    ("c1", "t3"),
    ("l4", "c2"),
    ("l3", "l22"),
)

BEIJING_TIMEZONE = common.BEIJING_TIMEZONE
LOG_LEVELS = common.LOG_LEVELS
AttachmentLink = common.AttachmentLink
CrawlerLogger = common.CrawlerLogger
HttpResponse = common.HttpResponse
configure_console_encoding = common.configure_console_encoding
describe_skipped_attachments = common.describe_skipped_attachments
download_attachments = common.download_attachments
normalize_inline_text = common.normalize_inline_text
normalize_multiline_text = common.normalize_multiline_text
parse_iso_date = common.parse_iso_date
previous_calendar_month = common.previous_calendar_month
safe_filename = common.safe_filename
without_fragment = common.without_fragment
write_json = common.write_json
write_jsonl = common.write_jsonl


@dataclass(frozen=True)
class PortalEntry:
    term_id: str
    title: str
    published_date: date
    detail_url: str
    original_url: str
    publisher: str
    content: str
    content_html: str
    attachments: list[AttachmentLink]
    invalid_attachment_urls: list[str]
    raw_item: dict[str, object]


@dataclass
class ScanResult:
    entries: list[PortalEntry]
    pages_fetched: int = 0
    total_pages_reported: int = 0
    total_items_reported: int = 0
    items_received: int = 0
    invalid_items: int = 0
    duplicate_items: int = 0
    skipped_before_cutoff: int = 0
    skipped_after_today: int = 0
    skipped_by_keywords: int = 0
    stop_reason: str = ""


class HtmlTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.ignored_depth = 0

    def handle_starttag(self, tag: str, _attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if self.ignored_depth:
            if tag not in common.VOID_TAGS:
                self.ignored_depth += 1
            return
        if tag in common.IGNORED_BODY_TAGS:
            self.ignored_depth = 1
        elif tag in common.BLOCK_TAGS or tag == "br":
            self.parts.append("\n")
        elif tag in {"td", "th"}:
            self.parts.append("\t")

    def handle_endtag(self, tag: str) -> None:
        if self.ignored_depth:
            self.ignored_depth -= 1
        elif tag.lower() in common.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.ignored_depth:
            self.parts.append(data)


class PortalHttpClient(common.HttpClient):
    def post_json(
        self,
        url: str,
        payload: Mapping[str, object],
        referer: str,
    ) -> dict[str, object]:
        encoded_payload = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            self._throttle()
            request = Request(
                url,
                data=encoded_payload,
                method="POST",
                headers={
                    "User-Agent": common.USER_AGENT,
                    "Accept": "application/json",
                    "Content-Type": "application/json; charset=utf-8",
                    "Referer": referer,
                },
            )
            started_at = time.monotonic()
            self.logger.debug(
                "http.query.request.start",
                "发起招聘数据查询请求。",
                url=url,
                page=payload.get("page"),
                page_size=payload.get("pageSize"),
                attempt=attempt + 1,
                max_attempts=self.retries + 1,
            )
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    final_url = without_fragment(response.geturl())
                    if not is_query_url(final_url):
                        raise ValueError(f"Query redirected outside the expected endpoint: {final_url}")
                    body = response.read(MAX_QUERY_RESPONSE_BYTES + 1)
                    if len(body) > MAX_QUERY_RESPONSE_BYTES:
                        raise ValueError(f"Query response exceeds {MAX_QUERY_RESPONSE_BYTES} bytes")
                    value = json.loads(body.decode("utf-8-sig"))
                    if not isinstance(value, dict):
                        raise ValueError("Query response root must be a JSON object")
                    self.logger.debug(
                        "http.query.request.success",
                        "招聘数据查询请求成功。",
                        url=url,
                        final_url=final_url,
                        status=getattr(response, "status", 200),
                        size_bytes=len(body),
                        elapsed_ms=round((time.monotonic() - started_at) * 1000),
                        attempt=attempt + 1,
                    )
                    return value
            except HTTPError as error:
                last_error = error
                if error.code not in common.RETRYABLE_HTTP_CODES or attempt >= self.retries:
                    self.logger.error(
                        "http.query.request.failed",
                        "招聘数据查询请求失败。",
                        url=url,
                        status=error.code,
                        reason=str(error.reason),
                        attempt=attempt + 1,
                        elapsed_ms=round((time.monotonic() - started_at) * 1000),
                    )
                    raise
                self._sleep_before_retry(attempt, url, error, error.headers.get("Retry-After"))
            except (URLError, TimeoutError, OSError, UnicodeError, json.JSONDecodeError) as error:
                last_error = error
                if attempt >= self.retries:
                    self.logger.error(
                        "http.query.request.failed",
                        "招聘数据查询请求失败。",
                        url=url,
                        error_type=type(error).__name__,
                        error=str(error),
                        attempt=attempt + 1,
                        elapsed_ms=round((time.monotonic() - started_at) * 1000),
                    )
                    raise
                self._sleep_before_retry(attempt, url, error)
        raise RuntimeError(f"Query request failed: {url}") from last_error


def is_configured_list_url(url: str) -> bool:
    parsed = urlsplit(url)
    return (
        parsed.scheme == "https"
        and parsed.netloc.lower() == "www.beijing.gov.cn"
        and parsed.path.rstrip("/") == "/fwcj/quickQuery/page/zhaopin"
        and not parsed.query
        and not parsed.fragment
    )


def is_query_url(url: str) -> bool:
    parsed = urlsplit(url)
    return (
        parsed.scheme == "https"
        and parsed.netloc.lower() == "www.beijing.gov.cn"
        and parsed.path == "/fwcj/quickQuery/mongo/v2/query"
        and not parsed.query
        and not parsed.fragment
    )


def is_attachment_resource_url(url: str) -> bool:
    parsed = urlsplit(url)
    host = parsed.netloc.lower().split(":", 1)[0]
    filename = Path(parsed.path).name
    allowed_host = (
        host == "beijing.gov.cn"
        or host.endswith(".beijing.gov.cn")
        or host == "bjhr.gov.cn"
        or host.endswith(".bjhr.gov.cn")
    )
    return (
        parsed.scheme == "https"
        and allowed_host
        and bool(filename)
        and Path(filename).suffix.lower() in common.DOWNLOAD_EXTENSIONS
        and not parsed.fragment
    )


def title_matches(title: str) -> bool:
    normalized = normalize_inline_text(title)
    return all(keyword in normalized for keyword in REQUIRED_TITLE_KEYWORDS)


def entry_filter_reason(
    title: str,
    published_date: date,
    cutoff: date,
    through_date: date,
) -> str | None:
    if published_date < cutoff:
        return "before_cutoff"
    if published_date > through_date:
        return "after_today"
    if not title_matches(title):
        return "keyword_mismatch"
    return None


def html_to_text(value: str) -> str:
    parser = HtmlTextParser()
    parser.feed(value)
    parser.close()
    return normalize_multiline_text("".join(parser.parts))


def build_query(page: int, page_size: int) -> dict[str, object]:
    return {
        "page": page,
        "pageSize": page_size,
        "sortField": "createTime",
        "sortType": "desc",
        "collection": COLLECTION_ID,
        "and": [
            {
                "fieldName": "cmsCategoryId",
                "criteriaType": "=",
                "queryFieldValue": CATEGORY_ID,
                "queryFieldValueType": "string",
            },
            {
                "fieldName": "deleted",
                "criteriaType": "=",
                "queryFieldValue": 0,
                "queryFieldValueType": "int",
            },
            {
                "fieldName": "title",
                "criteriaType": "like",
                "queryFieldValue": SERVER_TITLE_KEYWORD,
                "queryFieldValueType": "string",
            },
        ],
        "or": [],
    }


def parse_query_response(value: object) -> tuple[list[dict[str, object]], int, int]:
    if not isinstance(value, dict):
        raise ValueError("Query response root must be an object")
    if value.get("success") is not True or value.get("code") != 1000:
        raise ValueError(
            f"Query API rejected the request: code={value.get('code')!r}, message={value.get('message')!r}"
        )
    raw_items = value.get("data")
    if not isinstance(raw_items, list):
        raise ValueError("Query response data must be an array")
    items = [item for item in raw_items if isinstance(item, dict)]
    if len(items) != len(raw_items):
        raise ValueError("Query response data contains a non-object item")
    total_pages = parse_nonnegative_int(value.get("totalPages"), "totalPages")
    total_items = parse_nonnegative_int(value.get("allCount"), "allCount")
    return items, total_pages, total_items


def parse_nonnegative_int(value: object, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"Query response {field} must be a non-negative integer")
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as error:
        raise ValueError(f"Query response {field} must be a non-negative integer") from error
    if parsed < 0:
        raise ValueError(f"Query response {field} must be a non-negative integer")
    return parsed


def detail_url(term_id: str, list_url: str = DEFAULT_LIST_URL) -> str:
    return urljoin(list_url, f"detail.html?id={quote(term_id, safe='')}")


def parse_api_item(item: Mapping[str, object], list_url: str = DEFAULT_LIST_URL) -> PortalEntry:
    term_id = normalize_inline_text(str(item.get("termId") or ""))
    title = normalize_inline_text(str(item.get("title") or ""))
    basic = item.get("termBasicInfo")
    if not term_id or not title or not isinstance(basic, dict):
        raise ValueError("API item is missing termId, title, or termBasicInfo")
    published_date = parse_iso_date(str(basic.get("l20") or ""))
    if published_date is None:
        published_date = parse_timestamp_date(str(item.get("publishTime") or ""))
    if published_date is None:
        raise ValueError("API item has no valid publication date")

    content_html = str(item.get("contentHtml") or "")
    content = normalize_multiline_text(str(item.get("content") or ""))
    if not content and content_html:
        content = html_to_text(content_html)
    publisher = normalize_inline_text(str(basic.get("websitename") or "")) or "北京市人民政府门户网站"
    original_url = without_fragment(str(basic.get("url") or "").strip())

    attachments: list[AttachmentLink] = []
    invalid_urls: list[str] = []
    seen_urls: set[str] = set()
    for position, (url_field, name_field) in enumerate(ATTACHMENT_FIELDS, start=1):
        raw_url = str(basic.get(url_field) or "").strip()
        if not raw_url:
            continue
        attachment_url = without_fragment(urljoin(list_url, raw_url))
        if attachment_url in seen_urls:
            continue
        seen_urls.add(attachment_url)
        if not is_attachment_resource_url(attachment_url):
            invalid_urls.append(attachment_url)
            continue
        remote_filename = unquote(Path(urlsplit(attachment_url).path).name)
        display_name = normalize_inline_text(str(basic.get(name_field) or ""))
        attachments.append(
            AttachmentLink(
                display_name=display_name or remote_filename or f"附件{position}",
                url=attachment_url,
                remote_filename=remote_filename,
            )
        )

    return PortalEntry(
        term_id=term_id,
        title=title,
        published_date=published_date,
        detail_url=detail_url(term_id, list_url),
        original_url=original_url,
        publisher=publisher,
        content=content,
        content_html=content_html,
        attachments=attachments,
        invalid_attachment_urls=invalid_urls,
        raw_item=dict(item),
    )


def parse_timestamp_date(value: str) -> date | None:
    normalized = value.strip().replace("Z", "+00:00")
    if not normalized:
        return None
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return parse_iso_date(normalized)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(BEIJING_TIMEZONE)
    return parsed.date()


def atomic_write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def collect_entries(
    client: PortalHttpClient,
    logger: CrawlerLogger,
    list_url: str,
    cutoff: date,
    through_date: date,
    output_dir: Path,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_pages: int = 0,
    max_articles: int = 0,
) -> ScanResult:
    result = ScanResult(entries=[])
    seen_ids: set[str] = set()
    page = 0

    while True:
        if max_pages and result.pages_fetched >= max_pages:
            result.stop_reason = "max_pages"
            logger.warning(
                "query.scan.limit.max_pages",
                "已达到查询分页上限，停止扫描。",
                max_pages=max_pages,
                pages_fetched=result.pages_fetched,
            )
            break

        payload = build_query(page, page_size)
        logger.info(
            "query.page.start",
            "开始查询招聘数据页。",
            page_number=page + 1,
            page_index=page,
            page_size=page_size,
            query_url=QUERY_URL,
            server_title_keyword=SERVER_TITLE_KEYWORD,
        )
        started_at = time.monotonic()
        response = client.post_json(QUERY_URL, payload, referer=list_url)
        result.pages_fetched += 1
        api_page_path = output_dir / "api-pages" / f"page-{page + 1:03d}.json"
        atomic_write_json(api_page_path, response)
        items, total_pages, total_items = parse_query_response(response)
        result.total_pages_reported = total_pages
        result.total_items_reported = total_items
        result.items_received += len(items)
        logger.info(
            "query.page.success",
            "招聘数据页查询并保存完成。",
            page_number=page + 1,
            items_received=len(items),
            total_pages=total_pages,
            total_items=total_items,
            raw_path=api_page_path.relative_to(output_dir).as_posix(),
            elapsed_ms=round((time.monotonic() - started_at) * 1000),
        )

        for item_number, item in enumerate(items, start=1):
            try:
                entry = parse_api_item(item, list_url)
            except Exception as error:
                result.invalid_items += 1
                logger.warning(
                    "query.item.invalid",
                    "招聘数据条目字段无效，已跳过。",
                    page_number=page + 1,
                    item_number=item_number,
                    term_id=item.get("termId"),
                    title=item.get("title"),
                    error_type=type(error).__name__,
                    error=str(error),
                )
                continue
            if entry.term_id in seen_ids:
                result.duplicate_items += 1
                logger.warning(
                    "query.item.duplicate",
                    "检测到重复招聘数据，已跳过。",
                    term_id=entry.term_id,
                    title=entry.title,
                    detail_url=entry.detail_url,
                )
                continue
            seen_ids.add(entry.term_id)
            reason = entry_filter_reason(entry.title, entry.published_date, cutoff, through_date)
            if reason:
                if reason == "before_cutoff":
                    result.skipped_before_cutoff += 1
                elif reason == "after_today":
                    result.skipped_after_today += 1
                else:
                    result.skipped_by_keywords += 1
                logger.debug(
                    "query.item.filtered",
                    "招聘数据不符合本次抓取条件，已过滤。",
                    term_id=entry.term_id,
                    title=entry.title,
                    published_date=entry.published_date,
                    reason=reason,
                    cutoff_date=cutoff,
                    through_date=through_date,
                )
                continue
            result.entries.append(entry)
            logger.info(
                "query.item.selected",
                "检测到符合条件的招聘公告。",
                selected_number=len(result.entries),
                term_id=entry.term_id,
                title=entry.title,
                published_date=entry.published_date,
                detail_url=entry.detail_url,
                attachments_found=len(entry.attachments),
            )
            if max_articles and len(result.entries) >= max_articles:
                result.stop_reason = "max_articles"
                logger.warning(
                    "query.scan.limit.max_articles",
                    "已达到文章数量上限，停止扫描。",
                    max_articles=max_articles,
                )
                return result

        page += 1
        if not items:
            result.stop_reason = "empty_page"
            break
        if total_pages == 0 or page >= total_pages:
            result.stop_reason = "all_pages_scanned"
            break

    return result


def build_record(
    entry: PortalEntry,
    attachments: list[dict[str, object]],
    raw_item_path: Path,
    output_dir: Path,
    run_id: str,
    fetched_at: str,
) -> dict[str, object]:
    warnings: list[str] = []
    if not entry.content:
        warnings.append("article_body_empty")
    if entry.invalid_attachment_urls:
        warnings.append("unsafe_attachment_url_skipped")
    if any(item.get("status") == "failed" for item in attachments):
        warnings.append("attachment_download_failed")
    return {
        "schema_version": "1.0",
        "record_id": f"sha256:{hashlib.sha256(entry.detail_url.encode('utf-8')).hexdigest()}",
        "run_id": run_id,
        "task_id": "beijing_portal_recruitment_announcements",
        "status": "ok" if not warnings else "completed_with_warnings",
        "source_url": entry.detail_url,
        "published_date": entry.published_date.isoformat(),
        "publisher": entry.publisher,
        "content_text": entry.content,
        "source": {
            "url": entry.detail_url,
            "list_page_url": DEFAULT_LIST_URL,
            "original_url": entry.original_url or None,
            "query_url": QUERY_URL,
            "published_at": entry.published_date.isoformat(),
            "fetched_at": fetched_at,
            "publisher": entry.publisher,
        },
        "title": entry.title,
        "text": entry.content,
        "attachments": attachments,
        "provenance": {
            "crawler": Path(__file__).name,
            "term_id": entry.term_id,
            "raw_json_path": raw_item_path.relative_to(output_dir).as_posix(),
            "content_hash": f"sha256:{hashlib.sha256(entry.content.encode('utf-8')).hexdigest()}",
        },
        "quality": {"parse_ok": bool(entry.content), "warnings": warnings},
    }


def determine_run_outcome(
    selected_count: int,
    succeeded_count: int,
    article_failure_count: int,
    error_count: int = 0,
) -> tuple[str, int]:
    if selected_count > 0 and succeeded_count == 0 and article_failure_count >= selected_count:
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
    (output_dir / "articles").mkdir(parents=True, exist_ok=True)

    now = datetime.now(BEIJING_TIMEZONE)
    cutoff = now.date() - timedelta(days=args.lookback_days) if args.lookback_days else previous_calendar_month(now.date())
    logger.info(
        "crawler.start",
        "北京市人民政府门户网站招聘公告爬虫启动。",
        run_id=args.run_id,
        source_url=args.url,
        query_url=QUERY_URL,
        output_dir=output_dir,
        cutoff_date=cutoff,
        through_date=now.date(),
        required_title_keywords=list(REQUIRED_TITLE_KEYWORDS),
        page_size=args.page_size,
        max_pages=args.max_pages or None,
        max_articles=args.max_articles or None,
        max_attachment_mb=args.max_attachment_mb,
        skip_attachments=args.skip_attachments,
        timeout_seconds=args.timeout,
        retries=args.retries,
        request_delay_seconds=args.request_delay,
        python=sys.version.split()[0],
    )
    client = PortalHttpClient(
        timeout=args.timeout,
        retries=args.retries,
        request_delay=args.request_delay,
        logger=logger,
        attachment_url_validator=is_attachment_resource_url,
    )
    errors: list[dict[str, object]] = []
    records: list[dict[str, object]] = []

    scan = collect_entries(
        client=client,
        logger=logger,
        list_url=args.url,
        cutoff=cutoff,
        through_date=now.date(),
        output_dir=output_dir,
        page_size=args.page_size,
        max_pages=args.max_pages,
        max_articles=args.max_articles,
    )
    logger.info(
        "article.batch.selected",
        "招聘公告筛选完成，准备保存正文并处理附件。",
        articles_selected=len(scan.entries),
        pages_fetched=scan.pages_fetched,
        items_received=scan.items_received,
        skipped_before_cutoff=scan.skipped_before_cutoff,
        skipped_after_today=scan.skipped_after_today,
        skipped_by_keywords=scan.skipped_by_keywords,
        invalid_items=scan.invalid_items,
    )

    attachments_found = 0
    attachments_downloaded = 0
    article_failures = 0
    for article_number, entry in enumerate(scan.entries, start=1):
        article_started_at = time.monotonic()
        logger.info(
            "article.process.start",
            "开始整理招聘公告正文与附件。",
            article_number=article_number,
            article_count=len(scan.entries),
            term_id=entry.term_id,
            title=entry.title,
            published_date=entry.published_date,
            detail_url=entry.detail_url,
            original_url=entry.original_url or None,
        )
        try:
            if not entry.content:
                raise ValueError("Article body is empty in the query response")
            raw_item_path = output_dir / "articles" / f"{safe_filename(entry.term_id)}.json"
            atomic_write_json(raw_item_path, entry.raw_item)
            logger.info(
                "article.raw_json.saved",
                "招聘公告原始 JSON 已保存。",
                term_id=entry.term_id,
                path=raw_item_path.relative_to(output_dir).as_posix(),
                size_bytes=raw_item_path.stat().st_size,
            )

            for invalid_url in entry.invalid_attachment_urls:
                errors.append(
                    {
                        "kind": "attachment_url",
                        "article_url": entry.detail_url,
                        "url": invalid_url,
                        "message": "Attachment URL is outside the allowed Beijing government hosts or file types",
                    }
                )
                logger.warning(
                    "attachment.detected.invalid_url",
                    "检测到不符合安全规则的附件地址，已跳过。",
                    article_url=entry.detail_url,
                    url=invalid_url,
                )
            for attachment_number, attachment in enumerate(entry.attachments, start=1):
                logger.info(
                    "attachment.detected",
                    "检测到招聘公告附件。",
                    article_url=entry.detail_url,
                    attachment_number=attachment_number,
                    attachment_count=len(entry.attachments),
                    display_name=attachment.display_name,
                    remote_filename=attachment.remote_filename,
                    url=attachment.url,
                )

            if args.skip_attachments:
                attachments = describe_skipped_attachments(entry.attachments)
                logger.warning(
                    "attachment.batch.skipped",
                    "已根据运行参数跳过本公告的全部附件下载。",
                    article_url=entry.detail_url,
                    attachments_skipped=len(attachments),
                )
            else:
                attachments = download_attachments(
                    client=client,
                    logger=logger,
                    links=entry.attachments,
                    output_dir=output_dir,
                    article_id=safe_filename(entry.term_id),
                    article_url=entry.detail_url,
                    max_attachment_bytes=args.max_attachment_mb * 1024 * 1024,
                    errors=errors,
                )
            attachments_found += len(attachments) + len(entry.invalid_attachment_urls)
            attachments_downloaded += sum(item.get("status") == "downloaded" for item in attachments)
            record = build_record(
                entry=entry,
                attachments=attachments,
                raw_item_path=raw_item_path,
                output_dir=output_dir,
                run_id=args.run_id,
                fetched_at=datetime.now(BEIJING_TIMEZONE).isoformat(timespec="seconds"),
            )
            records.append(record)
            logger.info(
                "article.completed",
                "招聘公告正文与附件处理完成。",
                article_number=article_number,
                article_count=len(scan.entries),
                term_id=entry.term_id,
                title=entry.title,
                status=record["status"],
                text_characters=len(entry.content),
                attachments_found=len(entry.attachments),
                attachments_downloaded=sum(item.get("status") == "downloaded" for item in attachments),
                attachments_skipped=sum(item.get("status") == "skipped" for item in attachments),
                attachments_failed=sum(item.get("status") == "failed" for item in attachments),
                elapsed_ms=round((time.monotonic() - article_started_at) * 1000),
            )
        except Exception as error:
            article_failures += 1
            errors.append(
                {
                    "kind": "article",
                    "term_id": entry.term_id,
                    "title": entry.title,
                    "url": entry.detail_url,
                    "published_at": entry.published_date.isoformat(),
                    "error_type": type(error).__name__,
                    "message": str(error),
                }
            )
            logger.exception(
                "article.failed",
                "招聘公告处理失败，继续处理下一条。",
                article_number=article_number,
                article_count=len(scan.entries),
                term_id=entry.term_id,
                title=entry.title,
                url=entry.detail_url,
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
        "query_url": QUERY_URL,
        "cutoff_date": cutoff.isoformat(),
        "through_date": now.date().isoformat(),
        "required_title_keywords": list(REQUIRED_TITLE_KEYWORDS),
        "pages_fetched": scan.pages_fetched,
        "total_pages_reported": scan.total_pages_reported,
        "total_items_reported": scan.total_items_reported,
        "items_received": scan.items_received,
        "articles_selected": len(scan.entries),
        "articles_succeeded": len(records),
        "articles_failed": article_failures,
        "attachments_found": attachments_found,
        "attachments_downloaded": attachments_downloaded,
        "attachments_skipped": sum(
            item.get("status") == "skipped" for record in records for item in record["attachments"]
        ),
        "attachments_failed": sum(
            item.get("status") == "failed" for record in records for item in record["attachments"]
        ),
        "invalid_items": scan.invalid_items,
        "duplicate_items": scan.duplicate_items,
        "skipped_before_cutoff": scan.skipped_before_cutoff,
        "skipped_after_today": scan.skipped_after_today,
        "skipped_by_keywords": scan.skipped_by_keywords,
        "stop_reason": scan.stop_reason,
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
        logger.error("crawler.finish", "爬虫运行失败，符合条件的公告均未成功保存。", **finish_fields)
    elif errors:
        logger.warning("crawler.finish", "爬虫运行完成，但存在已记录的局部错误。", **finish_fields)
    else:
        logger.info("crawler.finish", "北京市人民政府门户网站招聘公告爬虫运行成功。", **finish_fields)
    return exit_code


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Crawl Beijing portal items from the recent month whose titles contain both 招聘 and 公告."
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
    parser.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE)
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
    if args.page_size < 1 or args.page_size > 1000:
        raise ValueError("--page-size must be between 1 and 1000")
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

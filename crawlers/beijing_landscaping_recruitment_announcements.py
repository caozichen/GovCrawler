"""Crawl recent Beijing Landscaping Bureau notices containing 招聘 and 公告.

The script follows the PA console crawler protocol, uses only the Python
standard library, and stores all artifacts in the runner-provided directory.
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


DEFAULT_LIST_URL = "https://yllhj.beijing.gov.cn/zwgk/zkzp/"
LIST_SECTION_PATH = "/zwgk/zkzp/"
ARTICLE_PATH_PATTERN = re.compile(
    r"^/zwgk/zkzp/\d{6}/t\d{8}_\d+\.shtml$",
    re.IGNORECASE,
)
LIST_PAGE_PATH_PATTERN = re.compile(
    r"^/zwgk/zkzp/(?:index(?:_\d+)?\.shtml)?$",
    re.IGNORECASE,
)
PAGE_NUMBER_PATTERN = re.compile(
    r"/zwgk/zkzp/index_(\d+)\.shtml",
    re.IGNORECASE,
)
PAGE_COUNT_PATTERN = re.compile(r"var\s+countPage\s*=\s*(\d+)", re.IGNORECASE)
DATE_PATTERN = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")
ALLOWED_HOSTS = {"yllhj.beijing.gov.cn"}
REQUIRED_TITLE_KEYWORDS = ("招聘", "公告")
IGNORED_DETAIL_CLASSES = {
    "ewmtxt",
    "qrcode",
    "share",
}


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


class ListPageParser(HTMLParser):
    def __init__(self, page_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.page_url = page_url
        self.entries: list[ListEntry] = []
        self.page_count = 1
        self._current: dict[str, object] | None = None
        self._list_depth = 0
        self._container_depth = 0
        self._link_depth = 0
        self._date_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attributes = {key.lower(): value or "" for key, value in attrs}
        pagination_source = " ".join(
            (
                attributes.get("href", ""),
                attributes.get("tagname", ""),
                attributes.get("onclick", ""),
            )
        )
        page_match = PAGE_NUMBER_PATTERN.search(pagination_source)
        if page_match:
            self.page_count = max(self.page_count, int(page_match.group(1)) + 1)
        if tag == "input" and attributes.get("name", "").lower() == "article_paging_list_hidden":
            try:
                self.page_count = max(self.page_count, int(attributes.get("totalpage", "1")))
            except ValueError:
                pass

        classes = {item.lower() for item in attributes.get("class", "").split()}
        if tag == "ul":
            if self._list_depth:
                self._list_depth += 1
            elif "newslist" in classes:
                self._list_depth = 1

        if not self._list_depth:
            return
        if self._current is None:
            if tag == "li":
                self._current = {
                    "url": "",
                    "title_attribute": "",
                    "title_parts": [],
                    "date_parts": [],
                }
                self._container_depth = 1
            return

        is_void = tag in VOID_TAGS
        if not is_void:
            self._container_depth += 1

        if self._link_depth > 0 and not is_void:
            self._link_depth += 1
        elif tag == "a" and not self._current["url"]:
            candidate = without_fragment(urljoin(self.page_url, attributes.get("href", "")))
            if is_article_url(candidate):
                self._current["url"] = candidate
                self._current["title_attribute"] = attributes.get("title", "")
                self._link_depth = 1

        if self._date_depth > 0 and not is_void:
            self._date_depth += 1
        elif tag == "span" and self._link_depth == 0:
            self._date_depth = 1

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._current is not None:
            if self._link_depth > 0:
                self._link_depth -= 1
            if self._date_depth > 0:
                self._date_depth -= 1
            self._container_depth -= 1
            if self._container_depth == 0:
                self._finish_entry()
        if tag == "ul" and self._list_depth:
            self._list_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._current is None:
            return
        if self._link_depth > 0:
            title_parts = self._current["title_parts"]
            assert isinstance(title_parts, list)
            title_parts.append(data)
        if self._date_depth > 0:
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
            title = normalize_inline_text("".join(str(part) for part in title_parts))
        url = str(self._current["url"])
        published_date = parse_date("".join(str(part) for part in date_parts))
        if title and url and published_date:
            self.entries.append(
                ListEntry(
                    title=title,
                    url=url,
                    published_date=published_date,
                    list_page_url=self.page_url,
                )
            )
        self._current = None
        self._container_depth = 0
        self._link_depth = 0
        self._date_depth = 0


class ArticlePageParser(HTMLParser):
    def __init__(self, article_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.article_url = article_url
        self.meta: dict[str, str] = {}
        self.body_parts: list[str] = []
        self.attachments: list[AttachmentLink] = []
        self._article_depth = 0
        self._ignored_depth = 0
        self._attachment_depth = 0
        self._attachment_url = ""
        self._attachment_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attributes = {key.lower(): value or "" for key, value in attrs}
        is_void = tag in VOID_TAGS

        if tag == "meta":
            name = attributes.get("name", "").strip().lower()
            content = attributes.get("content", "").strip()
            if name and content:
                self.meta[name] = content

        classes = {item.lower() for item in attributes.get("class", "").split()}
        if self._article_depth == 0:
            if tag == "div" and ("article" in classes or attributes.get("id", "").lower() == "articlewrap"):
                self._article_depth = 1
            return
        if not is_void:
            self._article_depth += 1

        if self._attachment_depth > 0 and not is_void:
            self._attachment_depth += 1
        elif tag == "a":
            candidate = without_fragment(urljoin(self.article_url, attributes.get("href", "")))
            if is_attachment_resource_url(candidate):
                self._attachment_url = candidate
                self._attachment_parts = []
                self._attachment_depth = 1

        if self._ignored_depth > 0 and not is_void:
            self._ignored_depth += 1
        elif tag in IGNORED_BODY_TAGS and not is_void:
            self._ignored_depth = 1
        elif (
            classes.intersection(IGNORED_DETAIL_CLASSES)
            or attributes.get("id", "").lower() in IGNORED_DETAIL_CLASSES
        ) and not is_void:
            self._ignored_depth = 1
        elif self._ignored_depth == 0 and (tag in BLOCK_TAGS or tag == "br"):
            self.body_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._attachment_depth > 0:
            self._attachment_depth -= 1
            if self._attachment_depth == 0:
                self._finish_attachment()
        if self._ignored_depth > 0:
            self._ignored_depth -= 1
        elif self._article_depth > 0 and tag in BLOCK_TAGS:
            self.body_parts.append("\n")
        if self._article_depth > 0:
            self._article_depth -= 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self._article_depth > 0 and self._ignored_depth == 0:
            self.body_parts.append(data)
        if self._attachment_depth > 0:
            self._attachment_parts.append(data)

    def result(self) -> ArticleData:
        unique_attachments: list[AttachmentLink] = []
        seen_urls: set[str] = set()
        for attachment in self.attachments:
            if attachment.url not in seen_urls:
                seen_urls.add(attachment.url)
                unique_attachments.append(attachment)
        return ArticleData(
            title=normalize_inline_text(self.meta.get("articletitle", "")),
            published_date=parse_date(self.meta.get("pubdate", "")),
            source=normalize_inline_text(self.meta.get("contentsource", "")),
            text=normalize_multiline_text("".join(self.body_parts)),
            attachments=unique_attachments,
        )

    def _finish_attachment(self) -> None:
        if not self._attachment_url:
            return
        remote_filename = unquote(Path(urlsplit(self._attachment_url).path).name)
        display_name = normalize_inline_text("".join(self._attachment_parts))
        if not display_name:
            display_name = Path(remote_filename).stem or "附件"
        self.attachments.append(
            AttachmentLink(
                display_name=display_name,
                url=self._attachment_url,
                remote_filename=remote_filename,
            )
        )
        self._attachment_url = ""
        self._attachment_parts = []


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


def parse_list_page(html: str, page_url: str) -> tuple[list[ListEntry], int]:
    parser = ListPageParser(page_url)
    parser.feed(html)
    parser.close()
    count_match = PAGE_COUNT_PATTERN.search(html)
    if count_match:
        parser.page_count = max(parser.page_count, int(count_match.group(1)))
    entries: list[ListEntry] = []
    seen_urls: set[str] = set()
    for entry in parser.entries:
        if entry.url not in seen_urls:
            seen_urls.add(entry.url)
            entries.append(entry)
    return entries, parser.page_count


def parse_article_page(html: str, article_url: str) -> ArticleData:
    parser = ArticlePageParser(article_url)
    parser.feed(html)
    parser.close()
    return parser.result()


def is_list_url(url: str) -> bool:
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
    path_matches = re.match(
        r"^/zwgk/zkzp/\d{6}/[^/]+$",
        parsed.path,
        re.IGNORECASE,
    )
    return (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() in ALLOWED_HOSTS
        and bool(path_matches)
        and Path(parsed.path).suffix.lower() in DOWNLOAD_EXTENSIONS
        and not parsed.fragment
    )


def page_url(base_url: str, page_number: int) -> str:
    if page_number == 1:
        return base_url
    parsed = urlsplit(base_url)
    return f"{parsed.scheme}://{parsed.netloc}{LIST_SECTION_PATH}index_{page_number - 1}.shtml"


def article_identifier(url: str) -> str:
    return common.article_identifier(url)


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def collect_entries(
    client: HttpClient,
    logger: CrawlerLogger,
    base_url: str,
    cutoff_date: date,
    through_date: date,
    output_dir: Path,
    max_pages: int,
) -> tuple[list[ListEntry], int, bool]:
    selected: list[ListEntry] = []
    seen_urls: set[str] = set()
    page_number = 1
    page_count = 1
    pages_fetched = 0
    limited_by_max_pages = False

    while page_number <= page_count:
        if max_pages and pages_fetched >= max_pages:
            limited_by_max_pages = True
            logger.warning(
                "list.scan.limit.max_pages",
                "已达到列表页上限，停止扫描。",
                max_pages=max_pages,
                pages_fetched=pages_fetched,
                articles_selected=len(selected),
            )
            break
        current_url = page_url(base_url, page_number)
        logger.info(
            "list.page.fetch.start",
            "开始抓取通知公告列表页。",
            page_number=page_number,
            known_page_count=page_count,
            url=current_url,
        )
        page_started_at = time.monotonic()
        response = client.get(current_url, referer=base_url)
        if not is_list_url(response.final_url):
            raise ValueError(f"List page redirected outside the configured section: {response.final_url}")
        html = decode_html(response)
        raw_path = output_dir / "list-pages" / f"page-{page_number:03d}.html"
        atomic_write_text(raw_path, html)
        entries, detected_page_count = parse_list_page(html, response.final_url)
        page_count = max(page_count, detected_page_count)
        pages_fetched += 1
        if not entries:
            raise ValueError(f"No valid notice entries were found on list page {page_number}")

        in_range = [
            entry for entry in entries if cutoff_date <= entry.published_date <= through_date
        ]
        matching = [entry for entry in in_range if title_matches(entry.title)]
        for entry in matching:
            if entry.url in seen_urls:
                continue
            seen_urls.add(entry.url)
            selected.append(entry)
            logger.info(
                "list.article.selected",
                "公告标题同时包含“招聘”和“公告”，已加入抓取队列。",
                title=entry.title,
                url=entry.url,
                published_date=entry.published_date,
                page_number=page_number,
            )
        oldest_date = min(entry.published_date for entry in entries)
        newest_date = max(entry.published_date for entry in entries)
        logger.info(
            "list.page.fetch.success",
            "通知公告列表页抓取并筛选完成。",
            page_number=page_number,
            url=current_url,
            final_url=response.final_url,
            http_status=response.status,
            response_bytes=len(response.body),
            entries_found=len(entries),
            entries_in_date_range=len(in_range),
            keyword_matches=len(matching),
            newest_date=newest_date,
            oldest_date=oldest_date,
            raw_html_path=raw_path.relative_to(output_dir).as_posix(),
            elapsed_ms=round((time.monotonic() - page_started_at) * 1000),
        )
        if oldest_date < cutoff_date:
            logger.info(
                "list.scan.cutoff.reached",
                "当前列表页已出现早于一个月边界的记录，停止翻页。",
                cutoff_date=cutoff_date,
                page_number=page_number,
                oldest_date=oldest_date,
            )
            break
        page_number += 1

    logger.info(
        "list.scan.completed",
        "近一个月通知公告扫描完成。",
        pages_fetched=pages_fetched,
        page_count=page_count,
        cutoff_date=cutoff_date,
        through_date=through_date,
        articles_selected=len(selected),
        limited_by_max_pages=limited_by_max_pages,
    )
    return selected, pages_fetched, limited_by_max_pages


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
    publisher = article.source or "北京市园林绿化局"
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
        "task_id": "beijing_landscaping_recruitment_announcements",
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
        "北京市园林绿化局招聘公告爬虫开始运行。",
        schema_version=1,
        run_id=args.run_id,
        source_url=args.url,
        cutoff_date=cutoff_date,
        through_date=through_date,
        required_title_keywords=REQUIRED_TITLE_KEYWORDS,
        output_dir=output_dir,
        max_attachment_mb=args.max_attachment_mb,
        skip_attachments=args.skip_attachments,
        max_pages=args.max_pages,
        request_delay_seconds=args.request_delay,
        timeout_seconds=args.timeout,
        retries=args.retries,
    )
    entries, pages_fetched, limited_by_max_pages = collect_entries(
        client=client,
        logger=logger,
        base_url=args.url,
        cutoff_date=cutoff_date,
        through_date=through_date,
        output_dir=output_dir,
        max_pages=args.max_pages,
    )

    records: list[dict[str, object]] = []
    errors: list[dict[str, object]] = []
    attachments_found = 0
    attachments_downloaded = 0
    attachments_skipped = 0
    attachments_failed = 0

    for index, entry in enumerate(entries, start=1):
        article_started_at = time.monotonic()
        logger.info(
            "article.fetch.start",
            "开始抓取符合条件的文章详情。",
            article_number=index,
            article_count=len(entries),
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
                article_count=len(entries),
                url=entry.url,
                final_url=response.final_url,
                http_status=response.status,
                response_bytes=len(response.body),
            )
            html = decode_html(response)
            article = parse_article_page(html, response.final_url)
            logger.info(
                "article.parse.success",
                "文章详情解析完成。",
                article_number=index,
                article_count=len(entries),
                title=article.title or entry.title,
                url=entry.url,
                published_date=article.published_date or entry.published_date,
                source=article.source or "北京市园林绿化局",
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
            article_downloaded = sum(item.get("status") == "downloaded" for item in attachments)
            article_skipped = sum(item.get("status") == "skipped" for item in attachments)
            article_failed = sum(item.get("status") == "failed" for item in attachments)
            attachments_downloaded += article_downloaded
            attachments_skipped += article_skipped
            attachments_failed += article_failed
            logger.info(
                "article.completed",
                "文章抓取与数据整理完成。",
                article_number=index,
                article_count=len(entries),
                title=record["title"],
                url=entry.url,
                status=record["status"],
                text_characters=len(article.text),
                attachments_found=len(attachments),
                attachments_downloaded=article_downloaded,
                attachments_skipped=article_skipped,
                attachments_failed=article_failed,
                warnings=record["quality"]["warnings"],
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
                "文章抓取失败。",
                article_number=index,
                article_count=len(entries),
                title=entry.title,
                url=entry.url,
                error_type=type(error).__name__,
                error=str(error),
                elapsed_ms=round((time.monotonic() - article_started_at) * 1000),
            )

    records_path = output_dir / "records.jsonl"
    errors_path = output_dir / "errors.jsonl"
    summary_path = output_dir / "summary.json"
    logger.info(
        "output.write.start",
        "开始写入抓取结果文件。",
        records_path=records_path,
        errors_path=errors_path,
        summary_path=summary_path,
        record_count=len(records),
        error_count=len(errors),
    )
    write_jsonl(records_path, records)
    write_jsonl(errors_path, errors)
    logger.info(
        "output.records.written",
        "文章记录文件已写入。",
        path=records_path,
        record_count=len(records),
        size_bytes=records_path.stat().st_size,
    )
    if errors:
        logger.warning(
            "output.errors.written",
            "错误记录文件已写入。",
            path=errors_path,
            error_count=len(errors),
            size_bytes=errors_path.stat().st_size,
        )
    else:
        logger.info("output.errors.empty", "本次运行没有产生错误记录。", path=errors_path)

    article_failures = sum(error.get("kind") == "article" for error in errors)
    status = "completed" if not errors else "completed_with_errors"
    summary = {
        "schema_version": 1,
        "run_id": args.run_id,
        "status": status,
        "source_url": args.url,
        "cutoff_date": cutoff_date.isoformat(),
        "through_date": through_date.isoformat(),
        "required_title_keywords": list(REQUIRED_TITLE_KEYWORDS),
        "pages_fetched": pages_fetched,
        "articles_selected": len(entries),
        "articles_succeeded": len(records),
        "articles_failed": article_failures,
        "attachments_found": attachments_found,
        "attachments_downloaded": attachments_downloaded,
        "attachments_skipped": attachments_skipped,
        "attachments_failed": attachments_failed,
        "max_attachment_mb": args.max_attachment_mb,
        "limited_by_max_pages": limited_by_max_pages,
        "event_log_path": event_log_path.relative_to(output_dir).as_posix(),
        "finished_at": datetime.now(BEIJING_TIMEZONE).isoformat(timespec="seconds"),
    }
    write_json(summary_path, summary)
    logger.info(
        "output.summary.written",
        "运行汇总文件已写入。",
        path=summary_path,
        size_bytes=summary_path.stat().st_size,
    )
    exit_code = 0 if not errors else 2
    finish_fields = {
        **summary,
        "exit_code": exit_code,
        "elapsed_ms": round((time.monotonic() - started_at) * 1000),
    }
    if exit_code:
        logger.warning("crawler.finish", "爬虫运行完成，但存在错误。", **finish_fields)
    else:
        logger.info("crawler.finish", "爬虫运行成功完成。", **finish_fields)
    return exit_code


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Crawl recent Beijing Landscaping Bureau notices containing 招聘 and 公告."
    )
    parser.add_argument("--url", default=DEFAULT_LIST_URL)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=0,
        help="Override the default one-calendar-month date range.",
    )
    parser.add_argument("--max-pages", type=int, default=0, help="Smoke-test page limit; 0 is unlimited.")
    parser.add_argument("--request-delay", type=float, default=0.35)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument(
        "--max-attachment-mb",
        type=int,
        default=5,
        help="Skip attachments larger than this many megabytes; default: 5.",
    )
    parser.add_argument("--skip-attachments", action="store_true")
    parser.add_argument("--log-level", choices=tuple(LOG_LEVELS), default="INFO")
    return parser


def validate_arguments(args: argparse.Namespace) -> None:
    if not is_list_url(args.url) or urlsplit(args.url).path != LIST_SECTION_PATH:
        raise ValueError(f"The list URL must be {DEFAULT_LIST_URL}")
    if args.lookback_days < 0 or args.lookback_days > 3660:
        raise ValueError("--lookback-days must be between 0 and 3660")
    if args.max_pages < 0:
        raise ValueError("--max-pages cannot be negative")
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

#!/usr/bin/env python3
"""Crawl the newest recruitment notices from Beijing Chaoyang District.

The crawler intentionally uses only the Python standard library so it can run
with the same interpreter configuration as the PA Crawler Console.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen


BEIJING_TIMEZONE = timezone(timedelta(hours=8))
SITE_HOST = "www.bjchy.gov.cn"
SECTION_PATH = "/affair/civilemployed/zhk/"
ATTACHMENT_PATH = "/userfiles/file/"
ARTICLE_NAME_PATTERN = re.compile(r"^[0-9a-f]{32}\.html$", re.IGNORECASE)
DATE_PATTERN = re.compile(r"(\d{4}-\d{2}-\d{2})")
CHARSET_PATTERN = re.compile(br"charset\s*=\s*['\"]?\s*([A-Za-z0-9._-]+)", re.IGNORECASE)
INVALID_FILENAME_PATTERN = re.compile(r"[<>:\"/\\|?*\x00-\x1f]")
DOWNLOAD_EXTENSIONS = {
    ".7z",
    ".csv",
    ".doc",
    ".docx",
    ".jpeg",
    ".jpg",
    ".pdf",
    ".png",
    ".rar",
    ".txt",
    ".xls",
    ".xlsx",
    ".zip",
}
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
BLOCK_TAGS = {"article", "br", "div", "h1", "h2", "h3", "li", "p", "section", "table", "td", "th", "tr"}
RETRYABLE_HTTP_CODES = {408, 425, 429, 500, 502, 503, 504}
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0 Safari/537.36"
)
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}
LOG_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}


@dataclass(frozen=True)
class ListEntry:
    title: str
    url: str
    published_date: date | None
    list_page_url: str
    position: int


@dataclass(frozen=True)
class AttachmentLink:
    display_name: str
    url: str
    remote_filename: str


@dataclass(frozen=True)
class ArticleData:
    title: str
    published_date: date | None
    source: str
    text: str
    attachments: list[AttachmentLink]


@dataclass(frozen=True)
class HttpResponse:
    body: bytes
    final_url: str
    status: int
    headers: dict[str, str]


class AttachmentTooLargeError(ValueError):
    def __init__(self, url: str, max_bytes: int, detected_bytes: int, size_source: str) -> None:
        self.url = url
        self.max_bytes = max_bytes
        self.detected_bytes = detected_bytes
        self.size_source = size_source
        super().__init__(
            f"Attachment exceeds {max_bytes} bytes "
            f"({detected_bytes} bytes detected via {size_source}): {url}"
        )


class _BelowErrorFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno < logging.ERROR


class _HumanLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created, BEIJING_TIMEZONE).isoformat(
            timespec="milliseconds"
        )
        event = getattr(record, "event_name", "crawler.log")
        fields = getattr(record, "event_fields", {})
        field_text = " ".join(
            f"{key}={json.dumps(_json_safe(value), ensure_ascii=False, separators=(',', ':'))}"
            for key, value in fields.items()
            if value is not None
        )
        line = f"{timestamp} {record.levelname:<7} {event} - {record.getMessage()}"
        if field_text:
            line = f"{line} | {field_text}"
        if record.exc_info:
            line = f"{line}\n{self.formatException(record.exc_info)}"
        return line


class _JsonEventFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        value: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, BEIJING_TIMEZONE).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "event": getattr(record, "event_name", "crawler.log"),
            "message": record.getMessage(),
            "fields": _json_safe(getattr(record, "event_fields", {})),
        }
        if record.exc_info:
            error_type = record.exc_info[0]
            error_value = record.exc_info[1]
            value["exception"] = {
                "type": error_type.__name__ if error_type else "Exception",
                "message": str(error_value) if error_value else "",
                "traceback": self.formatException(record.exc_info),
            }
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class CrawlerLogger:
    def __init__(self, level: str) -> None:
        self.logger = logging.getLogger(f"pa.beijing_chaoyang_recruitment.{id(self)}")
        self.logger.setLevel(LOG_LEVELS[level])
        self.logger.propagate = False
        self.handlers: list[logging.Handler] = []

        human_formatter = _HumanLogFormatter()
        stdout_handler = logging.StreamHandler(sys.stdout)
        stdout_handler.addFilter(_BelowErrorFilter())
        stdout_handler.setFormatter(human_formatter)
        self._add_handler(stdout_handler)

        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.setLevel(logging.ERROR)
        stderr_handler.setFormatter(human_formatter)
        self._add_handler(stderr_handler)

    def attach_event_log(self, target_path: Path) -> None:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(target_path, mode="w", encoding="utf-8")
        file_handler.setFormatter(_JsonEventFormatter())
        self._add_handler(file_handler)

    def debug(self, event: str, message: str, **fields: object) -> None:
        self._log(logging.DEBUG, event, message, fields)

    def info(self, event: str, message: str, **fields: object) -> None:
        self._log(logging.INFO, event, message, fields)

    def warning(self, event: str, message: str, **fields: object) -> None:
        self._log(logging.WARNING, event, message, fields)

    def error(self, event: str, message: str, **fields: object) -> None:
        self._log(logging.ERROR, event, message, fields)

    def exception(self, event: str, message: str, **fields: object) -> None:
        self.logger.error(
            message,
            extra={"event_name": event, "event_fields": fields},
            exc_info=True,
        )

    def close(self) -> None:
        for handler in self.handlers:
            handler.flush()
            handler.close()
            self.logger.removeHandler(handler)
        self.handlers.clear()

    def _add_handler(self, handler: logging.Handler) -> None:
        self.logger.addHandler(handler)
        self.handlers.append(handler)

    def _log(self, level: int, event: str, message: str, fields: dict[str, object]) -> None:
        self.logger.log(level, message, extra={"event_name": event, "event_fields": fields})


def _json_safe(value: object) -> object:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, Iterable):
        return [_json_safe(item) for item in value]
    return str(value)


class ListPageParser(HTMLParser):
    def __init__(self, page_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.page_url = page_url
        self.entries: list[tuple[str, str, date | None]] = []
        self.found_list = False
        self._list_depth = 0
        self._in_item = False
        self._in_date = False
        self._in_link = False
        self._date_text: list[str] = []
        self._title_text: list[str] = []
        self._href = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attributes = {key.lower(): (value or "") for key, value in attrs}
        classes = set(attributes.get("class", "").split())

        if not self._list_depth and tag == "ul" and "news_list" in classes:
            self.found_list = True
            self._list_depth = 1
            return
        if not self._list_depth:
            return

        if tag == "li":
            self._in_item = True
            self._in_date = False
            self._in_link = False
            self._date_text = []
            self._title_text = []
            self._href = ""
        elif self._in_item and tag == "span":
            self._in_date = True
        elif self._in_item and tag == "a" and "news" in classes:
            self._in_link = True
            self._href = attributes.get("href", "").strip()

        if tag not in VOID_TAGS:
            self._list_depth += 1

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if not self._list_depth:
            return
        if tag == "a":
            self._in_link = False
        elif tag == "span":
            self._in_date = False
        elif tag == "li" and self._in_item:
            self._finish_item()
            self._in_item = False
        if tag not in VOID_TAGS:
            self._list_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._in_date:
            self._date_text.append(data)
        if self._in_link:
            self._title_text.append(data)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def _finish_item(self) -> None:
        title = normalize_inline_text("".join(self._title_text))
        absolute_url = without_fragment(urljoin(self.page_url, self._href.strip()))
        if not title or not is_article_url(absolute_url):
            return
        match = DATE_PATTERN.search("".join(self._date_text))
        published_date = parse_iso_date(match.group(1)) if match else None
        self.entries.append((title, absolute_url, published_date))


class ArticlePageParser(HTMLParser):
    def __init__(self, article_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.article_url = article_url
        self.meta_title = ""
        self.meta_date = ""
        self.meta_source = ""
        self.title_text: list[str] = []
        self.subtitle_text: list[str] = []
        self.body_parts: list[str] = []
        self.attachments: list[AttachmentLink] = []
        self.found_content = False
        self._title_depth = 0
        self._subtitle_depth = 0
        self._content_depth = 0
        self._ignore_depth = 0
        self._attachment_url = ""
        self._attachment_text: list[str] = []
        self._seen_attachment_urls: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attributes = {key.lower(): (value or "") for key, value in attrs}
        classes = set(attributes.get("class", "").split())

        if tag == "meta":
            name = attributes.get("name", "").lower()
            if name == "articletitle":
                self.meta_title = normalize_inline_text(attributes.get("content", ""))
            elif name == "pubdate":
                self.meta_date = normalize_inline_text(attributes.get("content", ""))
            elif name == "contentsource":
                self.meta_source = normalize_inline_text(attributes.get("content", ""))

        if self._content_depth:
            if self._ignore_depth:
                if tag not in VOID_TAGS:
                    self._ignore_depth += 1
            elif tag in {"script", "style"}:
                self._ignore_depth = 1
            else:
                if tag in BLOCK_TAGS:
                    self.body_parts.append("\n")
                if tag == "a":
                    candidate = without_fragment(urljoin(self.article_url, attributes.get("href", "").strip()))
                    if is_attachment_url(candidate):
                        self._attachment_url = candidate
                        self._attachment_text = []
            if tag not in VOID_TAGS:
                self._content_depth += 1
            return

        if tag == "div" and "content_article" in classes:
            self.found_content = True
            self._content_depth = 1
            return

        if self._title_depth:
            if tag not in VOID_TAGS:
                self._title_depth += 1
        elif tag == "h1" and "content_title" in classes:
            self._title_depth = 1

        if self._subtitle_depth:
            if tag not in VOID_TAGS:
                self._subtitle_depth += 1
        elif tag == "div" and "sub_title2_left" in classes:
            self._subtitle_depth = 1

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._content_depth:
            if self._ignore_depth:
                if tag not in VOID_TAGS:
                    self._ignore_depth -= 1
            else:
                if tag == "a" and self._attachment_url:
                    self._finish_attachment()
                if tag in BLOCK_TAGS:
                    self.body_parts.append("\n")
            if tag not in VOID_TAGS:
                self._content_depth -= 1
            return

        if self._title_depth and tag not in VOID_TAGS:
            self._title_depth -= 1
        if self._subtitle_depth and tag not in VOID_TAGS:
            self._subtitle_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._content_depth and not self._ignore_depth:
            self.body_parts.append(data)
            if self._attachment_url:
                self._attachment_text.append(data)
        elif self._title_depth:
            self.title_text.append(data)
        elif self._subtitle_depth:
            self.subtitle_text.append(data)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def result(self) -> ArticleData:
        subtitle = normalize_inline_text("".join(self.subtitle_text))
        date_match = DATE_PATTERN.search(self.meta_date or subtitle)
        source = self.meta_source
        if not source:
            source_match = re.search(r"来源[：:]\s*(.*?)(?:打印页面|关闭页面|$)", subtitle)
            if source_match:
                source = normalize_inline_text(source_match.group(1))
        return ArticleData(
            title=self.meta_title or normalize_inline_text("".join(self.title_text)),
            published_date=parse_iso_date(date_match.group(1)) if date_match else None,
            source=source,
            text=normalize_multiline_text("".join(self.body_parts)),
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
        display_name = re.sub(r"^附件\s*[：:]?\s*", "", display_name)
        self.attachments.append(
            AttachmentLink(
                display_name=display_name,
                url=url,
                remote_filename=remote_filename,
            )
        )


class HttpClient:
    def __init__(self, timeout: int, retries: int, request_delay: float, logger: CrawlerLogger) -> None:
        self.timeout = timeout
        self.retries = retries
        self.request_delay = max(0.0, request_delay)
        self.logger = logger
        self._last_request_at = 0.0

    def get(self, url: str, referer: str | None = None) -> HttpResponse:
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            self._throttle()
            started_at = time.monotonic()
            self.logger.debug(
                "http.request.start",
                "发起 HTTP 请求。",
                url=url,
                referer=referer,
                attempt=attempt + 1,
                max_attempts=self.retries + 1,
            )
            try:
                with urlopen(self._request(url, referer), timeout=self.timeout) as response:
                    body = response.read(20 * 1024 * 1024 + 1)
                    if len(body) > 20 * 1024 * 1024:
                        raise ValueError(f"HTML response is too large: {url}")
                    result = HttpResponse(
                        body=body,
                        final_url=response.geturl(),
                        status=getattr(response, "status", 200),
                        headers={key.lower(): value for key, value in response.headers.items()},
                    )
                    self.logger.debug(
                        "http.request.success",
                        "HTTP 请求成功。",
                        url=url,
                        final_url=result.final_url,
                        http_status=result.status,
                        response_bytes=len(body),
                        elapsed_ms=round((time.monotonic() - started_at) * 1000),
                        attempt=attempt + 1,
                    )
                    return result
            except HTTPError as error:
                last_error = error
                if error.code not in RETRYABLE_HTTP_CODES or attempt >= self.retries:
                    self.logger.error(
                        "http.request.failed",
                        "HTTP 请求失败。",
                        url=url,
                        http_status=error.code,
                        reason=str(error.reason),
                        attempt=attempt + 1,
                    )
                    raise
                self._sleep_before_retry(attempt, url, error)
            except (URLError, TimeoutError, OSError) as error:
                last_error = error
                if attempt >= self.retries:
                    self.logger.error(
                        "http.request.failed",
                        "网络请求失败。",
                        url=url,
                        error_type=type(error).__name__,
                        error=str(error),
                        attempt=attempt + 1,
                    )
                    raise
                self._sleep_before_retry(attempt, url, error)
        raise RuntimeError(f"Request failed: {url}") from last_error

    def download(self, url: str, target_path: Path, max_bytes: int, referer: str) -> dict[str, object]:
        temporary_path = target_path.with_name(f"{target_path.name}.part")
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            self._throttle()
            started_at = time.monotonic()
            try:
                with urlopen(self._request(url, referer), timeout=self.timeout) as response:
                    final_url = without_fragment(response.geturl())
                    if not is_attachment_url(final_url):
                        raise ValueError(f"Attachment redirected outside the allowed path: {final_url}")
                    content_length = response.headers.get("Content-Length")
                    reported_size: int | None = None
                    if content_length:
                        try:
                            reported_size = int(content_length)
                        except ValueError:
                            self.logger.warning(
                                "attachment.content_length.invalid",
                                "附件 Content-Length 无效，将按实际读取大小检查。",
                                url=url,
                                content_length=content_length,
                            )
                    if reported_size is not None and reported_size > max_bytes:
                        raise AttachmentTooLargeError(url, max_bytes, reported_size, "content_length")

                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    digest = hashlib.sha256()
                    downloaded = 0
                    signature = bytearray()
                    content_type = response.headers.get("Content-Type", "")
                    with temporary_path.open("wb") as output:
                        while True:
                            chunk = response.read(128 * 1024)
                            if not chunk:
                                break
                            downloaded += len(chunk)
                            if downloaded > max_bytes:
                                raise AttachmentTooLargeError(url, max_bytes, downloaded, "stream")
                            if len(signature) < 64:
                                signature.extend(chunk[: 64 - len(signature)])
                            digest.update(chunk)
                            output.write(chunk)
                    validate_attachment_payload(target_path.name, content_type, downloaded, bytes(signature))
                    os.replace(temporary_path, target_path)
                    return {
                        "status": "downloaded",
                        "final_url": final_url,
                        "content_type": content_type,
                        "size_bytes": downloaded,
                        "sha256": digest.hexdigest(),
                        "elapsed_ms": round((time.monotonic() - started_at) * 1000),
                    }
            except AttachmentTooLargeError:
                temporary_path.unlink(missing_ok=True)
                raise
            except HTTPError as error:
                last_error = error
                temporary_path.unlink(missing_ok=True)
                if error.code not in RETRYABLE_HTTP_CODES or attempt >= self.retries:
                    raise
                self._sleep_before_retry(attempt, url, error)
            except (URLError, TimeoutError, OSError) as error:
                last_error = error
                temporary_path.unlink(missing_ok=True)
                if attempt >= self.retries:
                    raise
                self._sleep_before_retry(attempt, url, error)
            except ValueError:
                temporary_path.unlink(missing_ok=True)
                raise
        raise RuntimeError(f"Download failed: {url}") from last_error

    def _request(self, url: str, referer: str | None) -> Request:
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/pdf,application/octet-stream;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "User-Agent": USER_AGENT,
        }
        if referer:
            headers["Referer"] = referer
        return Request(url, headers=headers)

    def _throttle(self) -> None:
        remaining = self.request_delay - (time.monotonic() - self._last_request_at)
        if remaining > 0:
            time.sleep(remaining)
        self._last_request_at = time.monotonic()

    def _sleep_before_retry(self, attempt: int, url: str, error: Exception) -> None:
        delay_seconds = min(2**attempt, 8)
        self.logger.warning(
            "http.retry.scheduled",
            "请求失败，将在退避后重试。",
            url=url,
            error_type=type(error).__name__,
            error=str(error),
            failed_attempt=attempt + 1,
            next_attempt=attempt + 2,
            delay_seconds=delay_seconds,
        )
        time.sleep(delay_seconds)


def decode_html(response: HttpResponse) -> str:
    candidates: list[str] = []
    content_type = response.headers.get("content-type", "")
    header_match = re.search(r"charset\s*=\s*([^;\s]+)", content_type, re.IGNORECASE)
    if header_match:
        candidates.append(header_match.group(1).strip("'\""))
    meta_match = CHARSET_PATTERN.search(response.body[:4096])
    if meta_match:
        candidates.append(meta_match.group(1).decode("ascii", "ignore"))
    candidates.extend(["gb18030", "utf-8"])

    normalized: list[str] = []
    for candidate in candidates:
        encoding = "gb18030" if candidate.lower().replace("-", "") in {"gb2312", "gbk", "gb18030"} else candidate
        if encoding.lower() in {item.lower() for item in normalized}:
            continue
        normalized.append(encoding)
        try:
            return response.body.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return response.body.decode("gb18030", errors="replace")


def parse_list_page(html: str, page_url: str) -> tuple[list[tuple[str, str, date | None]], bool]:
    parser = ListPageParser(page_url)
    parser.feed(html)
    parser.close()
    return parser.entries, parser.found_list


def parse_article_page(html: str, article_url: str) -> tuple[ArticleData, bool]:
    parser = ArticlePageParser(article_url)
    parser.feed(html)
    parser.close()
    return parser.result(), parser.found_content


def collect_entries(
    client: HttpClient,
    logger: CrawlerLogger,
    base_url: str,
    limit: int,
    max_pages: int,
) -> tuple[list[ListEntry], int]:
    selected: list[ListEntry] = []
    seen_urls: set[str] = set()
    pages_fetched = 0
    for page_index in range(max_pages):
        page_url = list_page_url(base_url, page_index)
        logger.info(
            "list.fetch.start",
            "开始抓取招聘列表页。",
            page_number=page_index + 1,
            url=page_url,
            selected_count=len(selected),
            target_count=limit,
        )
        response = client.get(page_url, referer=base_url if page_index else None)
        if not is_list_url(response.final_url):
            raise ValueError(f"List page redirected outside the allowed section: {response.final_url}")
        html = decode_html(response)
        items, found_list = parse_list_page(html, response.final_url)
        if not found_list:
            raise ValueError(f"List container ul.news_list was not found: {response.final_url}")
        pages_fetched += 1
        logger.info(
            "list.fetch.success",
            "招聘列表页抓取并解析成功。",
            page_number=page_index + 1,
            url=response.final_url,
            http_status=response.status,
            response_bytes=len(response.body),
            entries_found=len(items),
        )
        if not items:
            break
        for title, article_url, published_date in items:
            if article_url in seen_urls:
                continue
            seen_urls.add(article_url)
            entry = ListEntry(
                title=title,
                url=article_url,
                published_date=published_date,
                list_page_url=response.final_url,
                position=len(selected) + 1,
            )
            selected.append(entry)
            logger.info(
                "list.article.selected",
                "选中最近文章。",
                position=entry.position,
                target_count=limit,
                title=entry.title,
                url=entry.url,
                published_date=entry.published_date,
            )
            if len(selected) >= limit:
                break
        if len(selected) >= limit:
            break

    if not selected:
        raise ValueError("No article links were found in the recruitment list")
    if len(selected) < limit:
        logger.warning(
            "list.selection.shortfall",
            "列表中的可用文章少于请求数量。",
            requested=limit,
            selected=len(selected),
            pages_fetched=pages_fetched,
        )
    logger.info(
        "list.scan.completed",
        "最近文章选择完成。",
        requested=limit,
        selected=len(selected),
        pages_fetched=pages_fetched,
    )
    return selected, pages_fetched


def download_attachments(
    client: HttpClient,
    logger: CrawlerLogger,
    links: Iterable[AttachmentLink],
    output_dir: Path,
    article_id: str,
    article_url: str,
    max_attachment_bytes: int,
    errors: list[dict[str, object]],
) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    links = list(links)
    attachment_dir = output_dir / "attachments" / article_id
    for index, link in enumerate(links, start=1):
        suffix = Path(urlsplit(link.url).path).suffix.lower()
        target = unique_target(attachment_dir, safe_filename(link.display_name, suffix))
        local_path = target.relative_to(output_dir).as_posix()
        metadata: dict[str, object] = {
            "display_name": link.display_name,
            "url": link.url,
            "source_url": link.url,
            "remote_filename": link.remote_filename,
            "filename": target.name,
            "local_path": local_path,
        }
        logger.info(
            "attachment.download.start",
            "开始下载文章附件。",
            article_url=article_url,
            attachment_number=index,
            attachment_count=len(links),
            display_name=link.display_name,
            url=link.url,
            local_path=local_path,
            max_size_bytes=max_attachment_bytes,
        )
        started_at = time.monotonic()
        try:
            metadata.update(client.download(link.url, target, max_attachment_bytes, article_url))
            logger.info(
                "attachment.download.success",
                "文章附件下载成功。",
                article_url=article_url,
                attachment_number=index,
                attachment_count=len(links),
                display_name=link.display_name,
                url=link.url,
                final_url=metadata.get("final_url"),
                local_path=local_path,
                size_bytes=metadata.get("size_bytes"),
                sha256=metadata.get("sha256"),
                elapsed_ms=round((time.monotonic() - started_at) * 1000),
            )
        except AttachmentTooLargeError as error:
            metadata.update(
                {
                    "status": "skipped",
                    "skip_reason": "size_limit",
                    "local_path": None,
                    "detected_size_bytes": error.detected_bytes,
                    "max_size_bytes": error.max_bytes,
                    "size_source": error.size_source,
                }
            )
            logger.warning(
                "attachment.download.skipped.size_limit",
                "附件超过 5 MiB 限制，已跳过下载。",
                article_url=article_url,
                display_name=link.display_name,
                url=link.url,
                detected_size_bytes=error.detected_bytes,
                max_size_bytes=error.max_bytes,
                size_source=error.size_source,
            )
        except Exception as error:
            metadata.update({"status": "failed", "local_path": None, "error": str(error)})
            errors.append(
                {
                    "kind": "attachment",
                    "article_url": article_url,
                    "url": link.url,
                    "display_name": link.display_name,
                    "error_type": type(error).__name__,
                    "message": str(error),
                }
            )
            logger.exception(
                "attachment.download.failed",
                "文章附件下载失败，继续处理后续内容。",
                article_url=article_url,
                attachment_number=index,
                attachment_count=len(links),
                display_name=link.display_name,
                url=link.url,
                error_type=type(error).__name__,
                error=str(error),
            )
        results.append(metadata)
    logger.info(
        "attachment.batch.completed",
        "文章附件处理完成。",
        article_url=article_url,
        attachments_found=len(links),
        attachments_downloaded=sum(item.get("status") == "downloaded" for item in results),
        attachments_skipped=sum(item.get("status") == "skipped" for item in results),
        attachments_failed=sum(item.get("status") == "failed" for item in results),
    )
    return results


def describe_skipped_attachments(links: Iterable[AttachmentLink]) -> list[dict[str, object]]:
    return [
        {
            "display_name": link.display_name,
            "url": link.url,
            "source_url": link.url,
            "remote_filename": link.remote_filename,
            "filename": safe_filename(link.display_name, Path(urlsplit(link.url).path).suffix.lower()),
            "local_path": None,
            "status": "skipped",
            "skip_reason": "disabled_by_argument",
        }
        for link in links
    ]


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
    publisher = article.source or "北京市朝阳区人民政府"
    if not article.text:
        warnings.append("article_body_empty")
    if article.published_date and entry.published_date and article.published_date != entry.published_date:
        warnings.append("list_and_article_date_mismatch")
    if any(item.get("status") == "failed" for item in attachments):
        warnings.append("attachment_download_failed")
    return {
        "schema_version": "1.0",
        "record_id": f"sha256:{hashlib.sha256(entry.url.encode('utf-8')).hexdigest()}",
        "run_id": run_id,
        "task_id": "beijing_chaoyang_recruitment",
        "status": "ok" if not warnings else "completed_with_warnings",
        "source_url": entry.url,
        "published_date": published_date.isoformat() if published_date else None,
        "publisher": publisher,
        "content_text": article.text,
        "source": {
            "url": entry.url,
            "list_page_url": entry.list_page_url,
            "list_position": entry.position,
            "published_at": published_date.isoformat() if published_date else None,
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
    article_dir = output_dir / "articles"
    article_dir.mkdir(parents=True, exist_ok=True)
    event_log_path = output_dir / "crawler-events.jsonl"
    logger.attach_event_log(event_log_path)

    max_attachment_bytes = int(args.max_attachment_mb * 1024 * 1024)
    logger.info(
        "crawler.start",
        "北京市朝阳区招聘信息爬虫启动。",
        run_id=args.run_id,
        source_url=args.url,
        output_dir=output_dir,
        recent_article_limit=args.limit,
        max_attachment_bytes=max_attachment_bytes,
        request_delay_seconds=args.request_delay,
        timeout_seconds=args.timeout,
        retries=args.retries,
        skip_attachments=args.skip_attachments,
    )

    client = HttpClient(args.timeout, args.retries, args.request_delay, logger)
    records: list[dict[str, object]] = []
    errors: list[dict[str, object]] = []
    pages_fetched = 0
    selected: list[ListEntry] = []

    try:
        selected, pages_fetched = collect_entries(client, logger, args.url, args.limit, args.max_pages)
    except Exception as error:
        errors.append(
            {
                "kind": "list",
                "url": args.url,
                "error_type": type(error).__name__,
                "message": str(error),
            }
        )
        logger.exception(
            "list.scan.failed",
            "招聘列表抓取失败，无法继续。",
            url=args.url,
            error_type=type(error).__name__,
            error=str(error),
        )

    for entry in selected:
        article_started_at = time.monotonic()
        logger.info(
            "article.fetch.start",
            "开始抓取文章详情。",
            article_number=entry.position,
            article_count=len(selected),
            title=entry.title,
            url=entry.url,
            list_published_date=entry.published_date,
        )
        try:
            response = client.get(entry.url, referer=entry.list_page_url)
            if not is_article_url(response.final_url):
                raise ValueError(f"Article redirected outside the allowed section: {response.final_url}")
            logger.info(
                "article.fetch.success",
                "文章详情页请求成功。",
                article_number=entry.position,
                article_count=len(selected),
                url=entry.url,
                final_url=response.final_url,
                http_status=response.status,
                response_bytes=len(response.body),
            )

            article_id = article_identifier(entry.url)
            raw_html_path = article_dir / f"{article_id}.html"
            raw_html_path.write_bytes(response.body)
            logger.info(
                "article.raw_html.saved",
                "文章原始 HTML 已保存。",
                article_url=entry.url,
                raw_html_path=raw_html_path.relative_to(output_dir).as_posix(),
                size_bytes=len(response.body),
            )

            article, found_content = parse_article_page(decode_html(response), response.final_url)
            if not found_content:
                raise ValueError("Article container div.content_article was not found")
            logger.info(
                "article.parse.success",
                "文章详情解析完成。",
                article_number=entry.position,
                article_count=len(selected),
                title=article.title or entry.title,
                url=entry.url,
                published_date=article.published_date or entry.published_date,
                source=article.source,
                text_characters=len(article.text),
                attachments_found=len(article.attachments),
            )
            for index, link in enumerate(article.attachments, start=1):
                logger.info(
                    "attachment.detected",
                    "检测到文章附件。",
                    article_url=entry.url,
                    attachment_number=index,
                    attachment_count=len(article.attachments),
                    display_name=link.display_name,
                    remote_filename=link.remote_filename,
                    url=link.url,
                )

            if args.skip_attachments:
                attachments = describe_skipped_attachments(article.attachments)
                logger.info(
                    "attachment.batch.skipped",
                    "根据运行参数跳过所有附件下载。",
                    article_url=entry.url,
                    attachments_found=len(article.attachments),
                )
            else:
                attachments = download_attachments(
                    client,
                    logger,
                    article.attachments,
                    output_dir,
                    article_id,
                    entry.url,
                    max_attachment_bytes,
                    errors,
                )

            record = build_record(
                entry,
                article,
                attachments,
                raw_html_path,
                output_dir,
                args.run_id,
                datetime.now(BEIJING_TIMEZONE).isoformat(timespec="seconds"),
            )
            records.append(record)
            logger.info(
                "article.completed",
                "文章抓取与数据整理完成。",
                article_number=entry.position,
                article_count=len(selected),
                title=record["title"],
                url=entry.url,
                status=record["status"],
                text_characters=len(article.text),
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
                    "position": entry.position,
                    "title": entry.title,
                    "url": entry.url,
                    "published_date": entry.published_date.isoformat() if entry.published_date else None,
                    "error_type": type(error).__name__,
                    "message": str(error),
                }
            )
            logger.exception(
                "article.failed",
                "文章抓取失败，已记录错误并继续下一篇。",
                article_number=entry.position,
                article_count=len(selected),
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

    attachments = [item for record in records for item in record.get("attachments", [])]
    status = "failed" if not records else ("completed_with_errors" if errors else "completed")
    summary = {
        "schema_version": 1,
        "run_id": args.run_id,
        "status": status,
        "source_url": args.url,
        "recent_article_limit": args.limit,
        "pages_fetched": pages_fetched,
        "articles_selected": len(selected),
        "articles_succeeded": len(records),
        "articles_failed": sum(error.get("kind") == "article" for error in errors),
        "attachments_found": len(attachments),
        "attachments_downloaded": sum(item.get("status") == "downloaded" for item in attachments),
        "attachments_skipped": sum(item.get("status") == "skipped" for item in attachments),
        "attachments_failed": sum(item.get("status") == "failed" for item in attachments),
        "error_count": len(errors),
        "max_attachment_bytes": max_attachment_bytes,
        "event_log_path": event_log_path.name,
        "records_path": records_path.name,
        "errors_path": errors_path.name,
        "finished_at": datetime.now(BEIJING_TIMEZONE).isoformat(timespec="seconds"),
        "elapsed_ms": round((time.monotonic() - started_at) * 1000),
    }
    write_json(summary_path, summary)
    logger.info(
        "output.write.completed",
        "抓取结果文件写入完成。",
        records_path=records_path,
        errors_path=errors_path,
        summary_path=summary_path,
        record_count=len(records),
        error_count=len(errors),
    )
    logger.info(
        "crawler.finish",
        "北京市朝阳区招聘信息爬虫运行结束。",
        **summary,
        exit_code=0 if records else 1,
    )
    return 0 if records else 1


def validate_attachment_payload(filename: str, content_type: str, size: int, signature: bytes) -> None:
    if size <= 0:
        raise ValueError(f"Attachment is empty: {filename}")
    media_type = content_type.split(";", 1)[0].strip().lower()
    html_prefix = signature.lstrip().lower()
    if media_type in {"text/html", "application/xhtml+xml"} or html_prefix.startswith(
        (b"<!doctype html", b"<html", b"<head", b"<body")
    ):
        raise ValueError(f"Attachment response contains HTML instead of a file: {filename}")
    zip_signatures = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
    ole_signature = (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",)
    expected: dict[str, tuple[bytes, ...]] = {
        ".7z": (b"7z\xbc\xaf\x27\x1c",),
        ".doc": ole_signature,
        ".docx": zip_signatures,
        ".jpeg": (b"\xff\xd8\xff",),
        ".jpg": (b"\xff\xd8\xff",),
        ".pdf": (b"%PDF-",),
        ".png": (b"\x89PNG\r\n\x1a\n",),
        ".rar": (b"Rar!\x1a\x07",),
        ".xls": ole_signature,
        ".xlsx": zip_signatures,
        ".zip": zip_signatures,
    }
    suffix = Path(filename).suffix.lower()
    signatures = expected.get(suffix)
    if signatures and not signature.startswith(signatures):
        raise ValueError(f"Attachment content does not match the {suffix} file signature: {filename}")


def list_page_url(base_url: str, page_index: int) -> str:
    base_url = base_url if base_url.endswith("/") else f"{base_url}/"
    return base_url if page_index == 0 else urljoin(base_url, f"index_{page_index}.html")


def is_list_url(url: str) -> bool:
    parsed = urlsplit(url)
    path = parsed.path.lower()
    return (
        parsed.scheme == "http"
        and parsed.hostname == SITE_HOST
        and not parsed.query
        and not parsed.fragment
        and (path == SECTION_PATH or bool(re.fullmatch(rf"{re.escape(SECTION_PATH)}index(?:_\d+)?\.html", path)))
    )


def is_article_url(url: str) -> bool:
    parsed = urlsplit(url)
    return (
        parsed.scheme == "http"
        and parsed.hostname == SITE_HOST
        and not parsed.query
        and not parsed.fragment
        and parsed.path.lower().startswith(SECTION_PATH)
        and bool(ARTICLE_NAME_PATTERN.fullmatch(Path(parsed.path).name))
    )


def is_attachment_url(url: str) -> bool:
    parsed = urlsplit(url)
    return (
        parsed.scheme == "http"
        and parsed.hostname == SITE_HOST
        and not parsed.fragment
        and parsed.path.lower().startswith(ATTACHMENT_PATH)
        and Path(parsed.path).suffix.lower() in DOWNLOAD_EXTENSIONS
    )


def without_fragment(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))


def parse_iso_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def normalize_inline_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def normalize_multiline_text(value: str) -> str:
    lines = [normalize_inline_text(line) for line in value.replace("\r", "\n").split("\n")]
    return "\n".join(line for line in lines if line)


def safe_filename(value: str, suffix: str = "") -> str:
    suffix = suffix.lower()
    name = INVALID_FILENAME_PATTERN.sub("_", normalize_inline_text(value)).strip(" .")
    if not name:
        name = "attachment"
    if Path(name).stem.upper() in WINDOWS_RESERVED_NAMES:
        name = f"_{name}"
    if suffix and not name.lower().endswith(suffix):
        name = f"{name}{suffix}"
    if len(name) > 180:
        current_suffix = Path(name).suffix
        name = f"{Path(name).stem[: 180 - len(current_suffix)]}{current_suffix}"
    return name


def unique_target(directory: Path, filename: str) -> Path:
    target = directory / filename
    counter = 2
    while target.exists():
        target = directory / f"{Path(filename).stem}_{counter}{Path(filename).suffix}"
        counter += 1
    return target


def article_identifier(url: str) -> str:
    stem = Path(urlsplit(url).path).stem
    return stem if re.fullmatch(r"[0-9a-f]{32}", stem, re.IGNORECASE) else hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]


def write_json(path: Path, value: object) -> None:
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def write_jsonl(path: Path, values: Iterable[Mapping[str, object]]) -> None:
    temporary = path.with_name(f"{path.name}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as output:
        for value in values:
            output.write(json.dumps(value, ensure_ascii=False) + "\n")
    os.replace(temporary, path)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Crawl the newest Beijing Chaoyang recruitment notices")
    parser.add_argument("--url", required=True, help="Recruitment list URL")
    parser.add_argument("--output-dir", required=True, help="Runner-created output directory")
    parser.add_argument("--run-id", required=True, help="Run identifier from the PA console")
    parser.add_argument("--limit", type=int, default=10, help="Newest list entries to process (default: 10)")
    parser.add_argument("--max-pages", type=int, default=5, help="Maximum list pages to scan")
    parser.add_argument("--max-attachment-mb", type=float, default=5.0, help="Per-attachment size limit")
    parser.add_argument("--request-delay", type=float, default=0.35, help="Minimum delay between requests")
    parser.add_argument("--timeout", type=int, default=60, help="Per-request timeout in seconds")
    parser.add_argument("--retries", type=int, default=3, help="Retries for transient network failures")
    parser.add_argument("--skip-attachments", action="store_true", help="Collect metadata without downloading attachments")
    parser.add_argument("--log-level", choices=tuple(LOG_LEVELS), default="INFO")
    return parser


def validate_arguments(args: argparse.Namespace) -> None:
    if not is_list_url(args.url):
        raise ValueError(f"Unsupported list URL: {args.url}")
    if args.limit <= 0:
        raise ValueError("--limit must be greater than zero")
    if args.max_pages <= 0:
        raise ValueError("--max-pages must be greater than zero")
    if args.max_attachment_mb <= 0:
        raise ValueError("--max-attachment-mb must be greater than zero")
    if args.request_delay < 0:
        raise ValueError("--request-delay cannot be negative")
    if args.timeout <= 0:
        raise ValueError("--timeout must be greater than zero")
    if args.retries < 0:
        raise ValueError("--retries cannot be negative")


def configure_console_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    configure_console_encoding()
    parser = build_argument_parser()
    args = parser.parse_args()
    logger = CrawlerLogger(args.log_level)
    try:
        validate_arguments(args)
        return run(args, logger)
    except Exception as error:
        logger.exception(
            "crawler.fatal",
            "爬虫发生未处理异常。",
            error_type=type(error).__name__,
            error=str(error),
        )
        return 1
    finally:
        logger.close()


if __name__ == "__main__":
    raise SystemExit(main())

"""Crawl recent Beijing public recruitment articles and their attachments.

The crawler follows the PA console protocol and writes directly into the
``--output-dir`` allocated by the runner. It intentionally uses only the
Python standard library so the task can run on a clean local Python install.
"""

from __future__ import annotations

import argparse
import calendar
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


DEFAULT_LIST_URL = "https://rsj.beijing.gov.cn/xxgk/gkzp/"
BEIJING_TIMEZONE = timezone(timedelta(hours=8))
USER_AGENT = "PA-Crawler-Console/0.1 (+local research crawler)"
ARTICLE_PATH_PATTERN = re.compile(r"^/xxgk/gkzp/\d{6}/t\d{8}_\d+\.html$", re.IGNORECASE)
DATE_PATTERN = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")
PAGE_COUNT_PATTERN = re.compile(r"var\s+countPage\s*=\s*(\d+)", re.IGNORECASE)
LIST_PAGE_PATH_PATTERN = re.compile(r"^/xxgk/gkzp/(?:index_\d+\.html)?$", re.IGNORECASE)
DOWNLOAD_EXTENSIONS = {
    ".7z",
    ".csv",
    ".doc",
    ".docx",
    ".et",
    ".jpg",
    ".jpeg",
    ".pdf",
    ".ppt",
    ".pptx",
    ".png",
    ".rar",
    ".rtf",
    ".tar",
    ".txt",
    ".wps",
    ".xls",
    ".xlsx",
    ".zip",
}
BLOCK_TAGS = {
    "article",
    "blockquote",
    "div",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "li",
    "p",
    "section",
    "table",
    "tr",
}
RETRYABLE_HTTP_CODES = {408, 425, 429, 500, 502, 503, 504}
VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}
IGNORED_BODY_TAGS = {"script", "style", "noscript", "template"}
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
    published_date: date
    list_page_url: str


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
    def __init__(
        self,
        url: str,
        max_bytes: int,
        detected_bytes: int,
        size_source: str,
    ) -> None:
        self.url = url
        self.max_bytes = max_bytes
        self.detected_bytes = detected_bytes
        self.size_source = size_source
        super().__init__(
            f"Attachment exceeds the configured {max_bytes}-byte limit "
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
        event_record: dict[str, object] = {
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
            event_record["exception"] = {
                "type": error_type.__name__ if error_type else "Exception",
                "message": str(error_value) if error_value else "",
                "traceback": self.formatException(record.exc_info),
            }
        return json.dumps(event_record, ensure_ascii=False, separators=(",", ":"))


class CrawlerLogger:
    def __init__(self, level: str) -> None:
        self.logger = logging.getLogger(f"pa.beijing_hr_recruitment.{id(self)}")
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
        self.logger.log(
            level,
            message,
            extra={"event_name": event, "event_fields": fields},
        )


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
        self.entries: list[ListEntry] = []
        self.list_container_count = 0
        self.article_candidate_count = 0
        self.invalid_candidate_count = 0
        self._list_depth = 0
        self._li_depth = 0
        self._current_href = ""
        self._current_title_attr = ""
        self._anchor_depth = 0
        self._anchor_text: list[str] = []
        self._span_depth = 0
        self._span_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.lower(): value or "" for key, value in attrs}
        tag = tag.lower()
        if tag == "ul":
            classes = {name.lower() for name in attributes.get("class", "").split()}
            if self._list_depth > 0:
                self._list_depth += 1
            elif "list" in classes:
                self._list_depth = 1
                self.list_container_count += 1
            return
        if self._list_depth == 0:
            return
        if tag == "li":
            if self._li_depth == 0:
                self._reset_current()
            self._li_depth += 1
            return
        if self._li_depth == 0:
            return
        if tag == "a" and not self._current_href:
            href = attributes.get("href", "").strip()
            absolute_url = without_fragment(urljoin(self.page_url, href))
            if is_article_url(absolute_url):
                self.article_candidate_count += 1
                self._current_href = absolute_url
                self._current_title_attr = attributes.get("title", "").strip()
                self._anchor_depth = 1
                self._anchor_text = []
        elif self._anchor_depth > 0 and tag not in VOID_TAGS:
            self._anchor_depth += 1
        if tag == "span" and self._span_depth == 0:
            self._span_depth = 1
            self._span_text = []
        elif self._span_depth > 0 and tag not in VOID_TAGS:
            self._span_depth += 1

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._anchor_depth > 0:
            self._anchor_depth -= 1
        if self._span_depth > 0:
            self._span_depth -= 1
        if tag == "li" and self._li_depth > 0:
            self._li_depth -= 1
            if self._li_depth == 0:
                self._finish_current()
        if tag == "ul" and self._list_depth > 0:
            self._list_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._anchor_depth > 0:
            self._anchor_text.append(data)
        if self._span_depth > 0:
            self._span_text.append(data)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in VOID_TAGS:
            self.handle_endtag(tag)

    def _reset_current(self) -> None:
        self._current_href = ""
        self._current_title_attr = ""
        self._anchor_depth = 0
        self._anchor_text = []
        self._span_depth = 0
        self._span_text = []

    def _finish_current(self) -> None:
        if not self._current_href:
            return
        date_match = DATE_PATTERN.search(normalize_inline_text("".join(self._span_text)))
        if not date_match:
            self.invalid_candidate_count += 1
            return
        published_date = parse_iso_date(date_match.group(1))
        if not published_date:
            self.invalid_candidate_count += 1
            return
        title = normalize_inline_text(self._current_title_attr or "".join(self._anchor_text))
        self.entries.append(
            ListEntry(
                title=title,
                url=self._current_href,
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
        self._main_text_depth = 0
        self._zoom_depth = 0
        self._body_depth = 0
        self._h1_depth = 0
        self._h1_text: list[str] = []
        self._body_text: list[str] = []
        self._attachment_depth = 0
        self._attachment_url = ""
        self._attachment_text: list[str] = []
        self._seen_attachment_urls: set[str] = set()
        self._ignored_body_depth = 0
        self._header_depth = 0
        self._header_text: list[str] = []
        self._info_depth = 0
        self._info_span_depth = 0
        self._info_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.lower(): value or "" for key, value in attrs}
        tag = tag.lower()
        if tag == "meta":
            name = attributes.get("name", "").strip().lower()
            if name:
                self.meta[name] = attributes.get("content", "").strip()
            return

        if tag == "div":
            if self._main_text_depth > 0:
                self._main_text_depth += 1
            if self._zoom_depth > 0:
                self._zoom_depth += 1
            if self._body_depth > 0:
                self._body_depth += 1
            if self._header_depth > 0:
                self._header_depth += 1
            if self._info_depth > 0:
                self._info_depth += 1

            element_id = attributes.get("id", "")
            classes = {name.lower() for name in attributes.get("class", "").split()}
            if element_id == "mainText" and self._main_text_depth == 0:
                self._main_text_depth = 1
            if element_id == "mainTextZoom" and self._zoom_depth == 0:
                self._zoom_depth = 1
            if self._zoom_depth > 0 and self._body_depth == 0 and (
                "trs_ueditor" in classes or "view" in classes
            ):
                self._body_depth = 1
            if self._main_text_depth > 0 and "header" in classes and self._header_depth == 0:
                self._header_depth = 1
            if self._main_text_depth > 0 and "xqrqly" in classes and self._info_depth == 0:
                self._info_depth = 1

        if tag == "span" and self._info_depth > 0 and self._info_span_depth == 0:
            self._info_span_depth = 1
        elif self._info_span_depth > 0 and tag not in VOID_TAGS:
            self._info_span_depth += 1

        if tag == "h1" and self._main_text_depth > 0:
            self._h1_depth = 1
        elif self._h1_depth > 0 and tag not in VOID_TAGS:
            self._h1_depth += 1

        if self._body_depth > 0:
            if self._ignored_body_depth > 0:
                if tag not in VOID_TAGS:
                    self._ignored_body_depth += 1
            elif tag in IGNORED_BODY_TAGS:
                self._ignored_body_depth = 1
            elif tag in BLOCK_TAGS or tag == "br":
                self._body_text.append("\n")
            elif tag in {"td", "th"}:
                self._body_text.append("\t")

        if tag == "a" and self._zoom_depth > 0 and self._attachment_depth == 0:
            href = attributes.get("href", "").strip()
            absolute_url = without_fragment(urljoin(self.article_url, href))
            if is_downloadable_attachment(absolute_url) or (
                self._body_depth == 0 and is_attachment_resource_url(absolute_url)
            ):
                self._attachment_depth = 1
                self._attachment_url = absolute_url
                self._attachment_text = []
        elif self._attachment_depth > 0 and tag not in VOID_TAGS:
            self._attachment_depth += 1

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._body_depth > 0 and self._ignored_body_depth == 0 and tag in BLOCK_TAGS:
            self._body_text.append("\n")
        if self._ignored_body_depth > 0:
            self._ignored_body_depth -= 1
        if self._h1_depth > 0:
            self._h1_depth -= 1
        if self._attachment_depth > 0:
            self._attachment_depth -= 1
            if self._attachment_depth == 0:
                self._finish_attachment()
        if tag == "div":
            if self._body_depth > 0:
                self._body_depth -= 1
            if self._zoom_depth > 0:
                self._zoom_depth -= 1
            if self._main_text_depth > 0:
                self._main_text_depth -= 1
            if self._header_depth > 0:
                self._header_depth -= 1
            if self._info_depth > 0:
                self._info_depth -= 1
        if self._info_span_depth > 0:
            self._info_span_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._h1_depth > 0:
            self._h1_text.append(data)
        if self._body_depth > 0 and self._ignored_body_depth == 0:
            self._body_text.append(data)
        if self._attachment_depth > 0:
            self._attachment_text.append(data)
        if self._header_depth > 0:
            self._header_text.append(data)
        if self._info_span_depth > 0:
            self._info_text.append(data)

    def build(self) -> ArticleData:
        header_text = normalize_inline_text("".join(self._header_text))
        info_text = normalize_inline_text("".join(self._info_text))
        published_date = (
            parse_iso_date(info_text)
            or parse_iso_date(self.meta.get("pubdate", ""))
            or parse_iso_date(header_text)
        )
        title = normalize_inline_text(self.meta.get("articletitle", "") or "".join(self._h1_text))
        source = (
            parse_header_source(info_text)
            or normalize_inline_text(self.meta.get("contentsource", ""))
            or parse_header_source(header_text)
        )
        return ArticleData(
            title=title,
            published_date=published_date,
            source=source,
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
        self.attachments.append(
            AttachmentLink(
                display_name=display_name,
                url=url,
                remote_filename=remote_filename,
            )
        )

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in VOID_TAGS:
            self.handle_endtag(tag)


class HttpClient:
    def __init__(
        self,
        timeout: int,
        retries: int,
        request_delay: float,
        logger: CrawlerLogger,
    ) -> None:
        self.timeout = timeout
        self.retries = retries
        self.request_delay = max(0.0, request_delay)
        self.logger = logger
        self._last_request_at = 0.0

    def get(self, url: str, referer: str | None = None) -> HttpResponse:
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            self._throttle()
            request = self._request(url, referer)
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
                with urlopen(request, timeout=self.timeout) as response:
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
                        status=result.status,
                        size_bytes=len(result.body),
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
                        status=error.code,
                        reason=str(error.reason),
                        elapsed_ms=round((time.monotonic() - started_at) * 1000),
                        attempt=attempt + 1,
                    )
                    raise
                self._sleep_before_retry(
                    attempt,
                    url,
                    error,
                    error.headers.get("Retry-After"),
                )
            except (URLError, TimeoutError, OSError) as error:
                last_error = error
                if attempt >= self.retries:
                    self.logger.error(
                        "http.request.failed",
                        "HTTP 请求失败。",
                        url=url,
                        error_type=type(error).__name__,
                        error=str(error),
                        elapsed_ms=round((time.monotonic() - started_at) * 1000),
                        attempt=attempt + 1,
                    )
                    raise
                self._sleep_before_retry(attempt, url, error)
        raise RuntimeError(f"Request failed: {url}") from last_error

    def download(
        self,
        url: str,
        target_path: Path,
        max_bytes: int,
        referer: str,
    ) -> dict[str, object]:
        temporary_path = target_path.with_name(f"{target_path.name}.part")
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            self._throttle()
            request = self._request(url, referer)
            started_at = time.monotonic()
            self.logger.debug(
                "http.download.request.start",
                "发起附件下载请求。",
                url=url,
                target_path=target_path,
                attempt=attempt + 1,
                max_attempts=self.retries + 1,
            )
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    final_url = without_fragment(response.geturl())
                    if not is_attachment_resource_url(final_url):
                        raise ValueError(f"Attachment redirected outside the allowed section: {final_url}")
                    content_length = response.headers.get("Content-Length")
                    reported_size: int | None = None
                    if content_length:
                        try:
                            reported_size = int(content_length)
                        except ValueError:
                            self.logger.warning(
                                "http.download.content_length.invalid",
                                "附件响应中的 Content-Length 无效，将按实际读取大小检查。",
                                url=url,
                                content_length=content_length,
                            )
                    if reported_size is not None and reported_size > max_bytes:
                        raise AttachmentTooLargeError(
                            url=url,
                            max_bytes=max_bytes,
                            detected_bytes=reported_size,
                            size_source="content_length",
                        )
                    digest = hashlib.sha256()
                    downloaded = 0
                    signature = bytearray()
                    content_type = response.headers.get("Content-Type", "")
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    with temporary_path.open("wb") as output:
                        while True:
                            chunk = response.read(128 * 1024)
                            if not chunk:
                                break
                            downloaded += len(chunk)
                            if downloaded > max_bytes:
                                raise AttachmentTooLargeError(
                                    url=url,
                                    max_bytes=max_bytes,
                                    detected_bytes=downloaded,
                                    size_source="stream",
                                )
                            if len(signature) < 64:
                                signature.extend(chunk[: 64 - len(signature)])
                            digest.update(chunk)
                            output.write(chunk)
                    validate_attachment_payload(target_path.name, content_type, downloaded, bytes(signature))
                    os.replace(temporary_path, target_path)
                    result = {
                        "status": "downloaded",
                        "final_url": final_url,
                        "content_type": content_type,
                        "size_bytes": downloaded,
                        "sha256": digest.hexdigest(),
                    }
                    self.logger.debug(
                        "http.download.request.success",
                        "附件下载请求成功。",
                        url=url,
                        final_url=final_url,
                        target_path=target_path,
                        size_bytes=downloaded,
                        elapsed_ms=round((time.monotonic() - started_at) * 1000),
                        attempt=attempt + 1,
                    )
                    return result
            except AttachmentTooLargeError:
                temporary_path.unlink(missing_ok=True)
                raise
            except HTTPError as error:
                last_error = error
                temporary_path.unlink(missing_ok=True)
                if error.code not in RETRYABLE_HTTP_CODES or attempt >= self.retries:
                    self.logger.error(
                        "http.download.request.failed",
                        "附件下载请求失败。",
                        url=url,
                        status=error.code,
                        reason=str(error.reason),
                        elapsed_ms=round((time.monotonic() - started_at) * 1000),
                        attempt=attempt + 1,
                    )
                    raise
                self._sleep_before_retry(
                    attempt,
                    url,
                    error,
                    error.headers.get("Retry-After"),
                )
            except (URLError, TimeoutError, OSError, ValueError) as error:
                last_error = error
                temporary_path.unlink(missing_ok=True)
                if attempt >= self.retries or isinstance(error, ValueError):
                    self.logger.error(
                        "http.download.request.failed",
                        "附件下载请求失败。",
                        url=url,
                        error_type=type(error).__name__,
                        error=str(error),
                        elapsed_ms=round((time.monotonic() - started_at) * 1000),
                        attempt=attempt + 1,
                    )
                    raise
                self._sleep_before_retry(attempt, url, error)
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
            self.logger.debug(
                "http.throttle.wait",
                "等待请求间隔。",
                delay_ms=round(remaining * 1000),
            )
            time.sleep(remaining)
        self._last_request_at = time.monotonic()

    def _sleep_before_retry(
        self,
        attempt: int,
        url: str,
        error: Exception,
        retry_after: str | None = None,
    ) -> None:
        if retry_after and retry_after.isdigit():
            delay_seconds = min(int(retry_after), 30)
        else:
            delay_seconds = min(2**attempt, 8)
        self.logger.warning(
            "http.retry.scheduled",
            "网络请求失败，将在延迟后重试。",
            url=url,
            error_type=type(error).__name__,
            error=str(error),
            failed_attempt=attempt + 1,
            next_attempt=attempt + 2,
            delay_seconds=delay_seconds,
        )
        time.sleep(delay_seconds)


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
    expected_signatures: dict[str, tuple[bytes, ...]] = {
        ".7z": (b"7z\xbc\xaf\x27\x1c",),
        ".docx": zip_signatures,
        ".jpeg": (b"\xff\xd8\xff",),
        ".jpg": (b"\xff\xd8\xff",),
        ".pdf": (b"%PDF-",),
        ".png": (b"\x89PNG\r\n\x1a\n",),
        ".rar": (b"Rar!\x1a\x07",),
        ".xlsx": zip_signatures,
        ".zip": zip_signatures,
    }
    suffix = Path(filename).suffix.lower()
    expected = expected_signatures.get(suffix)
    if expected and not signature.startswith(expected):
        raise ValueError(f"Attachment content does not match the {suffix} file signature: {filename}")


def is_article_url(url: str) -> bool:
    parsed = urlsplit(url)
    return (
        parsed.scheme == "https"
        and parsed.netloc.lower() == "rsj.beijing.gov.cn"
        and not parsed.query
        and not parsed.fragment
        and bool(ARTICLE_PATH_PATTERN.match(parsed.path))
    )


def without_fragment(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))


def is_downloadable_attachment(url: str) -> bool:
    if not is_attachment_resource_url(url):
        return False
    parsed = urlsplit(url)
    return Path(parsed.path).suffix.lower() in DOWNLOAD_EXTENSIONS


def is_attachment_resource_url(url: str) -> bool:
    parsed = urlsplit(url)
    filename = Path(parsed.path).name
    return (
        parsed.scheme == "https"
        and parsed.netloc.lower() == "rsj.beijing.gov.cn"
        and parsed.path.lower().startswith("/xxgk/gkzp/")
        and bool(filename)
        and not filename.lower().endswith(".html")
        and not parsed.fragment
    )


def is_recruitment_list_page_url(url: str) -> bool:
    parsed = urlsplit(url)
    return (
        parsed.scheme == "https"
        and parsed.netloc.lower() == "rsj.beijing.gov.cn"
        and not parsed.query
        and not parsed.fragment
        and bool(LIST_PAGE_PATH_PATTERN.match(parsed.path))
    )


def parse_list_page(html: str, page_url: str) -> tuple[list[ListEntry], int | None]:
    parser, page_count = parse_list_page_diagnostics(html, page_url)
    return parser.entries, page_count


def parse_list_page_diagnostics(html: str, page_url: str) -> tuple[ListPageParser, int | None]:
    parser = ListPageParser(page_url)
    parser.feed(html)
    parser.close()
    count_match = PAGE_COUNT_PATTERN.search(html)
    page_count = int(count_match.group(1)) if count_match else None
    return parser, page_count


def parse_article_page(html: str, article_url: str) -> ArticleData:
    parser = ArticlePageParser(article_url)
    parser.feed(html)
    parser.close()
    return parser.build()


def decode_html(response: HttpResponse) -> str:
    content_type = response.headers.get("content-type", "")
    charset_match = re.search(r"charset\s*=\s*['\"]?([\w.-]+)", content_type, re.IGNORECASE)
    prefix = response.body[:8192].decode("ascii", errors="ignore")
    meta_matches = re.findall(
        r"<meta\b[^>]*?charset\s*=\s*['\"]?\s*([A-Za-z0-9._-]+)",
        prefix,
        re.IGNORECASE,
    )
    charsets: list[str] = []
    if response.body.startswith(b"\xef\xbb\xbf"):
        charsets.append("utf-8-sig")
    if charset_match:
        charsets.append(charset_match.group(1))
    charsets.extend(meta_matches)
    charsets.extend(["utf-8", "gb18030"])
    tried: set[str] = set()
    for charset in charsets:
        normalized_charset = "gb18030" if charset.lower() in {"gb2312", "gbk"} else charset.lower()
        if not normalized_charset or normalized_charset in tried:
            continue
        tried.add(normalized_charset)
        try:
            return response.body.decode(normalized_charset)
        except (LookupError, UnicodeDecodeError):
            continue
    return response.body.decode("utf-8", errors="replace")


def parse_iso_date(value: str) -> date | None:
    match = DATE_PATTERN.search(value)
    if not match:
        return None
    try:
        return date.fromisoformat(match.group(1))
    except ValueError:
        return None


def previous_calendar_month(value: date) -> date:
    if value.month == 1:
        year, month = value.year - 1, 12
    else:
        year, month = value.year, value.month - 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def normalize_inline_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\u3000", " ").replace("\xa0", " ")).strip()


def normalize_multiline_text(value: str) -> str:
    lines = [normalize_inline_text(line) for line in value.splitlines()]
    return "\n".join(line for line in lines if line)


def parse_header_source(value: str) -> str:
    match = re.search(
        r"(?:来源|文章来源)\s*[:：]\s*(.*?)(?=\s+(?:字体大小|分享|打印|关闭)\s*[:：]?|$)",
        value,
    )
    return normalize_inline_text(match.group(1)) if match else ""


def safe_filename(value: str, suffix: str = "") -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", normalize_inline_text(value))
    cleaned = cleaned.rstrip(". ") or "attachment"
    suffix = suffix.lower()
    if suffix and not cleaned.lower().endswith(suffix):
        cleaned = f"{cleaned}{suffix}"
    if cleaned.split(".", 1)[0].rstrip(" ").upper() in WINDOWS_RESERVED_NAMES:
        cleaned = f"_{cleaned}"
    if len(cleaned) > 160:
        stem_suffix = suffix if cleaned.lower().endswith(suffix) else ""
        stem = cleaned[: 160 - len(stem_suffix)].rstrip(". ")
        cleaned = f"{stem}{stem_suffix}"
    return cleaned


def unique_target(directory: Path, filename: str) -> Path:
    target = directory / filename
    counter = 2
    while target.exists():
        target = directory / f"{Path(filename).stem}_{counter}{Path(filename).suffix}"
        counter += 1
    return target


def article_identifier(url: str) -> str:
    stem = Path(urlsplit(url).path).stem
    return safe_filename(stem) if stem else hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def list_page_url(base_url: str, page_index: int) -> str:
    base = base_url if base_url.endswith("/") else f"{base_url}/"
    return base if page_index == 0 else urljoin(base, f"index_{page_index}.html")


def collect_entries(
    client: HttpClient,
    logger: CrawlerLogger,
    base_url: str,
    cutoff: date,
    max_pages: int,
    max_articles: int,
    through_date: date | None = None,
) -> tuple[list[ListEntry], int]:
    selected: list[ListEntry] = []
    seen_urls: set[str] = set()
    page_count = 1
    page_index = 0
    pages_fetched = 0

    while page_index < page_count:
        if max_pages and pages_fetched >= max_pages:
            logger.warning(
                "list.scan.limit.max_pages",
                "已达到列表页上限，停止扫描。",
                max_pages=max_pages,
                pages_fetched=pages_fetched,
                articles_selected=len(selected),
            )
            break
        page_url = list_page_url(base_url, page_index)
        logger.info(
            "list.page.fetch.start",
            "开始抓取列表页。",
            page_number=page_index + 1,
            known_page_count=page_count,
            url=page_url,
        )
        page_started_at = time.monotonic()
        response = client.get(page_url, referer=base_url)
        if not is_recruitment_list_page_url(response.final_url):
            raise ValueError(f"List page redirected outside the recruitment section: {response.final_url}")
        parsed_page, discovered_page_count = parse_list_page_diagnostics(decode_html(response), page_url)
        entries = parsed_page.entries
        pages_fetched += 1
        if page_index == 0:
            if discovered_page_count is None:
                raise ValueError("The list page does not declare countPage; refusing to return incomplete data")
            if not 1 <= discovered_page_count <= 500:
                raise ValueError(f"The declared countPage is outside the supported range: {discovered_page_count}")
            page_count = discovered_page_count
        elif discovered_page_count is not None and discovered_page_count != page_count:
            raise ValueError(
                f"The list page count changed during the crawl: expected {page_count}, got {discovered_page_count}"
            )
        if parsed_page.list_container_count == 0:
            raise ValueError(f"The expected ul.list container is missing: {page_url}")
        if parsed_page.invalid_candidate_count:
            raise ValueError(
                f"{parsed_page.invalid_candidate_count} recruitment list item(s) have an invalid publication date: {page_url}"
            )
        if not entries:
            raise ValueError(f"No recruitment article entries were found on an expected list page: {page_url}")

        logger.info(
            "list.page.parse.success",
            "列表页解析成功。",
            page_number=page_index + 1,
            page_count=page_count,
            url=page_url,
            final_url=response.final_url,
            http_status=response.status,
            response_bytes=len(response.body),
            list_containers=parsed_page.list_container_count,
            article_candidates=parsed_page.article_candidate_count,
            valid_entries=len(entries),
            elapsed_ms=round((time.monotonic() - page_started_at) * 1000),
        )

        dates = [entry.published_date for entry in entries]
        page_selected = 0
        page_before_cutoff = 0
        page_after_through_date = 0
        page_duplicates = 0
        for entry in entries:
            skip_reason = ""
            if entry.published_date < cutoff:
                skip_reason = "before_cutoff"
                page_before_cutoff += 1
            elif through_date and entry.published_date > through_date:
                skip_reason = "after_through_date"
                page_after_through_date += 1
            elif entry.url in seen_urls:
                skip_reason = "duplicate_url"
                page_duplicates += 1

            if skip_reason:
                logger.info(
                    "list.article.detected",
                    "检测到列表文章，本次不纳入抓取。",
                    page_number=page_index + 1,
                    title=entry.title,
                    url=entry.url,
                    published_date=entry.published_date,
                    selected=False,
                    skip_reason=skip_reason,
                )
                continue
            seen_urls.add(entry.url)
            selected.append(entry)
            page_selected += 1
            logger.info(
                "list.article.detected",
                "检测到符合日期范围的文章。",
                page_number=page_index + 1,
                title=entry.title,
                url=entry.url,
                published_date=entry.published_date,
                selected=True,
            )
            if max_articles and len(selected) >= max_articles:
                logger.info(
                    "list.page.scan.summary",
                    "列表页文章筛选完成。",
                    page_number=page_index + 1,
                    page_count=page_count,
                    entries=len(entries),
                    selected=page_selected,
                    skipped_before_cutoff=page_before_cutoff,
                    skipped_after_through_date=page_after_through_date,
                    skipped_duplicates=page_duplicates,
                    total_selected=len(selected),
                )
                logger.warning(
                    "list.scan.limit.max_articles",
                    "已达到文章数量上限，停止扫描。",
                    max_articles=max_articles,
                    pages_fetched=pages_fetched,
                    articles_selected=len(selected),
                )
                return selected, pages_fetched

        logger.info(
            "list.page.scan.summary",
            "列表页文章筛选完成。",
            page_number=page_index + 1,
            page_count=page_count,
            entries=len(entries),
            selected=page_selected,
            skipped_before_cutoff=page_before_cutoff,
            skipped_after_through_date=page_after_through_date,
            skipped_duplicates=page_duplicates,
            total_selected=len(selected),
        )
        if dates and max(dates) < cutoff:
            logger.info(
                "list.scan.stop.before_cutoff",
                "当前列表页的最新日期仍早于截止日期，停止扫描更旧页面。",
                page_number=page_index + 1,
                newest_date=max(dates),
                cutoff_date=cutoff,
            )
            break
        page_index += 1

    logger.info(
        "list.scan.completed",
        "列表页扫描完成。",
        pages_fetched=pages_fetched,
        articles_selected=len(selected),
        cutoff_date=cutoff,
        through_date=through_date,
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
    attachment_dir = output_dir / "attachments" / article_id
    attachment_links = list(links)
    for index, link in enumerate(attachment_links, start=1):
        suffix = Path(urlsplit(link.url).path).suffix.lower()
        filename = safe_filename(link.display_name, suffix)
        target = unique_target(attachment_dir, filename)
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
            attachment_count=len(attachment_links),
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
                attachment_count=len(attachment_links),
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
                "附件超过大小上限，已跳过下载。",
                article_url=article_url,
                attachment_number=index,
                attachment_count=len(attachment_links),
                display_name=link.display_name,
                url=link.url,
                detected_size_bytes=error.detected_bytes,
                max_size_bytes=error.max_bytes,
                size_source=error.size_source,
                elapsed_ms=round((time.monotonic() - started_at) * 1000),
            )
        except Exception as error:
            metadata.update({"status": "failed", "error": str(error)})
            errors.append(
                {
                    "kind": "attachment",
                    "article_url": article_url,
                    "url": link.url,
                    "error_type": type(error).__name__,
                    "message": str(error),
                }
            )
            logger.exception(
                "attachment.download.failed",
                "文章附件下载失败。",
                article_url=article_url,
                attachment_number=index,
                attachment_count=len(attachment_links),
                display_name=link.display_name,
                url=link.url,
                local_path=local_path,
                error_type=type(error).__name__,
                error=str(error),
                elapsed_ms=round((time.monotonic() - started_at) * 1000),
            )
        results.append(metadata)
    logger.info(
        "attachment.batch.completed",
        "文章附件处理完成。",
        article_url=article_url,
        attachments_found=len(attachment_links),
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
    publisher = article.source or "北京市人力资源和社会保障局"
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
        "task_id": "beijing_hr_recruitment",
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


def run(args: argparse.Namespace, logger: CrawlerLogger) -> int:
    run_started_at = time.monotonic()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    event_log_path = output_dir / "crawler-events.jsonl"
    logger.attach_event_log(event_log_path)
    article_dir = output_dir / "articles"
    article_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(BEIJING_TIMEZONE)
    cutoff = now.date() - timedelta(days=args.lookback_days) if args.lookback_days else previous_calendar_month(now.date())
    logger.info(
        "crawler.start",
        "北京市事业单位公开招聘爬虫启动。",
        run_id=args.run_id,
        source_url=args.url,
        output_dir=output_dir,
        event_log_path=event_log_path,
        cutoff_date=cutoff,
        through_date=now.date(),
        lookback_days=args.lookback_days or None,
        request_delay_seconds=args.request_delay,
        timeout_seconds=args.timeout,
        retries=args.retries,
        max_pages=args.max_pages or None,
        max_articles=args.max_articles or None,
        max_attachment_mb=args.max_attachment_mb,
        skip_attachments=args.skip_attachments,
        log_level=args.log_level,
        python=sys.version.split()[0],
    )
    client = HttpClient(
        timeout=args.timeout,
        retries=args.retries,
        request_delay=args.request_delay,
        logger=logger,
    )
    errors: list[dict[str, object]] = []
    records: list[dict[str, object]] = []

    logger.info(
        "list.scan.start",
        "开始扫描招聘信息列表。",
        source_url=args.url,
        cutoff_date=cutoff,
        through_date=now.date(),
    )
    entries, pages_fetched = collect_entries(
        client=client,
        logger=logger,
        base_url=args.url,
        cutoff=cutoff,
        max_pages=args.max_pages,
        max_articles=args.max_articles,
        through_date=now.date(),
    )
    logger.info(
        "article.batch.selected",
        "文章筛选完成，准备抓取详情。",
        articles_selected=len(entries),
        pages_fetched=pages_fetched,
        cutoff_date=cutoff,
        through_date=now.date(),
    )

    attachment_count = 0
    attachment_success_count = 0
    for index, entry in enumerate(entries, start=1):
        article_started_at = time.monotonic()
        logger.info(
            "article.fetch.start",
            "开始抓取文章详情。",
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
                raise ValueError(f"Article redirected outside the recruitment section: {response.final_url}")
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
            article_date = article.published_date or entry.published_date
            logger.info(
                "article.parse.success",
                "文章详情解析完成。",
                article_number=index,
                article_count=len(entries),
                title=article.title or entry.title,
                url=entry.url,
                published_date=article_date,
                source=article.source or "北京市人力资源和社会保障局",
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
            if article_date < cutoff or article_date > now.date():
                message = (
                    f"Detail date {article_date.isoformat()} is outside "
                    f"{cutoff.isoformat()} through {now.date().isoformat()}"
                )
                errors.append(
                    {
                        "kind": "article_date",
                        "title": article.title or entry.title,
                        "url": entry.url,
                        "published_at": article_date.isoformat(),
                        "message": message,
                    }
                )
                logger.warning(
                    "article.skip.date_range",
                    "文章详情日期不在本次抓取范围内，已跳过。",
                    article_number=index,
                    article_count=len(entries),
                    title=article.title or entry.title,
                    url=entry.url,
                    published_date=article_date,
                    cutoff_date=cutoff,
                    through_date=now.date(),
                    reason=message,
                )
                continue
            if not article.text:
                raise ValueError("Article body could not be parsed")

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
            attachments: list[dict[str, object]] = []
            if args.skip_attachments:
                attachments = describe_skipped_attachments(article.attachments)
                logger.warning(
                    "attachment.batch.skipped",
                    "已根据运行参数跳过本文章的附件下载。",
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
            attachment_count += len(attachments)
            attachment_success_count += sum(item.get("status") == "downloaded" for item in attachments)
            fetched_at = datetime.now(BEIJING_TIMEZONE).isoformat(timespec="seconds")
            record = build_record(
                entry=entry,
                article=article,
                attachments=attachments,
                raw_html_path=raw_html_path,
                output_dir=output_dir,
                run_id=args.run_id,
                fetched_at=fetched_at,
            )
            records.append(record)
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
                attachments_downloaded=sum(item.get("status") == "downloaded" for item in attachments),
                attachments_skipped=sum(item.get("status") == "skipped" for item in attachments),
                attachments_failed=sum(item.get("status") == "failed" for item in attachments),
                warnings=record["quality"]["warnings"],
                elapsed_ms=round((time.monotonic() - article_started_at) * 1000),
            )
        except Exception as error:
            errors.append(
                {
                    "kind": "article",
                    "title": entry.title,
                    "url": entry.url,
                    "published_at": entry.published_date.isoformat(),
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
                published_date=entry.published_date,
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
    logger.info(
        "output.records.written",
        "文章记录文件已写入。",
        path=records_path,
        record_count=len(records),
        size_bytes=records_path.stat().st_size,
    )
    if errors:
        write_jsonl(errors_path, errors)
        logger.warning(
            "output.errors.written",
            "错误记录文件已写入。",
            path=errors_path,
            error_count=len(errors),
            size_bytes=errors_path.stat().st_size,
        )
    else:
        errors_path.unlink(missing_ok=True)
        logger.info(
            "output.errors.empty",
            "本次运行没有产生错误记录。",
            path=errors_path,
        )
    summary = {
        "schema_version": 1,
        "run_id": args.run_id,
        "status": "completed_with_errors" if errors else "completed",
        "source_url": args.url,
        "cutoff_date": cutoff.isoformat(),
        "through_date": now.date().isoformat(),
        "pages_fetched": pages_fetched,
        "articles_selected": len(entries),
        "articles_succeeded": len(records),
        "articles_failed": sum(item["kind"] == "article" for item in errors),
        "attachments_found": attachment_count,
        "attachments_downloaded": attachment_success_count,
        "attachments_skipped": sum(
            item.get("status") == "skipped"
            for record in records
            for item in record["attachments"]
        ),
        "attachments_failed": sum(
            item.get("status") == "failed"
            for record in records
            for item in record["attachments"]
        ),
        "articles_skipped": sum(item["kind"] == "article_date" for item in errors),
        "limited_by_max_pages": bool(args.max_pages and pages_fetched >= args.max_pages),
        "limited_by_max_articles": bool(args.max_articles and len(entries) >= args.max_articles),
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
    exit_code = 3 if errors else 0
    finish_fields = {
        **summary,
        "exit_code": exit_code,
        "elapsed_ms": round((time.monotonic() - run_started_at) * 1000),
    }
    if errors:
        logger.warning(
            "crawler.finish",
            "爬虫运行完成，但存在需要检查的错误。",
            **finish_fields,
        )
    else:
        logger.info(
            "crawler.finish",
            "爬虫运行成功完成。",
            **finish_fields,
        )
    return exit_code


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Crawl recent Beijing public recruitment articles and attachments.")
    parser.add_argument("--url", default=DEFAULT_LIST_URL)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--lookback-days", type=int, default=0, help="Override the default one-calendar-month range.")
    parser.add_argument("--max-pages", type=int, default=0, help="Limit list pages for a smoke test; 0 means unlimited.")
    parser.add_argument("--max-articles", type=int, default=0, help="Limit articles for a smoke test; 0 means unlimited.")
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
    parser.add_argument(
        "--log-level",
        choices=tuple(LOG_LEVELS),
        default="INFO",
        help="Log verbosity written to stdout/stderr and crawler-events.jsonl.",
    )
    return parser


def validate_arguments(args: argparse.Namespace) -> None:
    if not is_list_url(args.url):
        raise ValueError("The list URL must be under https://rsj.beijing.gov.cn/xxgk/gkzp/")
    if args.lookback_days < 0 or args.lookback_days > 3660:
        raise ValueError("--lookback-days must be between 0 and 3660")
    if args.max_pages < 0 or args.max_articles < 0:
        raise ValueError("--max-pages and --max-articles cannot be negative")
    if args.timeout < 1 or args.retries < 0 or args.max_attachment_mb < 1:
        raise ValueError("Timeout, retries, and attachment size settings are invalid")


def is_list_url(url: str) -> bool:
    parsed = urlsplit(url)
    return (
        parsed.scheme == "https"
        and parsed.netloc.lower() == "rsj.beijing.gov.cn"
        and parsed.path.rstrip("/") == "/xxgk/gkzp"
        and not parsed.query
        and not parsed.fragment
    )


def configure_console_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")


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
            logger.warning(
                "crawler.interrupted",
                "爬虫已被用户停止。",
                exit_code=130,
            )
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

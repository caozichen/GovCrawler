from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock

from crawlers import beijing_hr_recruitment as crawler


BASE_URL = "https://rsj.beijing.gov.cn/xxgk/gkzp/"


def list_page(*items: tuple[str, str, str], page_count: int | None = None) -> str:
    rows = "".join(
        f'<li><a href="{href}" title="{title}">{title}</a><span>{published}</span></li>'
        for title, href, published in items
    )
    count_script = f"<script>var countPage = {page_count};</script>" if page_count else ""
    return f"<html><body><ul class=\"list\">{rows}</ul>{count_script}</body></html>"


class StaticClient:
    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = pages
        self.requested_urls: list[str] = []

    def get(self, url: str, referer: str | None = None) -> crawler.HttpResponse:
        del referer
        self.requested_urls.append(url)
        html = self.pages[url]
        return crawler.HttpResponse(
            body=html.encode("utf-8"),
            final_url=url,
            status=200,
            headers={"content-type": "text/html; charset=utf-8"},
        )


class OversizedAttachmentClient:
    def download(
        self,
        url: str,
        target_path: Path,
        max_bytes: int,
        referer: str,
    ) -> dict[str, object]:
        del target_path, referer
        raise crawler.AttachmentTooLargeError(
            url=url,
            max_bytes=max_bytes,
            detected_bytes=6 * 1024 * 1024,
            size_source="content_length",
        )


class ListPageParsingTests(unittest.TestCase):
    def test_parses_relative_article_links_dates_and_page_count(self) -> None:
        html = list_page(
            (
                "北京市民政局 &amp; 所属事业单位招聘公告",
                "./202608/t20260804_4808187.html",
                "2026-08-04",
            ),
            (
                "非法日期应跳过",
                "./202608/t20260804_4808100.html",
                "2026-02-30",
            ),
            (
                "站外链接应跳过",
                "https://example.com/xxgk/gkzp/202608/t20260804_4808101.html",
                "2026-08-04",
            ),
            page_count=81,
        )

        entries, page_count = crawler.parse_list_page(html, BASE_URL)

        self.assertEqual(81, page_count)
        self.assertEqual(1, len(entries))
        self.assertEqual("北京市民政局 & 所属事业单位招聘公告", entries[0].title)
        self.assertEqual(
            "https://rsj.beijing.gov.cn/xxgk/gkzp/202608/t20260804_4808187.html",
            entries[0].url,
        )
        self.assertEqual(date(2026, 8, 4), entries[0].published_date)
        self.assertEqual(BASE_URL, entries[0].list_page_url)

    def test_uses_anchor_text_when_title_attribute_is_missing(self) -> None:
        html = """
        <ul class="list">
          <li>
            <a href="./202608/t20260803_4806021.html">首都医科大学<br/>招聘公告</a>
            <span>发布时间：2026-08-03</span>
          </li>
        </ul>
        """

        entries, _ = crawler.parse_list_page(html, BASE_URL)

        self.assertEqual(["首都医科大学招聘公告"], [entry.title for entry in entries])


class DateBoundaryTests(unittest.TestCase):
    def test_previous_calendar_month_clamps_end_of_month(self) -> None:
        cases = {
            date(2026, 3, 31): date(2026, 2, 28),
            date(2024, 3, 31): date(2024, 2, 29),
            date(2026, 1, 31): date(2025, 12, 31),
            date(2026, 5, 30): date(2026, 4, 30),
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(expected, crawler.previous_calendar_month(value))

    def test_collect_entries_includes_cutoff_excludes_older_and_deduplicates(self) -> None:
        first_page = list_page(
            (
                "今天发布",
                "./202608/t20260804_4808202.html",
                "2026-08-04",
            ),
            (
                "边界日期",
                "./202607/t20260705_4700001.html",
                "2026-07-05",
            ),
            (
                "重复文章",
                "./202608/t20260804_4808202.html",
                "2026-08-04",
            ),
            (
                "过期文章",
                "./202607/t20260704_4699999.html",
                "2026-07-04",
            ),
            page_count=3,
        )
        old_page = list_page(
            (
                "更早文章",
                "./202607/t20260703_4699998.html",
                "2026-07-03",
            ),
            page_count=3,
        )
        unreachable_page = list_page(
            (
                "不应请求",
                "./202607/t20260702_4699997.html",
                "2026-07-02",
            ),
            page_count=3,
        )
        pages = {
            BASE_URL: first_page,
            f"{BASE_URL}index_1.html": old_page,
            f"{BASE_URL}index_2.html": unreachable_page,
        }
        client = StaticClient(pages)

        with (
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            logger = crawler.CrawlerLogger("ERROR")
            try:
                entries, pages_fetched = crawler.collect_entries(
                    client=client,
                    logger=logger,
                    base_url=BASE_URL,
                    cutoff=date(2026, 7, 5),
                    max_pages=0,
                    max_articles=0,
                )
            finally:
                logger.close()

        self.assertEqual(2, pages_fetched)
        self.assertEqual(
            [BASE_URL, f"{BASE_URL}index_1.html"],
            client.requested_urls,
        )
        self.assertEqual(
            [date(2026, 8, 4), date(2026, 7, 5)],
            [entry.published_date for entry in entries],
        )
        self.assertEqual(2, len({entry.url for entry in entries}))

    def test_run_excludes_articles_published_after_beijing_today(self) -> None:
        future_url = f"{BASE_URL}202608/t20260805_4809000.html"
        today_url = f"{BASE_URL}202608/t20260804_4808202.html"
        pages = {
            BASE_URL: list_page(
                ("未来文章", "./202608/t20260805_4809000.html", "2026-08-05"),
                ("当天文章", "./202608/t20260804_4808202.html", "2026-08-04"),
                page_count=1,
            ),
            future_url: self._article_html("未来文章", "2026-08-05"),
            today_url: self._article_html("当天文章", "2026-08-04"),
        }
        client = StaticClient(pages)

        class FixedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):  # type: ignore[no-untyped-def]
                current = cls(2026, 8, 4, 12, 0, 0, tzinfo=crawler.BEIJING_TIMEZONE)
                return current if tz is None else current.astimezone(tz)

        with tempfile.TemporaryDirectory() as temporary_directory:
            args = crawler.build_argument_parser().parse_args(
                [
                    "--url",
                    BASE_URL,
                    "--output-dir",
                    temporary_directory,
                    "--run-id",
                    "date-boundary-run",
                    "--lookback-days",
                    "30",
                    "--request-delay",
                    "0",
                    "--skip-attachments",
                ]
            )
            with (
                mock.patch.object(crawler, "datetime", FixedDateTime),
                mock.patch.object(crawler, "HttpClient", return_value=client),
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                logger = crawler.CrawlerLogger("ERROR")
                try:
                    return_code = crawler.run(args, logger)
                finally:
                    logger.close()

            records_path = Path(temporary_directory) / "records.jsonl"
            records = [json.loads(line) for line in records_path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(0, return_code)
        self.assertEqual([today_url], [record["source"]["url"] for record in records])

    @staticmethod
    def _article_html(title: str, published: str) -> str:
        return f"""
        <html><head>
          <meta name="ArticleTitle" content="{title}">
          <meta name="PubDate" content="{published}">
        </head><body>
          <div id="mainText"><div id="mainTextZoom">
            <div class="view"><p>{title}正文</p></div>
          </div></div>
        </body></html>
        """


class ArticlePageParsingTests(unittest.TestCase):
    ARTICLE_URL = (
        "https://rsj.beijing.gov.cn/xxgk/gkzp/202608/"
        "t20260804_4808202.html"
    )

    def test_parses_body_metadata_relative_attachments_and_deduplicates(self) -> None:
        html = """
        <html><head>
          <meta name="ArticleTitle" content="测试招聘公告">
          <meta name="PubDate" content="2026-08-04">
          <meta name="ContentSource" content="测试发布单位">
        </head><body>
          <div id="mainText">
            <h1>回退标题</h1>
            <div id="mainTextZoom">
              <div class="view TRS_UEDITOR">
                <p>第一段&nbsp;<strong>正文</strong></p>
                <p>第二段<br>换行</p>
                <a href="https://example.com/help">普通正文链接</a>
              </div>
              <p>附件:</p>
              <p><a href="./P020260804561463724335.xlsx"><img/>岗位计划表</a></p>
              <p><a href="./P020260804561463724335.xlsx"><span>重复附件</span></a></p>
              <p><a href="javascript:window.print()">打印本页</a></p>
            </div>
          </div>
        </body></html>
        """

        article = crawler.parse_article_page(html, self.ARTICLE_URL)

        self.assertEqual("测试招聘公告", article.title)
        self.assertEqual(date(2026, 8, 4), article.published_date)
        self.assertEqual("测试发布单位", article.source)
        self.assertEqual("第一段 正文\n第二段\n换行\n普通正文链接", article.text)
        self.assertEqual(1, len(article.attachments))
        attachment = article.attachments[0]
        self.assertEqual("岗位计划表", attachment.display_name)
        self.assertEqual(
            "https://rsj.beijing.gov.cn/xxgk/gkzp/202608/"
            "P020260804561463724335.xlsx",
            attachment.url,
        )
        self.assertEqual("P020260804561463724335.xlsx", attachment.remote_filename)

    def test_excludes_script_and_style_content_from_article_text(self) -> None:
        html = """
        <div id="mainText">
          <h1>招聘公告</h1>
          <div id="mainTextZoom">
            <div class="view">
              <p>可见正文</p>
              <script>window.bad = "不属于正文";</script>
              <style>.hidden::after { content: "样式内容"; }</style>
              <p>正文结束</p>
            </div>
          </div>
        </div>
        """

        article = crawler.parse_article_page(html, self.ARTICLE_URL)

        self.assertEqual("可见正文\n正文结束", article.text)

    def test_deduplicates_attachment_urls_that_only_differ_by_fragment(self) -> None:
        html = """
        <div id="mainText"><div id="mainTextZoom">
          <div class="view"><p>正文</p></div>
          <p><a href="./plan.xlsx">岗位表</a></p>
          <p><a href="./plan.xlsx#download">岗位表重复链接</a></p>
        </div></div>
        """

        article = crawler.parse_article_page(html, self.ARTICLE_URL)

        self.assertEqual(1, len(article.attachments))

    def test_prefers_visible_publisher_over_generic_meta_source(self) -> None:
        html = """
        <meta name="ContentSource" content="北京市人力资源和社会保障局">
        <div id="mainText">
          <div class="header">
            <div class="xqrqly">
              <span>日期：2026-08-04 来源：具体招聘单位</span>
              <dl>字体大小: 大 中 小</dl>
            </div>
          </div>
          <div id="mainTextZoom"><div class="view"><p>正文</p></div></div>
        </div>
        """

        article = crawler.parse_article_page(html, self.ARTICLE_URL)

        self.assertEqual("具体招聘单位", article.source)


class AttachmentValidationTests(unittest.TestCase):
    def test_accepts_valid_xlsx_zip_signature(self) -> None:
        crawler.validate_attachment_payload(
            "plan.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            8,
            b"PK\x03\x04data",
        )

    def test_rejects_html_error_page_and_empty_file(self) -> None:
        with self.assertRaisesRegex(ValueError, "contains HTML"):
            crawler.validate_attachment_payload("plan.xlsx", "text/html; charset=utf-8", 20, b"<html>blocked")
        with self.assertRaisesRegex(ValueError, "empty"):
            crawler.validate_attachment_payload("plan.xlsx", "application/octet-stream", 0, b"")

    def test_default_attachment_limit_is_five_megabytes(self) -> None:
        args = crawler.build_argument_parser().parse_args(
            ["--output-dir", "output", "--run-id", "default-limit"]
        )

        self.assertEqual(5, args.max_attachment_mb)

    def test_oversized_attachment_is_skipped_without_recording_an_error(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        errors: list[dict[str, object]] = []

        with tempfile.TemporaryDirectory() as temporary_directory:
            output_dir = Path(temporary_directory)
            event_log_path = output_dir / "crawler-events.jsonl"
            with (
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
            ):
                logger = crawler.CrawlerLogger("INFO")
                logger.attach_event_log(event_log_path)
                try:
                    results = crawler.download_attachments(
                        client=OversizedAttachmentClient(),
                        logger=logger,
                        links=[
                            crawler.AttachmentLink(
                                display_name="大型岗位表",
                                url=f"{BASE_URL}202608/large-plan.xlsx",
                                remote_filename="large-plan.xlsx",
                            )
                        ],
                        output_dir=output_dir,
                        article_id="article-1",
                        article_url=f"{BASE_URL}202608/t20260804_4808202.html",
                        max_attachment_bytes=5 * 1024 * 1024,
                        errors=errors,
                    )
                finally:
                    logger.close()

            events = [
                json.loads(line)
                for line in event_log_path.read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual([], errors)
        self.assertEqual("skipped", results[0]["status"])
        self.assertEqual("size_limit", results[0]["skip_reason"])
        self.assertIsNone(results[0]["local_path"])
        self.assertEqual(6 * 1024 * 1024, results[0]["detected_size_bytes"])
        self.assertIn("附件超过大小上限，已跳过下载。", stdout.getvalue())
        self.assertEqual("", stderr.getvalue())
        self.assertIn(
            "attachment.download.skipped.size_limit",
            [event["event"] for event in events],
        )


class CrawlerLoggerTests(unittest.TestCase):
    def test_routes_human_logs_and_writes_structured_exception_event(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()

        with tempfile.TemporaryDirectory() as temporary_directory:
            event_log_path = Path(temporary_directory) / "crawler-events.jsonl"
            with (
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
            ):
                logger = crawler.CrawlerLogger("DEBUG")
                logger.attach_event_log(event_log_path)
                try:
                    logger.info(
                        "article.detected",
                        "检测到文章。",
                        url=f"{BASE_URL}202608/t20260804_4808202.html",
                        published_date=date(2026, 8, 4),
                    )
                    try:
                        raise ValueError("测试解析异常")
                    except ValueError:
                        logger.exception(
                            "article.failed",
                            "文章抓取失败。",
                            step="parse",
                        )
                finally:
                    logger.close()

            events = [
                json.loads(line)
                for line in event_log_path.read_text(encoding="utf-8").splitlines()
            ]

        self.assertIn("INFO    article.detected - 检测到文章。", stdout.getvalue())
        self.assertNotIn("article.detected", stderr.getvalue())
        self.assertNotIn("article.failed", stdout.getvalue())
        self.assertIn("ERROR   article.failed - 文章抓取失败。", stderr.getvalue())
        self.assertIn("ValueError: 测试解析异常", stderr.getvalue())

        self.assertEqual(["article.detected", "article.failed"], [item["event"] for item in events])
        self.assertEqual("2026-08-04", events[0]["fields"]["published_date"])
        self.assertEqual("ValueError", events[1]["exception"]["type"])
        self.assertIn("ValueError: 测试解析异常", events[1]["exception"]["traceback"])

    def test_log_level_filters_console_and_event_file(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()

        with tempfile.TemporaryDirectory() as temporary_directory:
            event_log_path = Path(temporary_directory) / "crawler-events.jsonl"
            with (
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
            ):
                logger = crawler.CrawlerLogger("WARNING")
                logger.attach_event_log(event_log_path)
                try:
                    logger.debug("debug.hidden", "不应输出。")
                    logger.info("info.hidden", "不应输出。")
                    logger.warning("warning.visible", "需要关注。")
                    logger.error("error.visible", "执行失败。")
                finally:
                    logger.close()

            events = [
                json.loads(line)
                for line in event_log_path.read_text(encoding="utf-8").splitlines()
            ]

        self.assertNotIn("debug.hidden", stdout.getvalue())
        self.assertNotIn("info.hidden", stdout.getvalue())
        self.assertIn("warning.visible", stdout.getvalue())
        self.assertNotIn("error.visible", stdout.getvalue())
        self.assertEqual(1, stderr.getvalue().count("error.visible"))
        self.assertEqual(["warning.visible", "error.visible"], [item["event"] for item in events])


class SafeFilenameTests(unittest.TestCase):
    def test_replaces_path_characters_appends_suffix_and_limits_length(self) -> None:
        filename = crawler.safe_filename(
            "  岗位/计划:*?表<>|\"  " + "很长" * 100,
            ".XLSX",
        )

        self.assertLessEqual(len(filename), 160)
        self.assertTrue(filename.endswith(".xlsx"))
        self.assertFalse(any(character in filename for character in '<>:"/\\|?*'))
        self.assertEqual(filename, Path(filename).name)

    def test_empty_or_dot_only_name_uses_attachment_fallback(self) -> None:
        self.assertEqual("attachment.pdf", crawler.safe_filename(".. ", ".pdf"))

    def test_avoids_windows_reserved_device_names(self) -> None:
        reserved_stems = {
            "CON",
            "PRN",
            "AUX",
            "NUL",
            *(f"COM{number}" for number in range(1, 10)),
            *(f"LPT{number}" for number in range(1, 10)),
        }
        for unsafe_name in ("CON", "nul.txt", "LPT1.pdf"):
            with self.subTest(unsafe_name=unsafe_name):
                filename = crawler.safe_filename(unsafe_name, ".xlsx")
                device_name = filename.split(".", 1)[0].rstrip(" ").upper()
                self.assertNotIn(device_name, reserved_stems)

    def test_unique_target_does_not_overwrite_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            (directory / "岗位表.xlsx").write_bytes(b"first")
            (directory / "岗位表_2.xlsx").write_bytes(b"second")

            target = crawler.unique_target(directory, "岗位表.xlsx")

            self.assertEqual(directory / "岗位表_3.xlsx", target)
            self.assertEqual(b"first", (directory / "岗位表.xlsx").read_bytes())
            self.assertEqual(b"second", (directory / "岗位表_2.xlsx").read_bytes())


if __name__ == "__main__":
    unittest.main()

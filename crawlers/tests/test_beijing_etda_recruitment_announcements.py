from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from crawlers import beijing_etda_recruitment_announcements as crawler


LIST_URL = crawler.DEFAULT_LIST_URL
ARTICLE_URL = (
    "https://kfqgw.beijing.gov.cn/zwgkkfq/tzgg/202607/t20260709_4800001.html"
)


class SilentLogger:
    def debug(self, *_args: object, **_kwargs: object) -> None:
        pass

    def info(self, *_args: object, **_kwargs: object) -> None:
        pass

    def warning(self, *_args: object, **_kwargs: object) -> None:
        pass


class FakeHttpClient:
    def __init__(self, responses: dict[str, crawler.HttpResponse]) -> None:
        self.responses = responses
        self.requested_urls: list[str] = []

    def get(self, url: str, referer: str | None = None) -> crawler.HttpResponse:
        del referer
        self.requested_urls.append(url)
        return self.responses[url]


def list_html(entries: list[tuple[str, str, str]], pages: int = 2) -> str:
    items = "".join(
        f'<li><font></font><a href="{href}" title="{title}">{title}</a>'
        f'<span class="date">{published}</span></li>'
        for title, href, published in entries
    )
    return f'<ul class="list">{items}</ul><script>Pager({{size:{pages}, current:0}});</script>'


class BeijingEtdaListTests(unittest.TestCase):
    def test_parses_entries_pagination_and_title_filter(self) -> None:
        html = list_html(
            [
                (
                    "北京经济技术开发区事业单位公开招聘工作人员公告",
                    "./202607/t20260709_4800001.html",
                    "2026-07-09",
                ),
                ("普通通知", "./202607/t20260708_4800002.html", "2026-07-08"),
            ],
            50,
        )
        entries, pages = crawler.parse_list_page(html, LIST_URL)

        self.assertEqual(50, pages)
        self.assertEqual(2, len(entries))
        self.assertEqual(ARTICLE_URL, entries[0].url)
        self.assertEqual(date(2026, 7, 9), entries[0].published_date)
        self.assertTrue(crawler.title_matches("补充公告：招聘工作安排"))
        self.assertFalse(crawler.title_matches("公开招聘拟录用公示"))
        self.assertFalse(crawler.title_matches("人员录用公告"))

    def test_pagination_and_inclusive_date_filter(self) -> None:
        self.assertEqual(LIST_URL, crawler.list_page_url(LIST_URL, 1))
        self.assertEqual(
            "https://kfqgw.beijing.gov.cn/zwgkkfq/tzgg/index_1.html",
            crawler.list_page_url(LIST_URL, 2),
        )
        boundary = crawler.ListEntry("招聘公告", ARTICLE_URL, date(2026, 7, 6), LIST_URL)
        self.assertIsNone(
            crawler.entry_filter_reason(boundary, date(2026, 7, 6), date(2026, 8, 6))
        )


class BeijingEtdaArticleTests(unittest.TestCase):
    def test_parses_metadata_content_and_attachment_area(self) -> None:
        html = """
        <meta name="ArticleTitle" content="经开区事业单位公开招聘工作人员公告">
        <meta name="PubDate" content="2026-07-09 17:14">
        <meta name="ContentSource" content="社会事业局">
        <h1>备用标题</h1>
        <div id="div_zhengwen">
          <div class="view TRS_UEDITOR">
            <p>第一段正文。</p><script>脚本内容</script><p>第二段正文。</p>
          </div>
          <div class="fujian">
            附件：<a href="./P020260709123456789012.xlsx">招聘岗位表</a>
          </div>
          <div id="qrcode">二维码</div>
        </div>
        """
        article, parser = crawler.parse_article_page_diagnostics(html, ARTICLE_URL)

        self.assertTrue(parser.found_article_box)
        self.assertTrue(parser.found_body)
        self.assertEqual("经开区事业单位公开招聘工作人员公告", article.title)
        self.assertEqual(date(2026, 7, 9), article.published_date)
        self.assertEqual("社会事业局", article.source)
        self.assertEqual("第一段正文。\n第二段正文。", article.text)
        self.assertNotIn("脚本内容", article.text)
        self.assertEqual(1, len(article.attachments))
        self.assertEqual("招聘岗位表", article.attachments[0].display_name)
        self.assertEqual(
            "https://kfqgw.beijing.gov.cn/zwgkkfq/tzgg/202607/P020260709123456789012.xlsx",
            article.attachments[0].url,
        )

    def test_rejects_unsafe_attachment_and_page_urls(self) -> None:
        html = """
        <div id="div_zhengwen">
          <div class="view"><p>正文</p></div>
          <div class="fujian"><a href="https://example.com/file.xlsx">附件</a></div>
        </div>
        """
        article = crawler.parse_article_page(html, ARTICLE_URL)
        self.assertEqual([], article.attachments)
        self.assertEqual(["https://example.com/file.xlsx"], article.invalid_attachment_urls)
        self.assertTrue(crawler.is_list_url(LIST_URL))
        self.assertTrue(crawler.is_article_url(ARTICLE_URL))
        self.assertTrue(
            crawler.is_attachment_resource_url(
                "https://kfqgw.beijing.gov.cn/zwgkkfq/tzgg/202607/file.xlsx"
            )
        )
        self.assertFalse(crawler.is_attachment_resource_url("https://example.com/file.xlsx"))
        self.assertFalse(crawler.is_attachment_resource_url(ARTICLE_URL))


class BeijingEtdaCollectionTests(unittest.TestCase):
    def test_filters_list_and_stops_at_cutoff_before_detail_requests(self) -> None:
        first = list_html(
            [
                ("置顶目录", "./202604/t20260416_4585058.html", "2026-07-07"),
                ("公开招聘拟录用人员公示公告", "./202607/t20260709_4800001.html", "2026-07-09"),
                ("公开招聘人员公示", "./202607/t20260708_4800002.html", "2026-07-08"),
                ("公开招聘旧公告", "./202607/t20260705_4800003.html", "2026-07-05"),
            ],
            50,
        )
        client = FakeHttpClient(
            {LIST_URL: crawler.HttpResponse(first.encode(), LIST_URL, 200, {})}
        )
        with TemporaryDirectory() as temporary_directory:
            result = crawler.collect_entries(
                client=client,  # type: ignore[arg-type]
                logger=SilentLogger(),  # type: ignore[arg-type]
                base_url=LIST_URL,
                cutoff_date=date(2026, 7, 6),
                through_date=date(2026, 8, 6),
                output_dir=Path(temporary_directory),
            )

        self.assertEqual(["公开招聘拟录用人员公示公告"], [item.title for item in result.entries])
        self.assertEqual(1, result.pages_fetched)
        self.assertEqual(2, result.skipped_by_keywords)
        self.assertEqual(1, result.skipped_before_cutoff)
        self.assertEqual("cutoff_reached", result.stop_reason)
        self.assertEqual([LIST_URL], client.requested_urls)

    def test_zero_matches_is_success(self) -> None:
        self.assertEqual(("completed", 0), crawler.determine_run_outcome(0, 0, 0, 0))
        self.assertEqual(("completed_with_errors", 0), crawler.determine_run_outcome(2, 1, 1, 1))
        self.assertEqual(("failed", 1), crawler.determine_run_outcome(2, 0, 2, 2))


class BeijingEtdaArgumentTests(unittest.TestCase):
    def test_defaults(self) -> None:
        args = crawler.build_argument_parser().parse_args(
            ["--output-dir", "out", "--run-id", "run"]
        )
        self.assertEqual(0, args.lookback_days)
        self.assertEqual(0, args.max_pages)
        self.assertEqual(0, args.max_articles)
        self.assertEqual(5, args.max_attachment_mb)
        crawler.validate_arguments(args)


if __name__ == "__main__":
    unittest.main()

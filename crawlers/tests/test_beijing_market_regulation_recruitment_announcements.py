from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from crawlers import beijing_market_regulation_recruitment_announcements as crawler


LIST_URL = crawler.DEFAULT_LIST_URL
ARTICLE_URL = (
    "https://scjgj.beijing.gov.cn/zwxx/rsxx/"
    "202603/t20260316_4558256.html"
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


def list_html(entries: list[tuple[str, str, str]], pages: int = 13) -> str:
    items = "".join(
        f'<li><a href="{href}" title="{title}">{title}</a>'
        f"<span>{published}</span></li>"
        for title, href, published in entries
    )
    return (
        '<div class="public_list"><div class="public_list_team"><ul>'
        f"{items}</ul></div></div>"
        f"<script>var currentPage = 0; var countPage = {pages};</script>"
    )


class ListTests(unittest.TestCase):
    def test_parses_entries_pagination_and_title_filter(self) -> None:
        html = list_html(
            [
                (
                    "北京市市场监督管理局所属事业单位公开招聘公告",
                    "./202603/t20260316_4558256.html",
                    "2026-03-16",
                ),
                (
                    "北京市市场监督管理局拟录用人员公示",
                    "./202601/t20260109_4415012.html",
                    "2026-01-09",
                ),
            ],
            13,
        )
        entries, pages = crawler.parse_list_page(html, LIST_URL)

        self.assertEqual(13, pages)
        self.assertEqual(2, len(entries))
        self.assertEqual(ARTICLE_URL, entries[0].url)
        self.assertEqual(date(2026, 3, 16), entries[0].published_date)
        self.assertTrue(crawler.title_matches("补充公告：招聘工作安排"))
        self.assertFalse(crawler.title_matches("公开招聘拟录用公示"))
        self.assertFalse(crawler.title_matches("考试录用公告"))

    def test_pagination_and_inclusive_date_filter(self) -> None:
        self.assertEqual(LIST_URL, crawler.list_page_url(LIST_URL, 1))
        self.assertEqual(
            "https://scjgj.beijing.gov.cn/zwxx/rsxx/index_1.html",
            crawler.list_page_url(LIST_URL, 2),
        )
        boundary = crawler.ListEntry("招聘公告", ARTICLE_URL, date(2026, 7, 6), LIST_URL)
        self.assertIsNone(
            crawler.entry_filter_reason(boundary, date(2026, 7, 6), date(2026, 8, 6))
        )


class ArticleTests(unittest.TestCase):
    def test_parses_metadata_content_and_attachment(self) -> None:
        html = """
        <meta name="ArticleTitle" content="北京市市场监督管理局公开招聘公告">
        <meta name="PubDate" content="2026-03-16">
        <meta name="ContentSource" content="北京市市场监督管理局">
        <div class="details_page">
          <h2><p>备用标题</p></h2>
          <div class="article-info">时间：2026-03-16 来源：北京市市场监督管理局</div>
          <div id="div_zhengwen">
            <div class="view TRS_UEDITOR">
              <p>第一段正文。</p><script>脚本内容</script><p>第二段正文。</p>
            </div>
          </div>
          <hr>
          <div style="padding:10px 20px">
            <a href="./P020260316614490011588.xlsx">附件：公开招聘工作人员职位表</a>
          </div>
        </div>
        """
        article, parser = crawler.parse_article_page_diagnostics(html, ARTICLE_URL)

        self.assertTrue(parser.found_article_box)
        self.assertTrue(parser.found_body)
        self.assertEqual("北京市市场监督管理局公开招聘公告", article.title)
        self.assertEqual(date(2026, 3, 16), article.published_date)
        self.assertEqual("北京市市场监督管理局", article.source)
        self.assertEqual("第一段正文。\n第二段正文。", article.text)
        self.assertNotIn("脚本内容", article.text)
        self.assertEqual(1, len(article.attachments))
        self.assertEqual("附件：公开招聘工作人员职位表", article.attachments[0].display_name)
        self.assertEqual(
            (
                "https://scjgj.beijing.gov.cn/zwxx/rsxx/202603/"
                "P020260316614490011588.xlsx"
            ),
            article.attachments[0].url,
        )

    def test_rejects_unsafe_attachment_and_page_urls(self) -> None:
        html = (
            '<div class="details_page"><div id="div_zhengwen"><p>正文</p></div>'
            '<div><a href="https://example.com/file.xlsx">附件</a></div></div>'
        )
        article = crawler.parse_article_page(html, ARTICLE_URL)
        self.assertEqual([], article.attachments)
        self.assertEqual(["https://example.com/file.xlsx"], article.invalid_attachment_urls)
        self.assertTrue(crawler.is_list_url(LIST_URL))
        self.assertTrue(crawler.is_article_url(ARTICLE_URL))
        self.assertTrue(
            crawler.is_attachment_resource_url(
                "https://scjgj.beijing.gov.cn/files/notice.xlsx"
            )
        )
        self.assertFalse(crawler.is_attachment_resource_url("https://example.com/file.xlsx"))
        self.assertFalse(crawler.is_attachment_resource_url(ARTICLE_URL))


class CollectionTests(unittest.TestCase):
    def test_filters_first_page_and_stops_at_cutoff(self) -> None:
        html = list_html(
            [
                (
                    "公开招聘工作人员公告",
                    "./202607/t20260709_4755269.html",
                    "2026-07-09",
                ),
                (
                    "事业单位招聘拟录用人员公示",
                    "./202607/t20260708_4755000.html",
                    "2026-07-08",
                ),
                (
                    "公开招聘往期公告",
                    "./202607/t20260705_4754000.html",
                    "2026-07-05",
                ),
            ],
            13,
        )
        client = FakeHttpClient(
            {LIST_URL: crawler.HttpResponse(html.encode(), LIST_URL, 200, {})}
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

        self.assertEqual(["公开招聘工作人员公告"], [item.title for item in result.entries])
        self.assertEqual(1, result.pages_fetched)
        self.assertEqual(1, result.skipped_by_keywords)
        self.assertEqual(1, result.skipped_before_cutoff)
        self.assertEqual("cutoff_reached", result.stop_reason)
        self.assertEqual([LIST_URL], client.requested_urls)

    def test_zero_matches_is_success(self) -> None:
        self.assertEqual(("completed", 0), crawler.determine_run_outcome(0, 0, 0, 0))
        self.assertEqual(
            ("completed_with_errors", 0), crawler.determine_run_outcome(2, 1, 1, 1)
        )
        self.assertEqual(("failed", 1), crawler.determine_run_outcome(2, 0, 2, 2))


class ArgumentTests(unittest.TestCase):
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

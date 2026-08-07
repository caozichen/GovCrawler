from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from crawlers import beijing_sports_recruitment_announcements as crawler


LIST_URL = crawler.DEFAULT_LIST_URL
ARTICLE_URL = (
    "https://tyj.beijing.gov.cn/bjsports/zfxxgk_/gkzp/744087151/index.html"
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


def list_html(entries: list[tuple[str, str, str]], pages: int = 18) -> str:
    items = "".join(
        '<li>'
        f'<a class="mzhd_gjc" href="{href}"></a>'
        '<p class="liebiao_bt"><a><span class="liebiao_dian">.</span></a>'
        f'<a href="{href}" title="{title}" istitle="true">{title}</a>'
        f'<span class="article-time">{published}</span></p></li>'
        for title, href, published in entries
    )
    return (
        f'<div class="gzdt"><ul>{items}</ul>'
        f'<input type="hidden" totalpage="{pages}" /></div>'
    )


class ListTests(unittest.TestCase):
    def test_parses_entries_pagination_and_ignores_empty_duplicate_link(self) -> None:
        html = list_html(
            [
                (
                    "北京市体育局所属事业单位公开招聘工作人员公告",
                    "/bjsports/zfxxgk_/gkzp/744087151/index.html",
                    "2026-08-05",
                ),
                (
                    "公开招聘拟聘用人员公示",
                    "/bjsports/zfxxgk_/gkzp/744087668/index.html",
                    "2026-08-05",
                ),
            ],
            18,
        )
        entries, pages = crawler.parse_list_page(html, LIST_URL)

        self.assertEqual(18, pages)
        self.assertEqual(2, len(entries))
        self.assertEqual(ARTICLE_URL, entries[0].url)
        self.assertEqual(date(2026, 8, 5), entries[0].published_date)
        self.assertTrue(crawler.title_matches("补充公告：招聘工作安排"))
        self.assertFalse(crawler.title_matches("公开招聘拟聘用人员公示"))
        self.assertFalse(crawler.title_matches("考试录用公告"))

    def test_dynamic_pagination_and_inclusive_date_filter(self) -> None:
        self.assertEqual(LIST_URL, crawler.list_page_url(LIST_URL, 1))
        self.assertEqual(
            "https://tyj.beijing.gov.cn/bjsports/zfxxgk_/gkzp/711e5c8c-2.html",
            crawler.list_page_url(LIST_URL, 2),
        )
        boundary = crawler.ListEntry("招聘公告", ARTICLE_URL, date(2026, 7, 7), LIST_URL)
        self.assertIsNone(
            crawler.entry_filter_reason(boundary, date(2026, 7, 7), date(2026, 8, 7))
        )
        self.assertEqual("744087151", crawler.article_identifier(ARTICLE_URL))
        self.assertNotEqual(
            crawler.article_identifier(ARTICLE_URL),
            crawler.article_identifier(
                "https://tyj.beijing.gov.cn/bjsports/zfxxgk_/gkzp/744089021/index.html"
            ),
        )


class ArticleTests(unittest.TestCase):
    def test_parses_metadata_zoom_content_and_attachments(self) -> None:
        html = """
        <meta name="ArticleTitle" content="北京市体育局公开招聘工作人员公告">
        <meta name="PubDate" content="2026-08-05 11:08">
        <meta name="ContentSource" content="北京市体育局人事处">
        <div id="zoom" class="xinagxizhengwen">
          <p>第一段正文。</p><script>脚本内容</script><p>第二段正文。</p>
          <p>附件：<a href="/bjsports/zfxxgk_/gkzp/744087151/jobs.xlsx">岗位表</a></p>
          <p><a href="/bjsports/zfxxgk_/gkzp/744087151/form.docx">报名表</a></p>
        </div>
        """
        article, parser = crawler.parse_article_page_diagnostics(html, ARTICLE_URL)

        self.assertTrue(parser.found_article_box)
        self.assertTrue(parser.found_body)
        self.assertEqual("北京市体育局公开招聘工作人员公告", article.title)
        self.assertEqual(date(2026, 8, 5), article.published_date)
        self.assertEqual("北京市体育局人事处", article.source)
        self.assertIn("第一段正文。", article.text)
        self.assertIn("第二段正文。", article.text)
        self.assertNotIn("脚本内容", article.text)
        self.assertEqual(2, len(article.attachments))
        self.assertEqual("岗位表", article.attachments[0].display_name)
        self.assertEqual(
            "https://tyj.beijing.gov.cn/bjsports/zfxxgk_/gkzp/744087151/jobs.xlsx",
            article.attachments[0].url,
        )

    def test_rejects_unsafe_attachment_and_page_urls(self) -> None:
        html = (
            '<div id="zoom"><p>正文</p>'
            '<a href="https://example.com/file.xlsx">附件</a></div>'
        )
        article = crawler.parse_article_page(html, ARTICLE_URL)
        self.assertEqual([], article.attachments)
        self.assertEqual(["https://example.com/file.xlsx"], article.invalid_attachment_urls)
        self.assertTrue(crawler.is_list_url(LIST_URL))
        self.assertTrue(crawler.is_article_url(ARTICLE_URL))
        self.assertTrue(
            crawler.is_attachment_resource_url(
                "https://tyj.beijing.gov.cn/bjsports/files/notice.xlsx"
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
                    "/bjsports/zfxxgk_/gkzp/744087151/index.html",
                    "2026-08-05",
                ),
                (
                    "公开招聘拟聘用人员公示",
                    "/bjsports/zfxxgk_/gkzp/744087668/index.html",
                    "2026-08-05",
                ),
                (
                    "公开招聘往期公告",
                    "/bjsports/zfxxgk_/gkzp/744050000/index.html",
                    "2026-07-06",
                ),
            ],
            18,
        )
        client = FakeHttpClient(
            {LIST_URL: crawler.HttpResponse(html.encode(), LIST_URL, 200, {})}
        )
        with TemporaryDirectory() as temporary_directory:
            result = crawler.collect_entries(
                client=client,  # type: ignore[arg-type]
                logger=SilentLogger(),  # type: ignore[arg-type]
                base_url=LIST_URL,
                cutoff_date=date(2026, 7, 7),
                through_date=date(2026, 8, 7),
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

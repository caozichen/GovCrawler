from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from crawlers import beijing_civil_affairs_recruitment_announcements as crawler


def js_entry(title: str, href: str, published_date: str) -> str:
    year, month, day = published_date.split("-")
    return f"""
    urls[i] = '{href}';
    headers[i] = "{title}";
    year[i] = '{year}';
    month[i] = '{month}';
    day[i] = '{day}';
    imgstrs[i] = ' ';
    i++;
    """


class BeijingCivilAffairsListParserTests(unittest.TestCase):
    def test_parses_javascript_entries_as_one_fetchable_page(self) -> None:
        html = f"""
        <script>
        var urls = new Array();
        var headers = new Array();
        var year = new Array();
        var month = new Array();
        var day = new Array();
        var i = 0;
        {js_entry(
            ' 北京市民政局所属事业单位2026年公开招聘工作人员公告',
            '/art/2026/8/4/art_383_692772.html',
            '2026-08-04',
        )}
        {js_entry(
            ' 普通通知',
            '/art/2026/7/31/art_383_692676.html',
            '2026-07-31',
        )}
        </script>
        """

        entries, page_count = crawler.parse_list_page(html, crawler.DEFAULT_LIST_URL)

        self.assertEqual(1, page_count)
        self.assertEqual(2, len(entries))
        self.assertEqual("北京市民政局所属事业单位2026年公开招聘工作人员公告", entries[0].title)
        self.assertEqual("2026-08-04", entries[0].published_date.isoformat())
        self.assertEqual(
            "https://mzj.beijing.gov.cn/art/2026/8/4/art_383_692772.html",
            entries[0].url,
        )

    def test_title_filter_requires_both_keywords(self) -> None:
        self.assertTrue(crawler.title_matches("事业单位公开招聘工作人员公告"))
        self.assertFalse(crawler.title_matches("事业单位公开招聘工作人员公示"))
        self.assertFalse(crawler.title_matches("事业单位考试公告"))
        self.assertFalse(crawler.title_matches("普通通知"))


class BeijingCivilAffairsArticleParserTests(unittest.TestCase):
    def test_parses_metadata_body_and_article_attachments(self) -> None:
        article_url = "https://mzj.beijing.gov.cn/art/2026/8/4/art_383_692772.html"
        html = """
        <html><head>
          <meta name="ArticleTitle" content="北京市民政局所属事业单位2026年公开招聘工作人员公告">
          <meta name="PubDate" content="2026-08-04 17:18">
          <meta name="ContentSource" content="北京市民政局">
        </head><body>
          <div class="bjmz_tyarticle">页面标题不应进入正文</div>
          <div id="zoom">
            <meta name="ContentStart">
            <p>现面向社会公开招聘事业单位工作人员。</p>
            <p><a href="/attach/0/ac141be1c57f43c3b177ebe56ceedf0c.xlsx">附件：招聘工作人员岗位及要求</a></p>
            <script>脚本内容不能进入正文</script>
            <div id="qrcode">二维码不能进入正文</div>
            <meta name="ContentEnd">
          </div>
        </body></html>
        """

        article = crawler.parse_article_page(html, article_url)

        self.assertEqual("北京市民政局所属事业单位2026年公开招聘工作人员公告", article.title)
        self.assertEqual("2026-08-04", article.published_date.isoformat())
        self.assertEqual("北京市民政局", article.source)
        self.assertIn("现面向社会公开招聘事业单位工作人员", article.text)
        self.assertNotIn("页面标题不应进入正文", article.text)
        self.assertNotIn("脚本内容", article.text)
        self.assertNotIn("二维码", article.text)
        self.assertEqual(1, len(article.attachments))
        self.assertEqual("附件：招聘工作人员岗位及要求", article.attachments[0].display_name)
        self.assertEqual("ac141be1c57f43c3b177ebe56ceedf0c.xlsx", article.attachments[0].remote_filename)

    def test_restricts_list_article_and_attachment_urls(self) -> None:
        self.assertTrue(crawler.is_list_url(crawler.DEFAULT_LIST_URL))
        self.assertFalse(crawler.is_list_url("https://mzj.beijing.gov.cn/col/col384/index.html"))
        article_url = "https://mzj.beijing.gov.cn/art/2026/8/4/art_383_692772.html"
        attachment_url = (
            "https://mzj.beijing.gov.cn/attach/0/ac141be1c57f43c3b177ebe56ceedf0c.xlsx"
        )
        self.assertTrue(crawler.is_article_url(article_url))
        self.assertTrue(crawler.is_attachment_resource_url(attachment_url))
        self.assertEqual("art_383_692772", crawler.article_identifier(article_url))
        self.assertFalse(
            crawler.is_attachment_resource_url(
                "https://example.com/attach/0/ac141be1c57f43c3b177ebe56ceedf0c.xlsx"
            )
        )
        self.assertFalse(crawler.is_attachment_resource_url(article_url))


class FakeHttpClient:
    def __init__(self, responses: dict[str, crawler.common.HttpResponse]) -> None:
        self.responses = responses

    def get(self, url: str, referer: str | None = None) -> crawler.common.HttpResponse:
        del referer
        return self.responses[url]


class BeijingCivilAffairsCollectionTests(unittest.TestCase):
    def test_filters_date_and_keywords_from_embedded_records(self) -> None:
        list_url = crawler.DEFAULT_LIST_URL
        html = (
            js_entry(
                "公开招聘工作人员公告",
                "/art/2026/8/4/art_383_692772.html",
                "2026-08-04",
            )
            + js_entry(
                "公开招聘工作人员公示",
                "/art/2026/7/31/art_383_692676.html",
                "2026-07-31",
            )
            + js_entry(
                "公开招聘旧公告",
                "/art/2026/7/3/art_383_692298.html",
                "2026-07-03",
            )
        )
        responses = {
            list_url: crawler.common.HttpResponse(
                body=html.encode(), final_url=list_url, status=200, headers={}
            )
        }
        logger = crawler.CrawlerLogger("ERROR")
        try:
            with TemporaryDirectory() as temporary_directory:
                entries, pages_fetched, limited = crawler.collect_entries(
                    client=FakeHttpClient(responses),
                    logger=logger,
                    base_url=list_url,
                    cutoff_date=date(2026, 7, 7),
                    through_date=date(2026, 8, 7),
                    output_dir=Path(temporary_directory),
                    max_pages=0,
                )
        finally:
            logger.close()

        self.assertEqual(1, pages_fetched)
        self.assertFalse(limited)
        self.assertEqual(["公开招聘工作人员公告"], [entry.title for entry in entries])


class BeijingCivilAffairsArgumentsTests(unittest.TestCase):
    def test_defaults_to_calendar_month_and_five_megabytes(self) -> None:
        args = crawler.build_argument_parser().parse_args(
            ["--output-dir", "out", "--run-id", "test-run"]
        )

        self.assertEqual(0, args.lookback_days)
        self.assertEqual(5, args.max_attachment_mb)
        self.assertEqual(0, args.max_pages)
        crawler.validate_arguments(args)


if __name__ == "__main__":
    unittest.main()

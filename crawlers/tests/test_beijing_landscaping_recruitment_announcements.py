from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from crawlers import beijing_landscaping_recruitment_announcements as crawler


def list_entry_html(title: str, href: str, published_date: str) -> str:
    return f"""
    <li>
      <a href="{href}" target="_blank"><a href="{href}">{title}</a></a>
      <span style="float:right">{published_date}</span>
    </li>
    """


def list_page_html(entries: str, page_count: int = 2) -> str:
    return f"""
    <nav><ul><li><a href="/">首页</a></li></ul></nav>
    <ul class="newslist clearfix">
      {entries}
      <li class="blankline"></li>
    </ul>
    <script>var countPage = {page_count};</script>
    """


class BeijingLandscapingListParserTests(unittest.TestCase):
    def test_parses_nested_links_and_page_count(self) -> None:
        html = list_page_html(
            list_entry_html(
                "北京市园林绿化局所属事业单位公开招聘博士后人员面试成绩公告",
                "./202607/t20260710_4756718.shtml",
                "2026-07-10",
            )
            + list_entry_html(
                "北京市园林绿化局普通通知",
                "./202607/t20260709_4756600.shtml",
                "2026-07-09",
            ),
            page_count=11,
        )

        entries, page_count = crawler.parse_list_page(html, crawler.DEFAULT_LIST_URL)

        self.assertEqual(11, page_count)
        self.assertEqual(2, len(entries))
        self.assertEqual(
            "北京市园林绿化局所属事业单位公开招聘博士后人员面试成绩公告",
            entries[0].title,
        )
        self.assertEqual("2026-07-10", entries[0].published_date.isoformat())
        self.assertEqual(
            "https://yllhj.beijing.gov.cn/zwgk/zkzp/202607/t20260710_4756718.shtml",
            entries[0].url,
        )

    def test_title_filter_requires_both_keywords(self) -> None:
        self.assertTrue(crawler.title_matches("事业单位公开招聘工作人员公告"))
        self.assertFalse(crawler.title_matches("事业单位公开招聘工作人员公示"))
        self.assertFalse(crawler.title_matches("事业单位考试公告"))
        self.assertFalse(crawler.title_matches("普通通知"))


class BeijingLandscapingArticleParserTests(unittest.TestCase):
    def test_parses_metadata_body_and_attachment(self) -> None:
        article_url = (
            "https://yllhj.beijing.gov.cn/zwgk/zkzp/202607/t20260710_4756718.shtml"
        )
        html = """
        <html><head>
          <meta name="ArticleTitle" content="园林绿化局事业单位公开招聘人员公告">
          <meta name="PubDate" content="2026-07-10 15:10:00">
          <meta name="ContentSource" content="北京市园林绿化局">
        </head><body>
          <h1>不应进入正文的页面标题</h1>
          <div class="article">
            <div class="view TRS_UEDITOR">
              <p>现公布公开招聘人员面试成绩。</p>
              <script>脚本内容不能进入正文</script>
            </div>
            <p><a href="./P020260710548107809235.pdf">附件：面试成绩</a></p>
            <div id="qrcode">二维码不能进入正文</div>
          </div>
        </body></html>
        """

        article = crawler.parse_article_page(html, article_url)

        self.assertEqual("园林绿化局事业单位公开招聘人员公告", article.title)
        self.assertEqual("2026-07-10", article.published_date.isoformat())
        self.assertEqual("北京市园林绿化局", article.source)
        self.assertIn("现公布公开招聘人员面试成绩", article.text)
        self.assertNotIn("不应进入正文的页面标题", article.text)
        self.assertNotIn("脚本内容", article.text)
        self.assertNotIn("二维码", article.text)
        self.assertEqual(1, len(article.attachments))
        self.assertEqual("附件：面试成绩", article.attachments[0].display_name)
        self.assertTrue(article.attachments[0].url.endswith("P020260710548107809235.pdf"))

    def test_restricts_list_article_and_attachment_urls(self) -> None:
        self.assertTrue(crawler.is_list_url(crawler.DEFAULT_LIST_URL))
        self.assertTrue(
            crawler.is_list_url(
                "https://yllhj.beijing.gov.cn/zwgk/zkzp/index.shtml"
            )
        )
        self.assertTrue(crawler.is_list_url(crawler.page_url(crawler.DEFAULT_LIST_URL, 2)))
        article_url = (
            "https://yllhj.beijing.gov.cn/zwgk/zkzp/202607/t20260710_4756718.shtml"
        )
        attachment_url = (
            "https://yllhj.beijing.gov.cn/zwgk/zkzp/202607/P020260710548107809235.pdf"
        )
        self.assertTrue(crawler.is_article_url(article_url))
        self.assertTrue(crawler.is_attachment_resource_url(attachment_url))
        self.assertEqual("t20260710_4756718", crawler.article_identifier(article_url))
        self.assertFalse(
            crawler.is_attachment_resource_url(
                "https://example.com/zwgk/zkzp/202607/P020260710548107809235.pdf"
            )
        )
        self.assertFalse(crawler.is_attachment_resource_url(article_url))


class FakeHttpClient:
    def __init__(self, responses: dict[str, crawler.common.HttpResponse]) -> None:
        self.responses = responses

    def get(self, url: str, referer: str | None = None) -> crawler.common.HttpResponse:
        del referer
        return self.responses[url]


class BeijingLandscapingCollectionTests(unittest.TestCase):
    def test_scans_until_cutoff_and_filters_before_article_fetch(self) -> None:
        first_url = crawler.DEFAULT_LIST_URL
        second_url = crawler.page_url(first_url, 2)
        first_html = list_page_html(
            list_entry_html(
                "公开招聘工作人员公告",
                "./202608/t20260801_4800001.shtml",
                "2026-08-01",
            )
            + list_entry_html(
                "公开招聘工作人员公示",
                "./202607/t20260730_4800002.shtml",
                "2026-07-30",
            )
        )
        second_html = list_page_html(
            list_entry_html(
                "公开招聘补充公告",
                "./202607/t20260705_4800003.shtml",
                "2026-07-05",
            )
            + list_entry_html(
                "公开招聘旧公告",
                "./202607/t20260704_4800004.shtml",
                "2026-07-04",
            )
        )
        responses = {
            first_url: crawler.common.HttpResponse(
                body=first_html.encode(), final_url=first_url, status=200, headers={}
            ),
            second_url: crawler.common.HttpResponse(
                body=second_html.encode(), final_url=second_url, status=200, headers={}
            ),
        }
        logger = crawler.CrawlerLogger("ERROR")
        try:
            with TemporaryDirectory() as temporary_directory:
                entries, pages_fetched, limited = crawler.collect_entries(
                    client=FakeHttpClient(responses),
                    logger=logger,
                    base_url=first_url,
                    cutoff_date=date(2026, 7, 5),
                    through_date=date(2026, 8, 5),
                    output_dir=Path(temporary_directory),
                    max_pages=0,
                )
        finally:
            logger.close()

        self.assertEqual(2, pages_fetched)
        self.assertFalse(limited)
        self.assertEqual(
            ["公开招聘工作人员公告", "公开招聘补充公告"],
            [entry.title for entry in entries],
        )


class BeijingLandscapingArgumentsTests(unittest.TestCase):
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

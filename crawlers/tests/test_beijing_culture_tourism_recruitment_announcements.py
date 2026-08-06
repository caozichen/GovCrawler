from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from crawlers import beijing_culture_tourism_recruitment_announcements as crawler


def list_entry_html(title: str, article_id: str, published_date: str) -> str:
    return f"""
    <li>
      <b>.</b>
      <a href="/bjww/362690/362733/{article_id}/index.html"
         target="_blank" title="{title}" istitle="true">{title}</a>
      <span>{published_date}</span>
    </li>
    """


def list_page_html(entries: str, page_count: int = 2) -> str:
    return f"""
    <nav><li><a href="/bjww/1718401/index.html">首页</a></li></nav>
    <div class="erListWrap" opentype="page"><ul>{entries}</ul></div>
    <a onclick="queryArticleByCondition(this,'/bjww/362690/362733/53a590af-2.html')">2</a>
    <input type="hidden" name="article_paging_list_hidden" totalpage="{page_count}">
    """


class BeijingCultureTourismListParserTests(unittest.TestCase):
    def test_parses_entries_and_page_count(self) -> None:
        html = list_page_html(
            list_entry_html(
                "北京市文物局局属事业单位2026年第二批公开招聘工作人员公告",
                "744066817",
                "2026-07-13",
            )
            + list_entry_html("北京市文物局普通人事通知", "744066700", "2026-07-12"),
            page_count=17,
        )

        entries, page_count = crawler.parse_list_page(html, crawler.DEFAULT_LIST_URL)

        self.assertEqual(17, page_count)
        self.assertEqual(2, len(entries))
        self.assertEqual(
            "北京市文物局局属事业单位2026年第二批公开招聘工作人员公告",
            entries[0].title,
        )
        self.assertEqual("2026-07-13", entries[0].published_date.isoformat())
        self.assertEqual(
            "https://wwj.beijing.gov.cn/bjww/362690/362733/744066817/index.html",
            entries[0].url,
        )

    def test_title_filter_requires_both_keywords(self) -> None:
        self.assertTrue(crawler.title_matches("事业单位公开招聘工作人员公告"))
        self.assertFalse(crawler.title_matches("事业单位公开招聘工作人员公示"))
        self.assertFalse(crawler.title_matches("事业单位考试公告"))
        self.assertFalse(crawler.title_matches("普通通知"))


class BeijingCultureTourismArticleParserTests(unittest.TestCase):
    def test_parses_metadata_body_and_attachments(self) -> None:
        article_url = (
            "https://wwj.beijing.gov.cn/bjww/362690/362733/744066817/index.html"
        )
        html = """
        <html><head>
          <meta name="ArticleTitle" content="北京市文物局事业单位公开招聘工作人员公告">
          <meta name="PubDate" content="2026-07-13 16:01">
          <meta name="ContentSource" content="北京市文物局">
        </head><body>
          <h2 class="articleTit">不应进入正文的页面标题</h2>
          <div id="othermessage">来源和日期</div>
          <div id="mainText">
            <p>现开展北京市文物局局属事业单位公开招聘工作。</p>
            <p><a href="/bjww/362690/362733/744066817/2026071316041512976.xlsx">附件1：职位及要求表</a></p>
            <p><a href="/bjww/362690/362733/744066817/2026071316041585632.docx">附件2：申请表</a></p>
            <script>脚本内容不能进入正文</script>
            <div id="qrcode">二维码不能进入正文</div>
          </div>
        </body></html>
        """

        article = crawler.parse_article_page(html, article_url)

        self.assertEqual("北京市文物局事业单位公开招聘工作人员公告", article.title)
        self.assertEqual("2026-07-13", article.published_date.isoformat())
        self.assertEqual("北京市文物局", article.source)
        self.assertIn("现开展北京市文物局局属事业单位公开招聘工作", article.text)
        self.assertNotIn("不应进入正文的页面标题", article.text)
        self.assertNotIn("脚本内容", article.text)
        self.assertNotIn("二维码", article.text)
        self.assertEqual(2, len(article.attachments))
        self.assertEqual("附件1：职位及要求表", article.attachments[0].display_name)
        self.assertTrue(article.attachments[0].url.endswith("2026071316041512976.xlsx"))
        self.assertTrue(article.attachments[1].url.endswith("2026071316041585632.docx"))

    def test_restricts_list_article_and_attachment_urls(self) -> None:
        self.assertTrue(crawler.is_list_url(crawler.DEFAULT_LIST_URL))
        self.assertTrue(
            crawler.is_list_url(
                "https://wwj.beijing.gov.cn/bjww/362690/362733/index.html"
            )
        )
        self.assertTrue(crawler.is_list_url(crawler.page_url(crawler.DEFAULT_LIST_URL, 2)))
        article_url = (
            "https://wwj.beijing.gov.cn/bjww/362690/362733/744066817/index.html"
        )
        attachment_url = (
            "https://wwj.beijing.gov.cn/bjww/362690/362733/744066817/"
            "2026071316041512976.xlsx"
        )
        self.assertTrue(crawler.is_article_url(article_url))
        self.assertTrue(crawler.is_attachment_resource_url(attachment_url))
        self.assertEqual("744066817", crawler.article_identifier(article_url))
        self.assertFalse(
            crawler.is_attachment_resource_url(
                "https://example.com/bjww/362690/362733/744066817/file.xlsx"
            )
        )
        self.assertFalse(
            crawler.is_attachment_resource_url(
                "https://wwj.beijing.gov.cn/bjww/362690/362686/744066817/file.xlsx"
            )
        )
        self.assertFalse(crawler.is_attachment_resource_url(article_url))


class FakeHttpClient:
    def __init__(self, responses: dict[str, crawler.common.HttpResponse]) -> None:
        self.responses = responses

    def get(self, url: str, referer: str | None = None) -> crawler.common.HttpResponse:
        del referer
        return self.responses[url]


class BeijingCultureTourismCollectionTests(unittest.TestCase):
    def test_scans_until_cutoff_and_filters_before_article_fetch(self) -> None:
        first_url = crawler.DEFAULT_LIST_URL
        second_url = crawler.page_url(first_url, 2)
        first_html = list_page_html(
            list_entry_html("公开招聘工作人员公告", "744066801", "2026-08-01")
            + list_entry_html("公开招聘工作人员公示", "744066802", "2026-07-30")
        )
        second_html = list_page_html(
            list_entry_html("公开招聘补充公告", "744066803", "2026-07-05")
            + list_entry_html("公开招聘旧公告", "744066804", "2026-07-04")
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


class BeijingCultureTourismArgumentsTests(unittest.TestCase):
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

from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from crawlers import beijing_intellectual_property_recruitment_announcements as crawler


def list_entry_html(
    title: str,
    article_id: str,
    published_date: str,
    column: str = "tzgg",
) -> str:
    return f"""
    <li class="clearfix">
      <a href="/zscqj/zwgk/{column}/{article_id}/index.html"
         target="_blank" title="{title}" istitle="true">{title}</a>
      <span>{published_date}</span>
    </li>
    """


def list_page_html(entries: str, page_count: int = 2) -> str:
    return f"""
    <nav><ul><li><a href="/zscqj/index/index.html">首页</a></li></ul></nav>
    <ul class="subpageCon-conList">{entries}</ul>
    <a onclick="queryArticleByCondition(this,'/zscqj/zwgk/tzgg/2fbe2a94-2.html')">2</a>
    <input type="hidden" name="article_paging_list_hidden" totalpage="{page_count}">
    """


class BeijingIntellectualPropertyListParserTests(unittest.TestCase):
    def test_parses_entries_and_page_count(self) -> None:
        html = list_page_html(
            list_entry_html(
                "北京市知识产权局所属事业单位2026年下半年公开招聘工作人员公告",
                "744066919",
                "2026-07-13",
            )
            + list_entry_html(
                "北京市知识产权局普通政策通知",
                "744084989",
                "2026-08-03",
                column="zcwj92",
            ),
            page_count=141,
        )

        entries, page_count = crawler.parse_list_page(html, crawler.DEFAULT_LIST_URL)

        self.assertEqual(141, page_count)
        self.assertEqual(2, len(entries))
        self.assertEqual(
            "北京市知识产权局所属事业单位2026年下半年公开招聘工作人员公告",
            entries[0].title,
        )
        self.assertEqual("2026-07-13", entries[0].published_date.isoformat())
        self.assertEqual(
            "https://zscqj.beijing.gov.cn/zscqj/zwgk/tzgg/744066919/index.html",
            entries[0].url,
        )
        self.assertTrue(entries[1].url.endswith("/zscqj/zwgk/zcwj92/744084989/index.html"))

    def test_title_filter_requires_both_keywords(self) -> None:
        self.assertTrue(crawler.title_matches("事业单位公开招聘工作人员公告"))
        self.assertFalse(crawler.title_matches("事业单位公开招聘工作人员公示"))
        self.assertFalse(crawler.title_matches("事业单位考试公告"))
        self.assertFalse(crawler.title_matches("普通通知"))


class BeijingIntellectualPropertyArticleParserTests(unittest.TestCase):
    def test_parses_metadata_body_and_attachment(self) -> None:
        article_url = (
            "https://zscqj.beijing.gov.cn/zscqj/zwgk/tzgg/744066919/index.html"
        )
        html = """
        <html><head>
          <meta name="ArticleTitle" content="北京市知识产权局事业单位公开招聘工作人员公告">
          <meta name="PubDate" content="2026-07-13 16:33">
          <meta name="ContentSource" content="人事处">
        </head><body>
          <div class="article-tit">不应进入正文的页面标题</div>
          <div class="article-info">来源和日期</div>
          <div class="article-word">
            <p>现开展北京市知识产权局所属事业单位公开招聘工作。</p>
            <p><a href="/zscqj/zwgk/tzgg/744066919/2026071316341824349.xlsx">附件：岗位需求表</a></p>
            <script>脚本内容不能进入正文</script>
            <div id="qrcode">二维码不能进入正文</div>
          </div>
        </body></html>
        """

        article = crawler.parse_article_page(html, article_url)

        self.assertEqual("北京市知识产权局事业单位公开招聘工作人员公告", article.title)
        self.assertEqual("2026-07-13", article.published_date.isoformat())
        self.assertEqual("人事处", article.source)
        self.assertIn("现开展北京市知识产权局所属事业单位公开招聘工作", article.text)
        self.assertNotIn("不应进入正文的页面标题", article.text)
        self.assertNotIn("脚本内容", article.text)
        self.assertNotIn("二维码", article.text)
        self.assertEqual(1, len(article.attachments))
        self.assertEqual("附件：岗位需求表", article.attachments[0].display_name)
        self.assertTrue(article.attachments[0].url.endswith("2026071316341824349.xlsx"))

    def test_restricts_list_article_and_attachment_urls(self) -> None:
        self.assertTrue(crawler.is_list_url(crawler.DEFAULT_LIST_URL))
        self.assertTrue(
            crawler.is_list_url(
                "https://zscqj.beijing.gov.cn/zscqj/zwgk/tzgg/index.html"
            )
        )
        self.assertTrue(crawler.is_list_url(crawler.page_url(crawler.DEFAULT_LIST_URL, 2)))
        article_url = (
            "https://zscqj.beijing.gov.cn/zscqj/zwgk/zcwj92/744084989/index.html"
        )
        attachment_url = (
            "https://zscqj.beijing.gov.cn/zscqj/zwgk/tzgg/744066919/"
            "2026071316341824349.xlsx"
        )
        self.assertTrue(crawler.is_article_url(article_url))
        self.assertTrue(crawler.is_attachment_resource_url(attachment_url))
        self.assertEqual("744084989", crawler.article_identifier(article_url))
        self.assertFalse(
            crawler.is_attachment_resource_url(
                "https://example.com/zscqj/zwgk/tzgg/744066919/file.xlsx"
            )
        )
        self.assertFalse(
            crawler.is_article_url(
                "https://zscqj.beijing.gov.cn/zscqj/ztzl/topic/744066919/index.html"
            )
        )
        self.assertFalse(crawler.is_attachment_resource_url(article_url))


class FakeHttpClient:
    def __init__(self, responses: dict[str, crawler.common.HttpResponse]) -> None:
        self.responses = responses

    def get(self, url: str, referer: str | None = None) -> crawler.common.HttpResponse:
        del referer
        return self.responses[url]


class BeijingIntellectualPropertyCollectionTests(unittest.TestCase):
    def test_scans_until_cutoff_and_filters_before_article_fetch(self) -> None:
        first_url = crawler.DEFAULT_LIST_URL
        second_url = crawler.page_url(first_url, 2)
        first_html = list_page_html(
            list_entry_html("公开招聘工作人员公告", "744066901", "2026-08-01")
            + list_entry_html("公开招聘工作人员公示", "744066902", "2026-07-30")
        )
        second_html = list_page_html(
            list_entry_html("公开招聘补充公告", "744066903", "2026-07-05")
            + list_entry_html("公开招聘旧公告", "744066904", "2026-07-04")
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


class BeijingIntellectualPropertyArgumentsTests(unittest.TestCase):
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

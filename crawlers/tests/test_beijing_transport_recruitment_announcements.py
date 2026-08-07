from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from crawlers import beijing_transport_recruitment_announcements as crawler


def list_entry_html(title: str, href: str, published_date: str) -> str:
    return f"""
    <li>
      <a href="{href}" target="_blank" title="{title}">{title}</a>
      <span class="pull-right">{published_date}</span>
    </li>
    """


def list_page_html(*entries: str, page_count: int = 1) -> str:
    return f"""
    <nav><ul><li><a href="/unrelated/">导航链接</a></li></ul></nav>
    <div class="container list">
      <div class="publicity">
        <ul>{''.join(entries)}</ul>
      </div>
    </div>
    <script>createPageHTML({page_count}, 0, "index", "html", "699");</script>
    """


class BeijingTransportListParserTests(unittest.TestCase):
    def test_parses_scoped_entries_and_page_count(self) -> None:
        html = list_page_html(
            list_entry_html(
                "2026年北京市交通委员会所属事业单位公开招聘工作人员公告（第二批）",
                "./202608/t20260804_4808455.html",
                "2026-08-04",
            ),
            list_entry_html(
                "普通通知",
                "./202607/t20260729_4797516.html",
                "2026-07-30",
            ),
            page_count=24,
        )

        entries, page_count = crawler.parse_list_page(html, crawler.DEFAULT_LIST_URL)

        self.assertEqual(24, page_count)
        self.assertEqual(2, len(entries))
        self.assertEqual(
            "2026年北京市交通委员会所属事业单位公开招聘工作人员公告（第二批）",
            entries[0].title,
        )
        self.assertEqual("2026-08-04", entries[0].published_date.isoformat())
        self.assertEqual(
            "https://jtw.beijing.gov.cn/xxgk/rykl1/202608/t20260804_4808455.html",
            entries[0].url,
        )

    def test_title_filter_requires_both_keywords(self) -> None:
        self.assertTrue(crawler.title_matches("事业单位公开招聘工作人员公告"))
        self.assertFalse(crawler.title_matches("事业单位公开招聘工作人员公示"))
        self.assertFalse(crawler.title_matches("事业单位考试公告"))
        self.assertFalse(crawler.title_matches("普通通知"))


class BeijingTransportArticleParserTests(unittest.TestCase):
    def test_parses_metadata_body_and_article_attachments(self) -> None:
        article_url = (
            "https://jtw.beijing.gov.cn/xxgk/rykl1/202608/t20260804_4808455.html"
        )
        html = """
        <html><head>
          <meta name="ArticleTitle" content="2026年北京市交通委员会所属事业单位公开招聘工作人员公告（第二批）">
          <meta name="PubDate" content="2026-08-04 17:08">
          <meta name="ContentSource" content="人事处">
        </head><body>
          <div class="article_m">页面标题不应进入正文</div>
          <div class="article_i">
            <div class="view TRS_UEDITOR">
              <p>现面向社会公开招聘事业单位工作人员。</p>
              <script>脚本内容不能进入正文</script>
              <div id="qrcode">二维码不能进入正文</div>
            </div>
            <p><a href="./P020260804617378967672.xlsx">附件1：招聘岗位计划表</a></p>
            <p><a href="./P020260804617379065760.docx">附件2：报名表</a></p>
          </div>
        </body></html>
        """

        article = crawler.parse_article_page(html, article_url)

        self.assertEqual(
            "2026年北京市交通委员会所属事业单位公开招聘工作人员公告（第二批）",
            article.title,
        )
        self.assertEqual("2026-08-04", article.published_date.isoformat())
        self.assertEqual("人事处", article.source)
        self.assertIn("现面向社会公开招聘事业单位工作人员", article.text)
        self.assertNotIn("页面标题不应进入正文", article.text)
        self.assertNotIn("脚本内容", article.text)
        self.assertNotIn("二维码", article.text)
        self.assertEqual(2, len(article.attachments))
        self.assertEqual("附件1：招聘岗位计划表", article.attachments[0].display_name)
        self.assertEqual("P020260804617379065760.docx", article.attachments[1].remote_filename)

    def test_restricts_list_article_and_attachment_urls(self) -> None:
        self.assertTrue(crawler.is_list_url(crawler.DEFAULT_LIST_URL))
        self.assertTrue(
            crawler.is_list_url("https://jtw.beijing.gov.cn/xxgk/rykl1/index_1.html")
        )
        article_url = (
            "https://jtw.beijing.gov.cn/xxgk/rykl1/202608/t20260804_4808455.html"
        )
        attachment_url = (
            "https://jtw.beijing.gov.cn/xxgk/rykl1/202608/P020260804617378967672.xlsx"
        )
        self.assertTrue(crawler.is_article_url(article_url))
        self.assertTrue(crawler.is_attachment_resource_url(attachment_url))
        self.assertEqual("t20260804_4808455", crawler.article_identifier(article_url))
        self.assertFalse(
            crawler.is_attachment_resource_url(
                "https://example.com/xxgk/rykl1/202608/P020260804617378967672.xlsx"
            )
        )
        self.assertFalse(crawler.is_attachment_resource_url(article_url))


class FakeHttpClient:
    def __init__(self, responses: dict[str, crawler.common.HttpResponse]) -> None:
        self.responses = responses

    def get(self, url: str, referer: str | None = None) -> crawler.common.HttpResponse:
        del referer
        return self.responses[url]


class BeijingTransportCollectionTests(unittest.TestCase):
    def test_scans_until_cutoff_and_filters_before_article_fetch(self) -> None:
        first_url = crawler.DEFAULT_LIST_URL
        second_url = crawler.page_url(first_url, 2)
        first_html = list_page_html(
            list_entry_html(
                "公开招聘工作人员公告",
                "./202608/t20260804_4808455.html",
                "2026-08-04",
            ),
            list_entry_html(
                "公开招聘工作人员公示",
                "./202607/t20260729_4797516.html",
                "2026-07-30",
            ),
            page_count=2,
        )
        second_html = list_page_html(
            list_entry_html(
                "公开招聘补充公告",
                "./202607/t20260708_4752759.html",
                "2026-07-08",
            ),
            list_entry_html(
                "公开招聘旧公告",
                "./202607/t20260706_4750000.html",
                "2026-07-06",
            ),
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
                    cutoff_date=date(2026, 7, 7),
                    through_date=date(2026, 8, 7),
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


class BeijingTransportArgumentsTests(unittest.TestCase):
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

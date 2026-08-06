from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from crawlers import haidian_recruitment_announcements as crawler


def list_entry_html(title: str, href: str, published_date: str) -> str:
    return f"""
    <tr>
      <td class="mc">
        <script>
          document.write('<a href="{href}" target="_blank"><i></i>{title}</a>');
        </script>
      </td>
      <td class="rq">{published_date}</td>
    </tr>
    """


def article_path(article_id: str, month: str = "202607") -> str:
    return (
        "/jbdt/auto4496_51791/auto4496_52699/auto4496/"
        f"auto4496_52714/{month}/t{month}{article_id[-2:]}_{article_id}.shtml"
    )


class HaidianListParserTests(unittest.TestCase):
    def test_parses_javascript_entries_and_page_count(self) -> None:
        first_href = article_path("4821001")
        second_href = article_path("4821002")
        html = f"""
        <table>
          {list_entry_html('2026年招聘会备案情况公告（二十）', first_href, '2026-07-23')}
          {list_entry_html('海淀区普通通知', second_href, '2026-07-22')}
        </table>
        <script>var countPage = 5;</script>
        """

        entries, page_count = crawler.parse_list_page(
            html, crawler.page_url(crawler.DEFAULT_LIST_URL, 1)
        )

        self.assertEqual(5, page_count)
        self.assertEqual(2, len(entries))
        self.assertEqual("2026年招聘会备案情况公告（二十）", entries[0].title)
        self.assertEqual("2026-07-23", entries[0].published_date.isoformat())
        self.assertEqual(f"https://zyk.bjhd.gov.cn{first_href}", entries[0].url)

    def test_title_filter_requires_both_keywords(self) -> None:
        self.assertTrue(crawler.title_matches("事业单位公开招聘工作人员公告"))
        self.assertFalse(crawler.title_matches("事业单位公开招聘工作人员公示"))
        self.assertFalse(crawler.title_matches("事业单位考试公告"))
        self.assertFalse(crawler.title_matches("普通通知"))


class HaidianArticleParserTests(unittest.TestCase):
    def test_parses_metadata_body_and_article_attachments(self) -> None:
        article_url = (
            "https://zyk.bjhd.gov.cn/jbdt/auto4496_51791/auto4496_52699/"
            "auto4496/auto4496_52714/202606/t20260626_4819581.shtml"
        )
        html = """
        <html><head>
          <meta name="ArticleTitle" content="海淀区事业单位公开招聘工作人员公告">
          <meta name="PubDate" content="2026-06-26">
          <meta name="ContentSource" content="区人力社保局">
        </head><body>
          <h1>不应进入正文的页面标题</h1>
          <table class="xxgk-table">
            <tr>
              <td>附件</td>
              <td><a href="./P020260626592738166208.xlsx">附件1：招聘岗位一览表.xlsx</a></td>
            </tr>
          </table>
          <div class="contentTxt">
            <p>现面向社会公开招聘事业单位工作人员。</p>
            <script>脚本内容不能进入正文</script>
            <p class="ewmtxt">二维码提示不能进入正文</p>
            <div id="qrcode">二维码不能进入正文</div>
          </div>
        </body></html>
        """

        article = crawler.parse_article_page(html, article_url)

        self.assertEqual("海淀区事业单位公开招聘工作人员公告", article.title)
        self.assertEqual("2026-06-26", article.published_date.isoformat())
        self.assertEqual("区人力社保局", article.source)
        self.assertIn("现面向社会公开招聘事业单位工作人员", article.text)
        self.assertNotIn("不应进入正文的页面标题", article.text)
        self.assertNotIn("脚本内容", article.text)
        self.assertNotIn("二维码", article.text)
        self.assertEqual(1, len(article.attachments))
        self.assertEqual("附件1：招聘岗位一览表.xlsx", article.attachments[0].display_name)
        self.assertTrue(article.attachments[0].url.endswith("P020260626592738166208.xlsx"))

    def test_restricts_list_article_and_attachment_urls(self) -> None:
        self.assertTrue(crawler.is_list_url(crawler.DEFAULT_LIST_URL))
        self.assertTrue(crawler.is_list_url(crawler.page_url(crawler.DEFAULT_LIST_URL, 1)))
        self.assertTrue(crawler.is_list_url(crawler.page_url(crawler.DEFAULT_LIST_URL, 2)))
        article_url = (
            "https://zyk.bjhd.gov.cn/jbdt/auto4496_51791/auto4496_52699/"
            "auto4496/auto4496_52714/202606/t20260626_4819581.shtml"
        )
        attachment_url = (
            "https://zyk.bjhd.gov.cn/jbdt/auto4496_51791/auto4496_52699/"
            "auto4496/auto4496_52714/202606/P020260626592738166208.xlsx"
        )
        self.assertTrue(crawler.is_article_url(article_url))
        self.assertTrue(crawler.is_attachment_resource_url(attachment_url))
        self.assertEqual("t20260626_4819581", crawler.article_identifier(article_url))
        self.assertFalse(
            crawler.is_attachment_resource_url(
                "https://example.com/jbdt/auto4496_51791/path/202606/file.xlsx"
            )
        )
        self.assertFalse(crawler.is_attachment_resource_url(article_url))


class FakeHttpClient:
    def __init__(self, responses: dict[str, crawler.common.HttpResponse]) -> None:
        self.responses = responses

    def get(self, url: str, referer: str | None = None) -> crawler.common.HttpResponse:
        del referer
        return self.responses[url]


class HaidianCollectionTests(unittest.TestCase):
    def test_scans_until_cutoff_and_filters_before_article_fetch(self) -> None:
        base_url = crawler.DEFAULT_LIST_URL
        first_url = crawler.page_url(base_url, 1)
        second_url = crawler.page_url(base_url, 2)
        first_html = f"""
        {list_entry_html(
            '公开招聘工作人员公告', article_path('4821001', '202608'), '2026-08-01'
        )}
        {list_entry_html(
            '公开招聘工作人员公示', article_path('4821002'), '2026-07-30'
        )}
        <script>var countPage = 2;</script>
        """
        second_html = f"""
        {list_entry_html(
            '公开招聘补充公告', article_path('4821003'), '2026-07-05'
        )}
        {list_entry_html(
            '公开招聘旧公告', article_path('4821004'), '2026-07-04'
        )}
        <script>var countPage = 2;</script>
        """
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
                    base_url=base_url,
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


class HaidianArgumentsTests(unittest.TestCase):
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

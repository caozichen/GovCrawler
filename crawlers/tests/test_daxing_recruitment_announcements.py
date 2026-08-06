from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from crawlers import daxing_recruitment_announcements as crawler


def list_entry_html(title: str, href: str, published_date: str) -> str:
    return f"""
    <li class="clear">
      <a href="{href}" target="_blank" title="{title}" istitle="true">{title}</a>
      <span>{published_date}</span>
    </li>
    """


class DaxingListParserTests(unittest.TestCase):
    def test_parses_entries_and_page_count(self) -> None:
        html = f"""
        <ul class="listNews">
          {list_entry_html(
              '北京市大兴区事业单位公开招聘工作人员公告',
              '/bjsdxqrmzf/zwfw/rsxx/klzp/2365048/index.html',
              '2026-08-05',
          )}
          {list_entry_html(
              '普通通知',
              '/bjsdxqrmzf/zwfw/rsxx/klzp/2365015/index.html',
              '2026-08-04',
          )}
        </ul>
        <a tagname="/bjsdxqrmzf/zwfw/rsxx/klzp/6f2880ad-2.html">下一页</a>
        <input type="hidden" name="article_paging_list_hidden" totalpage="71">
        """

        entries, page_count = crawler.parse_list_page(html, crawler.DEFAULT_LIST_URL)

        self.assertEqual(71, page_count)
        self.assertEqual(2, len(entries))
        self.assertEqual("北京市大兴区事业单位公开招聘工作人员公告", entries[0].title)
        self.assertEqual("2026-08-05", entries[0].published_date.isoformat())
        self.assertEqual(
            "https://www.bjdx.gov.cn/bjsdxqrmzf/zwfw/rsxx/klzp/2365048/index.html",
            entries[0].url,
        )

    def test_title_filter_requires_both_keywords(self) -> None:
        self.assertTrue(crawler.title_matches("事业单位公开招聘工作人员公告"))
        self.assertFalse(crawler.title_matches("事业单位公开招聘工作人员公示"))
        self.assertFalse(crawler.title_matches("事业单位考试公告"))
        self.assertFalse(crawler.title_matches("普通通知"))


class DaxingArticleParserTests(unittest.TestCase):
    def test_parses_metadata_body_and_article_attachments(self) -> None:
        article_url = (
            "https://www.bjdx.gov.cn/bjsdxqrmzf/zwfw/rsxx/klzp/2365048/index.html"
        )
        html = """
        <html><head>
          <meta name="ArticleTitle" content="事业单位公开招聘工作人员公告">
          <meta name="PubDate" content="2026-08-05">
          <meta name="ContentSource" content="区人保局">
        </head><body>
          <div class="detailCon">
            <h2>不应进入正文的页面标题</h2>
            <div class="detail">来源和日期</div>
          </div>
          <div class="article" id="articleWrap">
            <p>现面向社会公开招聘事业单位工作人员。</p>
            <p><a href="/bjsdxqrmzf/zwfw/rsxx/klzp/2365048/2026080510451063022.xlsx">附件1：招聘岗位一览表.xlsx</a></p>
            <script>脚本内容不能进入正文</script>
            <p class="ewmTxt">二维码提示不能进入正文</p>
            <div id="qrcode">二维码不能进入正文</div>
          </div>
        </body></html>
        """

        article = crawler.parse_article_page(html, article_url)

        self.assertEqual("事业单位公开招聘工作人员公告", article.title)
        self.assertEqual("2026-08-05", article.published_date.isoformat())
        self.assertEqual("区人保局", article.source)
        self.assertIn("现面向社会公开招聘事业单位工作人员", article.text)
        self.assertNotIn("不应进入正文的页面标题", article.text)
        self.assertNotIn("脚本内容", article.text)
        self.assertNotIn("二维码", article.text)
        self.assertEqual(1, len(article.attachments))
        self.assertEqual("附件1：招聘岗位一览表.xlsx", article.attachments[0].display_name)
        self.assertTrue(article.attachments[0].url.endswith("2026080510451063022.xlsx"))

    def test_restricts_list_article_and_attachment_urls(self) -> None:
        self.assertTrue(crawler.is_list_url(crawler.DEFAULT_LIST_URL))
        self.assertTrue(
            crawler.is_list_url(
                "https://www.bjdx.gov.cn/bjsdxqrmzf/zwfw/rsxx/klzp/6f2880ad-2.html"
            )
        )
        article_url = "https://www.bjdx.gov.cn/bjsdxqrmzf/zwfw/rsxx/klzp/2365048/index.html"
        attachment_url = (
            "https://www.bjdx.gov.cn/bjsdxqrmzf/zwfw/rsxx/klzp/2365048/"
            "2026080510451063022.xlsx"
        )
        self.assertTrue(crawler.is_article_url(article_url))
        self.assertTrue(crawler.is_attachment_resource_url(attachment_url))
        self.assertEqual("2365048", crawler.article_identifier(article_url))
        self.assertFalse(
            crawler.is_attachment_resource_url(
                "https://example.com/bjsdxqrmzf/zwfw/rsxx/klzp/2365048/file.xlsx"
            )
        )
        self.assertFalse(crawler.is_attachment_resource_url(article_url))


class FakeHttpClient:
    def __init__(self, responses: dict[str, crawler.common.HttpResponse]) -> None:
        self.responses = responses

    def get(self, url: str, referer: str | None = None) -> crawler.common.HttpResponse:
        del referer
        return self.responses[url]


class DaxingCollectionTests(unittest.TestCase):
    def test_scans_until_cutoff_and_filters_before_article_fetch(self) -> None:
        first_url = crawler.DEFAULT_LIST_URL
        second_url = crawler.page_url(first_url, 2)
        first_html = f"""
        {list_entry_html(
            '公开招聘工作人员公告',
            '/bjsdxqrmzf/zwfw/rsxx/klzp/2365001/index.html',
            '2026-08-01',
        )}
        {list_entry_html(
            '公开招聘工作人员公示',
            '/bjsdxqrmzf/zwfw/rsxx/klzp/2365002/index.html',
            '2026-07-30',
        )}
        <input type="hidden" name="article_paging_list_hidden" totalpage="2">
        """
        second_html = f"""
        {list_entry_html(
            '公开招聘补充公告',
            '/bjsdxqrmzf/zwfw/rsxx/klzp/2365003/index.html',
            '2026-07-05',
        )}
        {list_entry_html(
            '公开招聘旧公告',
            '/bjsdxqrmzf/zwfw/rsxx/klzp/2365004/index.html',
            '2026-07-04',
        )}
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


class DaxingArgumentsTests(unittest.TestCase):
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

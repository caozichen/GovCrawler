from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from crawlers import xicheng_recruitment_announcements as crawler


def list_entry_html(title: str, href: str, published_date: str) -> str:
    return f"""
    <div class="zt clearfix">
      <div class="zuo pull-left">
        <a href="{href}" target="_blank" title="{title}"><span class="text">{title}</span></a>
      </div>
      <div class="time pull-right">{published_date}</div>
    </div>
    """


class XichengListParserTests(unittest.TestCase):
    def test_parses_entries_and_page_count(self) -> None:
        html = f"""
        <div class="list">
          {list_entry_html('事业单位公开招聘笔试成绩公告', '/xxgk/xxxq/pnidpv1001135.html', '2026-07-16')}
          {list_entry_html('普通通知', '/xxgk/xxxq/pnidpv1001134.html', '2026-07-15')}
        </div>
        <a href="/xxgk/tzgg/pnpagenumpv40.html">末页</a>
        """

        entries, page_count = crawler.parse_list_page(html, crawler.DEFAULT_LIST_URL)

        self.assertEqual(40, page_count)
        self.assertEqual(2, len(entries))
        self.assertEqual("事业单位公开招聘笔试成绩公告", entries[0].title)
        self.assertEqual("2026-07-16", entries[0].published_date.isoformat())
        self.assertEqual(
            "https://www.bjxch.gov.cn/xxgk/xxxq/pnidpv1001135.html",
            entries[0].url,
        )

    def test_title_filter_requires_both_keywords(self) -> None:
        self.assertTrue(crawler.title_matches("事业单位公开招聘笔试成绩公告"))
        self.assertFalse(crawler.title_matches("事业单位公开招聘笔试成绩公示"))
        self.assertFalse(crawler.title_matches("事业单位考试公告"))
        self.assertFalse(crawler.title_matches("普通通知"))


class XichengArticleParserTests(unittest.TestCase):
    def test_parses_metadata_body_and_file_links(self) -> None:
        article_url = "https://www.bjxch.gov.cn/xxgk/xxxq/pnidpv1001135.html"
        html = """
        <html><head>
          <meta name="ArticleTitle" content="事业单位公开招聘笔试成绩公告">
          <meta name="PubDate" content="2026-07-16 15:59">
          <meta name="ContentSource" content="区卫生健康委">
        </head><body>
          <div class="xiangqing">
            <div style="text-align:center">事业单位公开招聘笔试成绩公告</div>
            <div class="othermessage"><p>发布时间：2026-07-16 15:59</p></div>
            <p>现将笔试成绩予以公告。</p>
            <p>附件：<a href="/file/20260716/1784188895845057854.xlsx">笔试成绩公示.xlsx</a></p>
            <script>脚本内容不能进入正文</script>
            <div class="goToTop">回到顶部</div>
          </div>
        </body></html>
        """

        article = crawler.parse_article_page(html, article_url)

        self.assertEqual("事业单位公开招聘笔试成绩公告", article.title)
        self.assertEqual("2026-07-16", article.published_date.isoformat())
        self.assertEqual("区卫生健康委", article.source)
        self.assertIn("现将笔试成绩予以公告。", article.text)
        self.assertNotIn("发布时间", article.text)
        self.assertNotIn("脚本内容", article.text)
        self.assertNotIn("回到顶部", article.text)
        self.assertEqual(1, len(article.attachments))
        self.assertEqual("笔试成绩公示.xlsx", article.attachments[0].display_name)
        self.assertTrue(article.attachments[0].url.endswith("1784188895845057854.xlsx"))

    def test_restricts_article_list_and_attachment_urls(self) -> None:
        self.assertTrue(crawler.is_list_url(crawler.DEFAULT_LIST_URL))
        self.assertTrue(
            crawler.is_article_url("https://www.bjxch.gov.cn/xxgk/xxxq/pnidpv1001135.html")
        )
        self.assertTrue(
            crawler.is_attachment_resource_url(
                "https://www.bjxch.gov.cn/file/20260716/1784188895845057854.xlsx"
            )
        )
        self.assertFalse(
            crawler.is_attachment_resource_url(
                "https://example.com/file/20260716/1784188895845057854.xlsx"
            )
        )
        self.assertFalse(
            crawler.is_attachment_resource_url(
                "https://www.bjxch.gov.cn/xxgk/xxxq/pnidpv1001135.html"
            )
        )


class FakeHttpClient:
    def __init__(self, responses: dict[str, crawler.common.HttpResponse]) -> None:
        self.responses = responses

    def get(self, url: str, referer: str | None = None) -> crawler.common.HttpResponse:
        del referer
        return self.responses[url]


class XichengCollectionTests(unittest.TestCase):
    def test_scans_until_cutoff_and_filters_before_article_fetch(self) -> None:
        first_url = crawler.DEFAULT_LIST_URL
        second_url = crawler.page_url(first_url, 2)
        first_html = f"""
        {list_entry_html('公开招聘成绩公告', '/xxgk/xxxq/pnidpv1001001.html', '2026-08-01')}
        {list_entry_html('公开招聘成绩公示', '/xxgk/xxxq/pnidpv1001002.html', '2026-07-30')}
        <a href="/xxgk/tzgg/pnpagenumpv2.html">末页</a>
        """
        second_html = f"""
        {list_entry_html('公开招聘补充公告', '/xxgk/xxxq/pnidpv1001003.html', '2026-07-05')}
        {list_entry_html('公开招聘旧公告', '/xxgk/xxxq/pnidpv1001004.html', '2026-07-04')}
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
            ["公开招聘成绩公告", "公开招聘补充公告"],
            [entry.title for entry in entries],
        )


class XichengArgumentsTests(unittest.TestCase):
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

from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from crawlers import beijing_ecology_environment_recruitment_announcements as crawler


def list_entry_html(title: str, href: str, published_date: str) -> str:
    return f"""
    <li>
      <span>{published_date}</span>
      <a href="{href}" target="_blank" title="{title}" istitle="true">{title}</a>
    </li>
    """


def list_page_html(*entries: str, page_count: int = 1) -> str:
    return f"""
    <nav><ul><li><a href="/unrelated/index.html">导航链接</a></li></ul></nav>
    <div class="h_wz_m" opentype="page">
      <ul>{''.join(entries)}</ul>
      <input type="hidden" name="article_paging_list_hidden" totalpage="{page_count}">
    </div>
    """


class BeijingEcologyEnvironmentListParserTests(unittest.TestCase):
    def test_parses_scoped_entries_and_page_count(self) -> None:
        html = list_page_html(
            list_entry_html(
                "北京市生态环境局所属事业单位公开招聘工作人员公告",
                "/bjhrb/index/xxgk69/zfxxgk43/fdzdgknr2/zkzl/744077326/index.html",
                "2026-07-24",
            ),
            list_entry_html(
                "普通通知",
                "/bjhrb/index/xxgk69/zfxxgk43/fdzdgknr2/zkzl/744057632/index.html",
                "2026-07-03",
            ),
            page_count=38,
        )

        entries, page_count = crawler.parse_list_page(html, crawler.DEFAULT_LIST_URL)

        self.assertEqual(38, page_count)
        self.assertEqual(2, len(entries))
        self.assertEqual("北京市生态环境局所属事业单位公开招聘工作人员公告", entries[0].title)
        self.assertEqual("2026-07-24", entries[0].published_date.isoformat())
        self.assertEqual(
            "https://sthjj.beijing.gov.cn/bjhrb/index/xxgk69/zfxxgk43/fdzdgknr2/zkzl/744077326/index.html",
            entries[0].url,
        )

    def test_title_filter_requires_both_keywords(self) -> None:
        self.assertTrue(crawler.title_matches("事业单位公开招聘工作人员公告"))
        self.assertFalse(crawler.title_matches("事业单位公开招聘工作人员公示"))
        self.assertFalse(crawler.title_matches("事业单位考试公告"))
        self.assertFalse(crawler.title_matches("普通通知"))


class BeijingEcologyEnvironmentArticleParserTests(unittest.TestCase):
    def test_parses_metadata_body_and_article_attachments(self) -> None:
        article_url = (
            "https://sthjj.beijing.gov.cn/bjhrb/index/xxgk69/zfxxgk43/"
            "fdzdgknr2/zkzl/743992849/index.html"
        )
        html = """
        <html><head>
          <meta name="ArticleTitle" content="北京市生态环境局所属事业单位公开招聘工作人员公告">
          <meta name="PubDate" content="2026-04-24 00:00">
          <meta name="ContentSource" content="人事处">
        </head><body>
          <div class="h_dl_s">来源和日期不应进入正文</div>
          <div class="h_dl_c" id="h_dl_c">
            <p>现面向社会公开招聘事业单位工作人员。</p>
            <p><a href="/bjhrb/index/xxgk69/zfxxgk43/fdzdgknr2/zkzl/743992849/2026042418565091936.xlsx">附件1：招聘岗位需求表.xlsx</a></p>
            <script>脚本内容不能进入正文</script>
            <div id="qrcode">二维码不能进入正文</div>
          </div>
        </body></html>
        """

        article = crawler.parse_article_page(html, article_url)

        self.assertEqual("北京市生态环境局所属事业单位公开招聘工作人员公告", article.title)
        self.assertEqual("2026-04-24", article.published_date.isoformat())
        self.assertEqual("人事处", article.source)
        self.assertIn("现面向社会公开招聘事业单位工作人员", article.text)
        self.assertNotIn("来源和日期不应进入正文", article.text)
        self.assertNotIn("脚本内容", article.text)
        self.assertNotIn("二维码", article.text)
        self.assertEqual(1, len(article.attachments))
        self.assertEqual("附件1：招聘岗位需求表.xlsx", article.attachments[0].display_name)
        self.assertTrue(article.attachments[0].url.endswith("2026042418565091936.xlsx"))

    def test_restricts_list_article_and_attachment_urls(self) -> None:
        self.assertTrue(crawler.is_list_url(crawler.DEFAULT_LIST_URL))
        self.assertTrue(
            crawler.is_list_url(
                "https://sthjj.beijing.gov.cn/bjhrb/index/xxgk69/zfxxgk43/"
                "fdzdgknr2/zkzl/3b9edb46-2.html"
            )
        )
        article_url = (
            "https://sthjj.beijing.gov.cn/bjhrb/index/xxgk69/zfxxgk43/"
            "fdzdgknr2/zkzl/743992849/index.html"
        )
        attachment_url = (
            "https://sthjj.beijing.gov.cn/bjhrb/index/xxgk69/zfxxgk43/"
            "fdzdgknr2/zkzl/743992849/2026042418565091936.xlsx"
        )
        self.assertTrue(crawler.is_article_url(article_url))
        self.assertTrue(crawler.is_attachment_resource_url(attachment_url))
        self.assertEqual("743992849", crawler.article_identifier(article_url))
        self.assertFalse(
            crawler.is_attachment_resource_url(
                "https://example.com/bjhrb/index/xxgk69/zfxxgk43/fdzdgknr2/"
                "zkzl/743992849/file.xlsx"
            )
        )
        self.assertFalse(crawler.is_attachment_resource_url(article_url))


class FakeHttpClient:
    def __init__(self, responses: dict[str, crawler.common.HttpResponse]) -> None:
        self.responses = responses

    def get(self, url: str, referer: str | None = None) -> crawler.common.HttpResponse:
        del referer
        return self.responses[url]


class BeijingEcologyEnvironmentCollectionTests(unittest.TestCase):
    def test_scans_until_cutoff_and_filters_before_article_fetch(self) -> None:
        first_url = crawler.DEFAULT_LIST_URL
        second_url = crawler.page_url(first_url, 2)
        first_html = list_page_html(
            list_entry_html(
                "公开招聘工作人员公告",
                "/bjhrb/index/xxgk69/zfxxgk43/fdzdgknr2/zkzl/744077326/index.html",
                "2026-08-01",
            ),
            list_entry_html(
                "公开招聘工作人员公示",
                "/bjhrb/index/xxgk69/zfxxgk43/fdzdgknr2/zkzl/744057632/index.html",
                "2026-07-30",
            ),
            page_count=2,
        )
        second_html = list_page_html(
            list_entry_html(
                "公开招聘补充公告",
                "/bjhrb/index/xxgk69/zfxxgk43/fdzdgknr2/zkzl/744035081/index.html",
                "2026-07-05",
            ),
            list_entry_html(
                "公开招聘旧公告",
                "/bjhrb/index/xxgk69/zfxxgk43/fdzdgknr2/zkzl/744021311/index.html",
                "2026-07-04",
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


class BeijingEcologyEnvironmentArgumentsTests(unittest.TestCase):
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

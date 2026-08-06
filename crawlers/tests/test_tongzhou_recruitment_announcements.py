from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from crawlers import tongzhou_recruitment_announcements as crawler


LIST_URL = "https://www.bjtzh.gov.cn/bjtz/zwgk/tzgg/index.shtml"
ARTICLE_URL = "https://www.bjtzh.gov.cn/bjtz/zwgk/tzgg/2026071012345678901/index.shtml"


class SilentLogger:
    def debug(self, *_args: object, **_kwargs: object) -> None:
        pass

    def info(self, *_args: object, **_kwargs: object) -> None:
        pass

    def warning(self, *_args: object, **_kwargs: object) -> None:
        pass


class FakeClient:
    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = pages
        self.requested_urls: list[str] = []

    def get(self, url: str, referer: str | None = None) -> crawler.HttpResponse:
        del referer
        self.requested_urls.append(url)
        html = self.pages[url]
        return crawler.HttpResponse(
            body=html.encode("utf-8"),
            final_url=url,
            status=200,
            headers={"content-type": "text/html"},
        )


def list_html(entries: list[tuple[str, str, str]], total_pages: int = 3) -> str:
    items = "".join(
        f'<li><a href="{href}" title="{title}">{title[:8]}...</a>'
        f'<span class="m-time">{published}</span></li>'
        for href, title, published in entries
    )
    return (
        '<meta charset="utf-8">'
        f'<ul id="channelNames" class="m-ulList">{items}</ul>'
        f'<input type="hidden" totalpage="{total_pages}">'
    )


class TongzhouListParserTests(unittest.TestCase):
    def test_parses_full_title_relative_url_date_and_total_pages(self) -> None:
        html = list_html(
            [
                (
                    "/bjtz/zwgk/tzgg/2026071012345678901/index.shtml",
                    "北京市通州区事业单位公开招聘工作人员公告",
                    "2026-07-10",
                ),
                (
                    "/bjtz/zwgk/tzgg/2026070912345678902/index.shtml",
                    "只包含招聘的公示",
                    "2026-07-09",
                ),
            ],
            total_pages=67,
        )

        parser, total_pages = crawler.parse_list_page(html, LIST_URL)

        self.assertTrue(parser.found_container)
        self.assertEqual(67, total_pages)
        self.assertEqual(2, len(parser.entries))
        self.assertEqual("北京市通州区事业单位公开招聘工作人员公告", parser.entries[0].title)
        self.assertEqual(ARTICLE_URL, parser.entries[0].url)
        self.assertEqual(date(2026, 7, 10), parser.entries[0].published_date)

    def test_pagination_and_article_identifier_do_not_collide_on_index_name(self) -> None:
        self.assertEqual(LIST_URL, crawler.list_page_url(LIST_URL, 1))
        self.assertEqual(
            "https://www.bjtzh.gov.cn/bjtz/zwgk/tzgg/2088c985-2.shtml",
            crawler.list_page_url(LIST_URL, 2),
        )
        self.assertEqual("2026071012345678901", crawler.article_identifier(ARTICLE_URL))

    def test_collect_entries_filters_before_requesting_article_pages(self) -> None:
        page_two_url = crawler.list_page_url(LIST_URL, 2)
        pages = {
            LIST_URL: list_html(
                [
                    (
                        "/bjtz/zwgk/tzgg/2026071012345678901/index.shtml",
                        "公开招聘工作人员公告",
                        "2026-07-10",
                    ),
                    (
                        "/bjtz/zwgk/tzgg/2026070912345678902/index.shtml",
                        "公开招聘工作人员公示",
                        "2026-07-09",
                    ),
                ]
            ),
            page_two_url: list_html(
                [
                    (
                        "/bjtz/zwgk/tzgg/2026070112345678903/index.shtml",
                        "过期的公开招聘公告",
                        "2026-07-01",
                    )
                ]
            ),
        }
        client = FakeClient(pages)
        with TemporaryDirectory() as temporary_directory:
            result = crawler.collect_entries(
                client=client,  # type: ignore[arg-type]
                logger=SilentLogger(),  # type: ignore[arg-type]
                base_url=LIST_URL,
                cutoff=date(2026, 7, 5),
                through_date=date(2026, 8, 5),
                output_dir=Path(temporary_directory),
            )

            self.assertEqual([ARTICLE_URL], [entry.url for entry in result.entries])
            self.assertEqual(2, result.pages_fetched)
            self.assertEqual(1, result.skipped_by_keywords)
            self.assertEqual(1, result.skipped_before_cutoff)
            self.assertEqual("cutoff_reached", result.stop_reason)
            self.assertEqual([LIST_URL, page_two_url], client.requested_urls)
            self.assertTrue((Path(temporary_directory) / "list-pages" / "page-002.html").is_file())


class TongzhouFilteringTests(unittest.TestCase):
    def test_title_requires_both_keywords_in_any_order(self) -> None:
        self.assertTrue(crawler.title_matches("公开招聘工作人员公告"))
        self.assertTrue(crawler.title_matches("公告：本次招聘工作安排"))
        self.assertFalse(crawler.title_matches("公开招聘工作人员公示"))
        self.assertFalse(crawler.title_matches("人员录用公告"))

    def test_date_range_is_inclusive_and_future_is_rejected(self) -> None:
        cutoff = date(2026, 7, 5)
        through_date = date(2026, 8, 5)

        def entry(published_date: date) -> crawler.ListEntry:
            return crawler.ListEntry("招聘公告", ARTICLE_URL, published_date, LIST_URL)

        self.assertIsNone(crawler.entry_filter_reason(entry(cutoff), cutoff, through_date))
        self.assertIsNone(crawler.entry_filter_reason(entry(through_date), cutoff, through_date))
        self.assertEqual(
            "before_cutoff",
            crawler.entry_filter_reason(entry(date(2026, 7, 4)), cutoff, through_date),
        )
        self.assertEqual(
            "after_today",
            crawler.entry_filter_reason(entry(date(2026, 8, 6)), cutoff, through_date),
        )

    def test_zero_matches_is_a_successful_business_result(self) -> None:
        self.assertEqual(("completed", 0), crawler.determine_run_outcome(0, 0, 0))
        self.assertEqual(
            ("completed", 0),
            crawler.determine_run_outcome(2 - 2, 0, 0),
            "all candidates filtered by detail validation is still a valid empty result",
        )
        self.assertEqual(("completed_with_errors", 0), crawler.determine_run_outcome(2, 1, 1))
        self.assertEqual(("failed", 1), crawler.determine_run_outcome(2, 0, 2))


class TongzhouArticleParserTests(unittest.TestCase):
    def test_parses_metadata_body_and_only_main_text_attachments(self) -> None:
        html = """
        <html><head>
          <meta name="ArticleTitle" content="通州区事业单位公开招聘工作人员&lt;br/&gt;公告">
          <meta name="PubDate" content="2026-07-10">
          <meta name="ContentSource" content="区人力社保局">
        </head><body>
          <h1><p>备用标题</p></h1>
          <a href="/bjtz/zwgk/tzgg/outside.xlsx">正文外附件</a>
          <div id="mainText">
            <ul id="zc"><li>政策标签不应进入正文</li></ul>
            <p>第一段正文。</p>
            <script>脚本内容不应进入正文</script>
            <p><a href="./2026071011223344556.xlsx">附件：招聘岗位表</a></p>
            <div id="qrBox">二维码内容</div>
            <p>二维码后的分享信息不应进入正文</p>
          </div>
        </body></html>
        """

        article, found_main_text = crawler.parse_article_page(html, ARTICLE_URL)

        self.assertTrue(found_main_text)
        self.assertEqual("通州区事业单位公开招聘工作人员公告", article.title)
        self.assertNotIn("<br", article.title)
        self.assertEqual(date(2026, 7, 10), article.published_date)
        self.assertEqual("区人力社保局", article.source)
        self.assertIn("第一段正文。", article.text)
        self.assertNotIn("政策标签", article.text)
        self.assertNotIn("脚本内容", article.text)
        self.assertNotIn("二维码后的", article.text)
        self.assertEqual(1, len(article.attachments))
        self.assertEqual("招聘岗位表", article.attachments[0].display_name)
        self.assertEqual(
            "https://www.bjtzh.gov.cn/bjtz/zwgk/tzgg/2026071012345678901/"
            "2026071011223344556.xlsx",
            article.attachments[0].url,
        )

    def test_url_validators_restrict_host_section_and_file_type(self) -> None:
        self.assertTrue(crawler.is_configured_list_url(LIST_URL))
        self.assertTrue(crawler.is_list_page_url(crawler.list_page_url(LIST_URL, 13)))
        self.assertTrue(crawler.is_article_url(ARTICLE_URL))
        self.assertTrue(
            crawler.is_attachment_resource_url(
                "https://www.bjtzh.gov.cn/bjtz/zwgk/tzgg/2026071012345678901/file.pdf"
            )
        )
        self.assertFalse(crawler.is_article_url(ARTICLE_URL.replace("www.bjtzh", "example")))
        self.assertFalse(
            crawler.is_attachment_resource_url(
                "https://www.bjtzh.gov.cn/bjtz/zwgk/tzgg/2026071012345678901/index.shtml"
            )
        )


class TongzhouArgumentsTests(unittest.TestCase):
    def test_defaults_to_calendar_month_keyword_filter_and_five_megabytes(self) -> None:
        args = crawler.build_argument_parser().parse_args(
            ["--output-dir", "out", "--run-id", "test-run"]
        )

        self.assertEqual(0, args.lookback_days)
        self.assertEqual(0, args.max_pages)
        self.assertEqual(5, args.max_attachment_mb)
        self.assertEqual(("招聘", "公告"), crawler.REQUIRED_TITLE_KEYWORDS)
        crawler.validate_arguments(args)

    def test_atomic_text_writer_replaces_temporary_file(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            target = Path(temporary_directory) / "nested" / "page.html"
            crawler.atomic_write_text(target, "通州区招聘公告\n")

            self.assertEqual("通州区招聘公告\n", target.read_text(encoding="utf-8"))
            self.assertFalse(target.with_name("page.html.tmp").exists())


if __name__ == "__main__":
    unittest.main()

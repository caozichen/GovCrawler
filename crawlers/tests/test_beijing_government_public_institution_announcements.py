from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from crawlers import beijing_government_public_institution_announcements as crawler


LIST_URL = crawler.DEFAULT_LIST_URL
ARTICLE_URL = "https://www.beijing.gov.cn/gongkai/rsxx/sydwzp/202607/t20260710_4800001.html"


class SilentLogger:
    def debug(self, *_args: object, **_kwargs: object) -> None:
        pass

    def info(self, *_args: object, **_kwargs: object) -> None:
        pass

    def warning(self, *_args: object, **_kwargs: object) -> None:
        pass


class FakeHttpClient:
    def __init__(self, responses: dict[str, crawler.HttpResponse]) -> None:
        self.responses = responses
        self.requested_urls: list[str] = []

    def get(self, url: str, referer: str | None = None) -> crawler.HttpResponse:
        del referer
        self.requested_urls.append(url)
        return self.responses[url]


def list_html(entries: list[tuple[str, str, str]], page_count: int = 2) -> str:
    items = "".join(
        '<li class="col-md">'
        f'<a href="{href}" title="{title}">{title}</a>'
        f'<span>{published}</span></li>'
        for title, href, published in entries
    )
    return f'<ul class="list">{items}</ul><script>Pager({{size:{page_count}, current:0}});</script>'


class ListAndFilterTests(unittest.TestCase):
    def test_parses_page_and_requires_both_title_keywords(self) -> None:
        html = list_html(
            [
                ("事业单位公开招聘公告", "./202607/t20260710_4800001.html", "2026-07-10"),
                ("事业单位录用公示", "./202607/t20260709_4800000.html", "2026-07-09"),
            ],
            40,
        )
        entries, page_count = crawler.parse_list_page(html, LIST_URL)

        self.assertEqual(40, page_count)
        self.assertEqual(2, len(entries))
        self.assertEqual(ARTICLE_URL, entries[0].url)
        self.assertEqual(date(2026, 7, 10), entries[0].published_date)
        self.assertTrue(crawler.title_matches("补充公告：招聘工作安排"))
        self.assertFalse(crawler.title_matches("公开招聘人员公示"))
        self.assertFalse(crawler.title_matches("人才引进公告"))

    def test_page_url_uses_zero_based_filename_suffix(self) -> None:
        self.assertEqual(LIST_URL, crawler.list_page_url(LIST_URL, 1))
        self.assertEqual(
            "https://www.beijing.gov.cn/gongkai/rsxx/sydwzp/index_1.html",
            crawler.list_page_url(LIST_URL, 2),
        )

    def test_date_filter_is_inclusive(self) -> None:
        entry = crawler.ListEntry("招聘公告", ARTICLE_URL, date(2026, 7, 6), LIST_URL)
        self.assertIsNone(crawler.entry_filter_reason(entry, date(2026, 7, 6), date(2026, 8, 6)))
        old = crawler.ListEntry("招聘公告", ARTICLE_URL, date(2026, 7, 5), LIST_URL)
        self.assertEqual(
            "before_cutoff",
            crawler.entry_filter_reason(old, date(2026, 7, 6), date(2026, 8, 6)),
        )


class ArticleParserTests(unittest.TestCase):
    def test_parses_body_metadata_and_filerider_attachments(self) -> None:
        html = """
        <meta name="ArticleTitle" content="北京市事业单位公开招聘公告">
        <meta name="PubDate" content="2026-07-10 09:38">
        <meta name="ContentSource" content="北京市测试单位">
        <h1><p>备用标题</p></h1>
        <div id="mainText">
          <div id="posterWrap"><p>封面占位</p></div>
          <div class="view TRS_UEDITOR"><p>第一段正文。</p><script>脚本</script><p>第二段正文。</p></div>
          <div id="filerider"><a href="./jobs.xlsx">附件1：岗位表</a><br><a href="./form.docx">附件2：报名表</a></div>
        </div>
        <div id="qr_wrap">二维码</div>
        """
        article, parser = crawler.parse_article_page_diagnostics(html, ARTICLE_URL)

        self.assertTrue(parser.found_main_text)
        self.assertTrue(parser.found_body)
        self.assertEqual("北京市事业单位公开招聘公告", article.title)
        self.assertEqual(date(2026, 7, 10), article.published_date)
        self.assertEqual("北京市测试单位", article.source)
        self.assertEqual("第一段正文。\n第二段正文。", article.text)
        self.assertNotIn("脚本", article.text)
        self.assertEqual(2, len(article.attachments))
        self.assertEqual("附件1：岗位表", article.attachments[0].display_name)

    def test_rejects_unsafe_attachment_url(self) -> None:
        html = '<div id="mainText"><div class="view"><p>正文</p></div><a href="https://example.com/file.xlsx">附件</a></div>'
        article = crawler.parse_article_page(html, ARTICLE_URL)
        self.assertEqual([], article.attachments)
        self.assertEqual(["https://example.com/file.xlsx"], article.invalid_attachment_urls)

    def test_url_validators_are_restricted(self) -> None:
        self.assertTrue(crawler.is_list_url(LIST_URL))
        self.assertTrue(crawler.is_article_url(ARTICLE_URL))
        self.assertTrue(crawler.is_attachment_resource_url("https://rsj.beijing.gov.cn/files/jobs.xlsx"))
        self.assertFalse(crawler.is_attachment_resource_url("https://example.com/jobs.xlsx"))
        self.assertFalse(crawler.is_attachment_resource_url(ARTICLE_URL))


class CollectionTests(unittest.TestCase):
    def test_scans_until_cutoff_and_filters_before_detail_requests(self) -> None:
        page_two_url = crawler.list_page_url(LIST_URL, 2)
        first = list_html(
            [
                ("公开招聘工作人员公告", "./202607/t20260710_4800001.html", "2026-07-10"),
                ("公开招聘工作人员公示", "./202607/t20260709_4800000.html", "2026-07-09"),
            ]
        )
        second = list_html(
            [
                ("公开招聘补充公告", "./202607/t20260706_4799999.html", "2026-07-06"),
                ("公开招聘旧公告", "./202607/t20260705_4799998.html", "2026-07-05"),
            ]
        )
        client = FakeHttpClient(
            {
                LIST_URL: crawler.HttpResponse(first.encode(), LIST_URL, 200, {}),
                page_two_url: crawler.HttpResponse(second.encode(), page_two_url, 200, {}),
            }
        )
        with TemporaryDirectory() as temporary_directory:
            result = crawler.collect_entries(
                client=client,  # type: ignore[arg-type]
                logger=SilentLogger(),  # type: ignore[arg-type]
                base_url=LIST_URL,
                cutoff_date=date(2026, 7, 6),
                through_date=date(2026, 8, 6),
                output_dir=Path(temporary_directory),
            )

        self.assertEqual(["公开招聘工作人员公告", "公开招聘补充公告"], [item.title for item in result.entries])
        self.assertEqual(2, result.pages_fetched)
        self.assertEqual(1, result.skipped_by_keywords)
        self.assertEqual(1, result.skipped_before_cutoff)
        self.assertEqual("cutoff_reached", result.stop_reason)
        self.assertEqual([LIST_URL, page_two_url], client.requested_urls)

    def test_zero_matches_is_success(self) -> None:
        self.assertEqual(("completed", 0), crawler.determine_run_outcome(0, 0, 0, 0))
        self.assertEqual(("completed_with_errors", 0), crawler.determine_run_outcome(2, 1, 1, 1))
        self.assertEqual(("failed", 1), crawler.determine_run_outcome(2, 0, 2, 2))


class ArgumentTests(unittest.TestCase):
    def test_defaults(self) -> None:
        args = crawler.build_argument_parser().parse_args(["--output-dir", "out", "--run-id", "run"])
        self.assertEqual(0, args.lookback_days)
        self.assertEqual(0, args.max_pages)
        self.assertEqual(0, args.max_articles)
        self.assertEqual(5, args.max_attachment_mb)
        crawler.validate_arguments(args)


if __name__ == "__main__":
    unittest.main()

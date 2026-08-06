from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from crawlers import beijing_planning_natural_resources_recruitment_announcements as crawler


LIST_URL = crawler.DEFAULT_LIST_URL
ARTICLE_URL = (
    "https://ghzrzyw.beijing.gov.cn/zhengwuxinxi/tzgg/sj/"
    "202607/t20260709_4755269.html"
)


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


def list_html(entries: list[dict[str, str]]) -> str:
    payload = [*entries, {"title": "-1", "time": "", "url": ""}]
    return (
        '<html><body><abbr id="json" style="display:none;">'
        + json.dumps(payload, ensure_ascii=False)
        + "</abbr></body></html>"
    )


class ListTests(unittest.TestCase):
    def test_parses_hidden_json_and_rejects_sentinel(self) -> None:
        html = list_html(
            [
                {
                    "title": "事业单位公开招聘工作人员公告",
                    "time": "2026-07-09",
                    "url": ARTICLE_URL,
                },
                {
                    "title": "普通人事公告",
                    "time": "2026-07-02",
                    "url": (
                        "https://ghzrzyw.beijing.gov.cn/zhengwuxinxi/tzgg/hd/"
                        "202607/t20260702_4744531.html"
                    ),
                },
            ]
        )
        parser, pages = crawler.parse_list_page_diagnostics(html, LIST_URL)

        self.assertTrue(parser.found_list)
        self.assertEqual(1, pages)
        self.assertEqual(2, len(parser.entries))
        self.assertEqual(1, parser.invalid_candidates)
        self.assertEqual(ARTICLE_URL, parser.entries[0].url)
        self.assertEqual(date(2026, 7, 9), parser.entries[0].published_date)
        self.assertTrue(crawler.title_matches("补充公告：招聘工作安排"))
        self.assertFalse(crawler.title_matches("公开招聘拟录用公示"))
        self.assertFalse(crawler.title_matches("人员录用公告"))

    def test_inclusive_date_filter_and_single_page_source(self) -> None:
        self.assertEqual(LIST_URL, crawler.list_page_url(LIST_URL, 1))
        with self.assertRaises(ValueError):
            crawler.list_page_url(LIST_URL, 2)
        boundary = crawler.ListEntry("招聘公告", ARTICLE_URL, date(2026, 7, 6), LIST_URL)
        self.assertIsNone(
            crawler.entry_filter_reason(boundary, date(2026, 7, 6), date(2026, 8, 6))
        )


class ArticleTests(unittest.TestCase):
    def test_accepts_site_docx_links_that_contain_legacy_word_binary(self) -> None:
        detected = crawler.validate_site_attachment_payload(
            "notice.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            1024,
            crawler.LEGACY_COMPOUND_FILE_SIGNATURE + b"payload",
        )
        self.assertEqual("legacy_word_binary", detected)
        with self.assertRaises(ValueError):
            crawler.validate_site_attachment_payload(
                "notice.docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                1024,
                b"not-an-office-file",
            )

    def test_parses_metadata_main_text_and_attachment(self) -> None:
        html = """
        <meta name="ArticleTitle" content="事业单位公开招聘工作人员公告">
        <meta name="PubDate" content="2026-07-09 17:14:00">
        <meta name="ContentSource" content="北京市规划和自然资源委员会">
        <div class="detail-wrapper">
          <h1>备用标题</h1>
          <div class="inner-content">
            <div class="content" id="mainText">
              <div class="view TRS_UEDITOR">
                <p>第一段正文。</p><script>脚本内容</script><p>第二段正文。</p>
                <p>附件：<a appendix="true" href="./list.xlsx" title="拟录用人员名单.xlsx">
                  <span>拟录用人员名单.xlsx</span></a></p>
              </div>
            </div>
          </div>
        </div>
        """
        article, parser = crawler.parse_article_page_diagnostics(html, ARTICLE_URL)

        self.assertTrue(parser.found_article_box)
        self.assertTrue(parser.found_body)
        self.assertEqual("事业单位公开招聘工作人员公告", article.title)
        self.assertEqual(date(2026, 7, 9), article.published_date)
        self.assertEqual("北京市规划和自然资源委员会", article.source)
        self.assertIn("第一段正文。", article.text)
        self.assertIn("第二段正文。", article.text)
        self.assertNotIn("脚本内容", article.text)
        self.assertEqual(1, len(article.attachments))
        self.assertEqual("拟录用人员名单.xlsx", article.attachments[0].display_name)
        self.assertEqual(
            "https://ghzrzyw.beijing.gov.cn/zhengwuxinxi/tzgg/sj/202607/list.xlsx",
            article.attachments[0].url,
        )

    def test_rejects_unsafe_attachment_and_page_urls(self) -> None:
        html = (
            '<div class="detail-wrapper"><div id="mainText"><p>正文</p>'
            '<a href="https://example.com/file.xlsx">附件</a></div></div>'
        )
        article = crawler.parse_article_page(html, ARTICLE_URL)
        self.assertEqual([], article.attachments)
        self.assertEqual(["https://example.com/file.xlsx"], article.invalid_attachment_urls)
        self.assertTrue(crawler.is_list_url(LIST_URL))
        self.assertTrue(crawler.is_article_url(ARTICLE_URL))
        self.assertTrue(
            crawler.is_attachment_resource_url(
                "https://ghzrzyw.beijing.gov.cn/files/notice.xlsx"
            )
        )
        self.assertFalse(crawler.is_attachment_resource_url("https://example.com/file.xlsx"))
        self.assertFalse(crawler.is_attachment_resource_url(ARTICLE_URL))


class CollectionTests(unittest.TestCase):
    def test_filters_full_json_dataset_before_detail_requests(self) -> None:
        html = list_html(
            [
                {"title": "公开招聘工作人员公告", "time": "2026-07-09", "url": ARTICLE_URL},
                {
                    "title": "公开招聘工作人员公示",
                    "time": "2026-07-08",
                    "url": ARTICLE_URL.replace("4755269", "4755000"),
                },
                {
                    "title": "公开招聘往期公告",
                    "time": "2026-07-05",
                    "url": ARTICLE_URL.replace("4755269", "4754000"),
                },
            ]
        )
        client = FakeHttpClient(
            {LIST_URL: crawler.HttpResponse(html.encode(), LIST_URL, 200, {})}
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

        self.assertEqual(["公开招聘工作人员公告"], [item.title for item in result.entries])
        self.assertEqual(1, result.pages_fetched)
        self.assertEqual(1, result.skipped_by_keywords)
        self.assertEqual(1, result.skipped_before_cutoff)
        self.assertEqual("cutoff_reached", result.stop_reason)
        self.assertEqual([LIST_URL], client.requested_urls)

    def test_zero_matches_is_success(self) -> None:
        self.assertEqual(("completed", 0), crawler.determine_run_outcome(0, 0, 0, 0))
        self.assertEqual(
            ("completed_with_errors", 0), crawler.determine_run_outcome(2, 1, 1, 1)
        )
        self.assertEqual(("failed", 1), crawler.determine_run_outcome(2, 0, 2, 2))


class ArgumentTests(unittest.TestCase):
    def test_defaults(self) -> None:
        args = crawler.build_argument_parser().parse_args(
            ["--output-dir", "out", "--run-id", "run"]
        )
        self.assertEqual(0, args.lookback_days)
        self.assertEqual(0, args.max_pages)
        self.assertEqual(0, args.max_articles)
        self.assertEqual(5, args.max_attachment_mb)
        crawler.validate_arguments(args)


if __name__ == "__main__":
    unittest.main()

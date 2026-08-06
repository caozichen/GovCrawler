from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from crawlers import beijing_portal_recruitment_announcements as crawler


class SilentLogger:
    def debug(self, *_args: object, **_kwargs: object) -> None:
        pass

    def info(self, *_args: object, **_kwargs: object) -> None:
        pass

    def warning(self, *_args: object, **_kwargs: object) -> None:
        pass


class FakePortalClient:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = responses
        self.requests: list[tuple[str, dict[str, object], str]] = []

    def post_json(
        self,
        url: str,
        payload: dict[str, object],
        referer: str,
    ) -> dict[str, object]:
        self.requests.append((url, payload, referer))
        return self.responses[len(self.requests) - 1]


def api_item(
    term_id: str,
    title: str,
    published: str,
    **basic_overrides: object,
) -> dict[str, object]:
    basic: dict[str, object] = {
        "l20": published,
        "websitename": "北京市测试单位",
        "url": f"https://rsj.beijing.gov.cn/xxgk/gkzp/202607/{term_id}.html",
    }
    basic.update(basic_overrides)
    return {
        "termId": term_id,
        "title": title,
        "content": "第一段正文。\n第二段正文。",
        "contentHtml": "<p>第一段正文。</p><p>第二段正文。</p>",
        "termBasicInfo": basic,
    }


def api_response(
    items: list[dict[str, object]],
    total_pages: int = 1,
    total_items: int | None = None,
) -> dict[str, object]:
    return {
        "allCount": len(items) if total_items is None else total_items,
        "pageNum": 0,
        "pageSize": 100,
        "data": items,
        "httpStatusCode": 200,
        "code": 1000,
        "message": "操作成功",
        "totalPages": total_pages,
        "success": True,
    }


class BeijingPortalFilteringTests(unittest.TestCase):
    def test_title_requires_both_keywords_in_any_order(self) -> None:
        self.assertTrue(crawler.title_matches("公开招聘教师公告"))
        self.assertTrue(crawler.title_matches("补充公告：招聘工作安排"))
        self.assertFalse(crawler.title_matches("公开招聘教师公示"))
        self.assertFalse(crawler.title_matches("人才引进公告"))

    def test_date_range_is_inclusive_and_future_is_rejected(self) -> None:
        cutoff = date(2026, 7, 6)
        through_date = date(2026, 8, 6)
        title = "公开招聘教师公告"

        self.assertIsNone(crawler.entry_filter_reason(title, cutoff, cutoff, through_date))
        self.assertIsNone(crawler.entry_filter_reason(title, through_date, cutoff, through_date))
        self.assertEqual(
            "before_cutoff",
            crawler.entry_filter_reason(title, date(2026, 7, 5), cutoff, through_date),
        )
        self.assertEqual(
            "after_today",
            crawler.entry_filter_reason(title, date(2026, 8, 7), cutoff, through_date),
        )
        self.assertEqual(
            "keyword_mismatch",
            crawler.entry_filter_reason("招聘结果公示", cutoff, cutoff, through_date),
        )

    def test_query_uses_one_server_keyword_but_local_filter_remains_strict(self) -> None:
        query = crawler.build_query(2, 50)

        self.assertEqual(2, query["page"])
        self.assertEqual(50, query["pageSize"])
        title_conditions = [
            item
            for item in query["and"]  # type: ignore[union-attr]
            if item["fieldName"] == "title"
        ]
        self.assertEqual(
            [
                {
                    "fieldName": "title",
                    "criteriaType": "like",
                    "queryFieldValue": "公告",
                    "queryFieldValueType": "string",
                }
            ],
            title_conditions,
        )


class BeijingPortalItemTests(unittest.TestCase):
    def test_parses_body_metadata_and_official_attachment_mapping(self) -> None:
        item = api_item(
            "term-001",
            "北京市公开招聘教师公告",
            "2026-07-06 14:42:00",
            attachments="https://rsj.beijing.gov.cn/xxgk/gkzp/202607/jobs.xlsx",
            t1="招聘岗位表",
            c1="https://rsj.beijing.gov.cn/xxgk/gkzp/202607/form.docx",
            t3="报名表",
            l4="https://www.bjhr.gov.cn/files/commitment.pdf",
            c2="诚信承诺书",
        )

        entry = crawler.parse_api_item(item)

        self.assertEqual("term-001", entry.term_id)
        self.assertEqual(date(2026, 7, 6), entry.published_date)
        self.assertEqual("北京市测试单位", entry.publisher)
        self.assertEqual("第一段正文。\n第二段正文。", entry.content)
        self.assertEqual(
            "https://www.beijing.gov.cn/fwcj/quickQuery/page/zhaopin/detail.html?id=term-001",
            entry.detail_url,
        )
        self.assertEqual(["招聘岗位表", "报名表", "诚信承诺书"], [item.display_name for item in entry.attachments])
        self.assertEqual([], entry.invalid_attachment_urls)

    def test_html_body_fallback_ignores_scripts(self) -> None:
        item = api_item("term-002", "招聘公告", "2026-08-01")
        item["content"] = ""
        item["contentHtml"] = "<p>第一段</p><script>不要保存</script><p>第二段</p>"

        entry = crawler.parse_api_item(item)

        self.assertEqual("第一段\n第二段", entry.content)
        self.assertNotIn("不要保存", entry.content)

    def test_rejects_unsafe_attachment_before_any_download(self) -> None:
        item = api_item(
            "term-003",
            "招聘公告",
            "2026-08-01",
            attachments="https://example.com/private.xlsx",
            t1="外部文件",
        )

        entry = crawler.parse_api_item(item)

        self.assertEqual([], entry.attachments)
        self.assertEqual(["https://example.com/private.xlsx"], entry.invalid_attachment_urls)

    def test_publish_timestamp_fallback_converts_to_beijing_date(self) -> None:
        item = api_item("term-004", "招聘公告", "")
        item["publishTime"] = "2026-08-05T23:30:00+00:00"

        entry = crawler.parse_api_item(item)

        self.assertEqual(date(2026, 8, 6), entry.published_date)


class BeijingPortalCollectionTests(unittest.TestCase):
    def test_collects_all_pages_and_filters_before_processing_articles(self) -> None:
        responses = [
            api_response(
                [
                    api_item("selected", "公开招聘教师公告", "2026-07-06"),
                    api_item("keyword-skip", "公开招聘教师公示", "2026-07-20"),
                    api_item("future", "招聘公告", "2026-08-07"),
                ],
                total_pages=2,
                total_items=4,
            ),
            api_response(
                [api_item("old", "招聘公告", "2026-07-05")],
                total_pages=2,
                total_items=4,
            ),
        ]
        client = FakePortalClient(responses)
        with TemporaryDirectory() as temporary_directory:
            result = crawler.collect_entries(
                client=client,  # type: ignore[arg-type]
                logger=SilentLogger(),  # type: ignore[arg-type]
                list_url=crawler.DEFAULT_LIST_URL,
                cutoff=date(2026, 7, 6),
                through_date=date(2026, 8, 6),
                output_dir=Path(temporary_directory),
            )

            self.assertEqual(["selected"], [entry.term_id for entry in result.entries])
            self.assertEqual(2, result.pages_fetched)
            self.assertEqual(1, result.skipped_by_keywords)
            self.assertEqual(1, result.skipped_after_today)
            self.assertEqual(1, result.skipped_before_cutoff)
            self.assertEqual("all_pages_scanned", result.stop_reason)
            self.assertEqual([0, 1], [request[1]["page"] for request in client.requests])
            self.assertTrue((Path(temporary_directory) / "api-pages" / "page-001.json").is_file())
            self.assertTrue((Path(temporary_directory) / "api-pages" / "page-002.json").is_file())

    def test_zero_matches_is_a_successful_business_result(self) -> None:
        self.assertEqual(("completed", 0), crawler.determine_run_outcome(0, 0, 0, 0))
        self.assertEqual(("completed_with_errors", 0), crawler.determine_run_outcome(2, 1, 1, 1))
        self.assertEqual(("failed", 1), crawler.determine_run_outcome(2, 0, 2, 2))


class BeijingPortalValidationTests(unittest.TestCase):
    def test_url_validators_are_restricted_to_expected_services(self) -> None:
        self.assertTrue(crawler.is_configured_list_url(crawler.DEFAULT_LIST_URL))
        self.assertTrue(crawler.is_query_url(crawler.QUERY_URL))
        self.assertTrue(
            crawler.is_attachment_resource_url(
                "https://rsj.beijing.gov.cn/xxgk/gkzp/202607/file.xlsx"
            )
        )
        self.assertTrue(crawler.is_attachment_resource_url("https://www.bjhr.gov.cn/files/file.pdf"))
        self.assertFalse(crawler.is_attachment_resource_url("https://example.com/file.xlsx"))
        self.assertFalse(crawler.is_attachment_resource_url("http://rsj.beijing.gov.cn/file.xlsx"))
        self.assertFalse(crawler.is_attachment_resource_url("https://rsj.beijing.gov.cn/article.html"))

    def test_query_response_rejects_application_errors(self) -> None:
        with self.assertRaisesRegex(ValueError, "rejected"):
            crawler.parse_query_response(
                {"success": False, "code": 5000, "message": "失败", "data": []}
            )

    def test_argument_defaults_keep_recent_month_and_five_megabyte_limit(self) -> None:
        args = crawler.build_argument_parser().parse_args(
            ["--output-dir", "out", "--run-id", "test-run"]
        )

        self.assertEqual(0, args.lookback_days)
        self.assertEqual(100, args.page_size)
        self.assertEqual(5, args.max_attachment_mb)
        self.assertEqual(("招聘", "公告"), crawler.REQUIRED_TITLE_KEYWORDS)
        crawler.validate_arguments(args)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import unittest
from datetime import date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from crawlers.lib import community_recruitment as c


class FakeClient:
    def __init__(self, pages: dict[str, str | Exception], search_pages=None) -> None:
        self.pages = pages
        self.requested: list[str] = []
        self.search_pages = search_pages or {}
        self.search_params: list[dict] = []

    def get(self, url: str, referer=None):
        self.requested.append(url)
        page = self.pages[url]
        if isinstance(page, Exception):
            raise page
        return c.common.HttpResponse(page.encode(), url, 200, {"content-type": "text/html; charset=utf-8"})

    def search(self, params: dict):
        self.search_params.append(params)
        payload = self.search_pages[int(params["page"])]
        if isinstance(payload, Exception):
            raise payload
        return c.common.HttpResponse(json.dumps(payload).encode(), c.SEARCH_ENDPOINT, 200,
                                     {"content-type": "application/json; charset=utf-8"})


def article_url(number: int) -> str:
    return c.CHAOYANG.url + f"{number:032x}.html"


def list_page(entries: list[tuple[str, str, str]], pages: int = 1) -> str:
    items = "".join(f'<li><span>[{published}]</span><a href="{url}" class="news">{title}</a></li>'
                    for title, url, published in entries)
    return f'<ul class="news_list">{items}</ul><div>共{pages}页</div>'


def article_page(title: str, text: str, published: str = "2026-10-13") -> str:
    return (f'<meta name="ArticleTitle" content="{title}"><meta name="PubDate" content="{published}">'
            f'<div class="content_article"><p>{text}</p></div>')


def record(number: int, title: str, text: str, published: str = "2026-10-13") -> dict:
    url = article_url(number)
    entry = c.Entry(title, url, published, c.CHAOYANG.url)
    article, links = c.parse_article(article_page(title, text, published), c.CHAOYANG, entry)
    args = c.build_argument_parser(c.CHAOYANG).parse_args(["--output-dir", "/tmp/result", "--run-id", "test"])
    args.script_name = "chaoyang_community_recruitment.py"
    return c.build_record(c.CHAOYANG, entry, article, links, [], args,
                          datetime(2026, 10, 13, 10, tzinfo=c.TZ), Path("/tmp/result"))


class ParserTests(unittest.TestCase):
    def test_dongcheng_notices_keep_full_titles_unknown_dates_and_real_pins(self):
        url = c.DONGCHENG.url
        html = '''<ul class="list">
          <li class="col-md" data-zd=""><i>【置顶】</i><a href="./202610/t20261013_1.html"
             title="东城区2027年公开招考社区工作者公告">截断...</a><span>2026-10-13</span></li>
          <li class="col-md" data-zd="置顶"><a href="./202501/t20250102_2.html">旧公告</a><span>2025-01-02</span></li>
          <li class="col-md"><a href="./202610/t20261013_3.html">未知日期招聘公告</a></li>
          <li class="col-md"><a href="https://example.com/a.html">站外链接</a></li>
        </ul><script>Pager({size:40, current:0});</script>'''
        entries, total = c.parse_list(html, c.DONGCHENG, url)
        self.assertEqual(total, 40)
        self.assertEqual(len(entries), 3)
        self.assertEqual(entries[0].title, "东城区2027年公开招考社区工作者公告")
        self.assertFalse(entries[0].pinned)  # Hidden decorative <i> is not a real pin.
        self.assertTrue(entries[1].pinned)
        self.assertIsNone(entries[2].published_date)

    def test_article_retains_body_links_and_only_official_attachments(self):
        entry = c.Entry("招聘", c.DONGCHENG.url + "202610/t20261013_1.html", "2026-10-13", c.DONGCHENG.url)
        html = '''<a href="/outside.xlsx">外部导航</a><div id="mainText"><div class="view">
          <p>社区工作者招聘。<a href="https://www.gongzhao.net/">报名入口</a></p>
          <script>不应进入正文</script><div id="filerider"><a href="./jobs.doc">岗位表</a></div>
        </div></div>'''
        article, links = c.parse_article(html, c.DONGCHENG, entry)
        self.assertNotIn("不应进入正文", article.text)
        self.assertEqual(len(article.attachments), 1)
        self.assertIn("https://www.gongzhao.net/", links)
        self.assertEqual(article.attachments[0].url, c.DONGCHENG.url + "202610/jobs.doc")

    def test_broken_lists_are_errors_instead_of_zero_success(self):
        with self.assertRaises(ValueError):
            c.parse_list("<h1>访问失败</h1>", c.CHAOYANG, c.CHAOYANG.url)
        with self.assertRaises(ValueError):
            c.parse_list('<ul class="news_list"></ul>共5页', c.CHAOYANG, c.CHAOYANG.url)

    def test_stages_and_replacement_are_not_new_applications(self):
        cases = {
            "2027年社区工作者招聘公告": "recruitment",
            "2027年社区工作者笔试通知": "exam",
            "2027年社区工作者面试成绩公告": "scores",
            "2027年社区工作者拟录用人员公告": "result",
            "2027年社区工作者补录人员公告": "replacement",
            "2027年社区工作者报名延期公告": "extension",
            "2027年社区工作者招聘取消公告": "cancellation",
            "2027年社区工作者招聘体检公告": "assessment",
            "面向优秀社区工作者定向招聘事业编制人员公告": None,
            "社区工作者培训通知": None,
            "社会工作者职业资格考试公告": None,
        }
        for title, expected in cases.items():
            self.assertEqual(c.classify(title, "按原考试成绩依次递补"), expected, title)
        self.assertEqual(c.classify("社区工作者补录公告", "报名时间为2027年1月5日至8日"), "recruitment")
        self.assertEqual(c.recruitment_year("2026年底发布：2027年公开招考社区工作者公告"), 2027)

    def test_wps_attachments_are_accepted_by_the_shared_downloader(self):
        url = "http://www.bjchy.gov.cn/UserFiles/File/statement.wps"
        self.assertTrue(c.CHAOYANG.allowed(url, "attachment"))
        c.common.validate_attachment_payload("statement.wps", "application/octet-stream", 20, b"{\\rtf")


class ApplicationTests(unittest.TestCase):
    def test_explicit_cross_year_and_omitted_month(self):
        windows = c.parse_windows("报名时间：2026年12月28日9:00至2027年1月5日17:00。", date(2026, 12, 20))
        self.assertEqual(windows[0]["end"]["value"], "2027-01-05T17:00:00+08:00")
        self.assertEqual(c.application_status(windows, datetime(2027, 1, 2, 10, tzinfo=c.TZ)), "open")
        windows = c.parse_windows("报名时间：2027年1月5日10:00至8日16:00。", date(2026, 12, 20))
        self.assertEqual(windows[0]["end"]["value"], "2027-01-08T16:00:00+08:00")
        self.assertEqual(c.application_status(windows, datetime(2027, 1, 8, 17, tzinfo=c.TZ)), "closed")

    def test_date_only_deadline_is_unknown_on_its_final_day(self):
        windows = c.parse_windows("报名时间：2027年1月5日至8日。", date(2027, 1, 1))
        self.assertEqual(c.application_status(windows, datetime(2027, 1, 4, 10, tzinfo=c.TZ)), "upcoming")
        self.assertEqual(c.application_status(windows, datetime(2027, 1, 6, 10, tzinfo=c.TZ)), "open")
        self.assertEqual(c.application_status(windows, datetime(2027, 1, 8, 10, tzinfo=c.TZ)), "unknown")
        self.assertEqual(c.application_status(windows, datetime(2027, 1, 9, 10, tzinfo=c.TZ)), "closed")

    def test_daily_in_person_slots_and_lunch_break(self):
        windows = c.parse_windows("报名时间：2026年10月15日至16日，上午9:00—11:00、下午14:00—16:30。", date(2026, 10, 12))
        self.assertEqual(len(windows[0]["daily_hours"]), 2)
        for hour, expected in ((10, "open"), (12, "upcoming"), (15, "open"), (17, "closed")):
            self.assertEqual(c.application_status(windows, datetime(2026, 10, 16, hour, tzinfo=c.TZ)), expected)

    def test_missing_or_ambiguous_year_and_opening_are_not_invented(self):
        self.assertEqual(c.parse_windows("报名时间：12月28日至1月5日。", date(2026, 12, 20)), [])
        self.assertEqual(c.application_status([], datetime(2027, 1, 1, tzinfo=c.TZ)), "unknown")
        windows = c.parse_windows("报名截止：2027年1月8日17:00。", date(2027, 1, 1))
        self.assertEqual(c.application_status(windows, datetime(2027, 1, 6, tzinfo=c.TZ)), "unknown")

    def test_separate_start_and_end_lines(self):
        windows = c.parse_windows("报名开始：2027年1月5日9:00。\n报名截止：2027年1月8日17:00。", date(2027, 1, 1))
        self.assertEqual(len(windows), 1)
        self.assertEqual(c.application_status(windows, datetime(2027, 1, 6, tzinfo=c.TZ)), "open")

    def test_qualification_query_deadline_does_not_extend_registration(self):
        text = ("1.提交报考申请。报考者可于2026年6月9日9:00至6月11日17:00进行报名。\n"
                "2.网上资格审核。报考者可于2026年6月12日17:00前登录报名系统查询是否通过资格审核。报名时间截止后不得改报。")
        windows = c.parse_windows(text, date(2026, 6, 2))
        self.assertEqual(len(windows), 1)
        self.assertEqual(c.application_status(windows, datetime(2026, 6, 12, 10, tzinfo=c.TZ)), "closed")

    def test_publication_year_is_not_confused_with_next_recruitment_year(self):
        self.assertEqual(c.parse_windows("报名时间：1月5日至8日。", date(2026, 12, 20), 2027), [])
        windows = c.parse_windows("报名时间：2027年1月5日至8日。", date(2026, 12, 20), 2027)
        self.assertEqual(windows[0]["start"]["value"], "2027-01-05")

    def test_unambiguously_related_extension_and_cancel(self):
        title = "北京市朝阳区2026年度公开招聘社区工作者公告"
        original = record(1, title, "报名时间：2026年10月10日9:00至12日17:00。", "2026-10-09")
        extension = record(2, title.replace("公告", "报名延期公告"), "报名截止延长至2026年10月16日17:00。")
        catalog = c.build_catalog([original, extension], datetime(2026, 10, 13, 10, tzinfo=c.TZ))
        self.assertEqual(catalog["campaigns"][0]["application_status"], "open")
        cancel = record(3, title.replace("公告", "取消公告"), "取消本次招聘。", "2026-10-14")
        catalog = c.build_catalog([original, extension, cancel], datetime(2026, 10, 15, 10, tzinfo=c.TZ))
        self.assertEqual(catalog["campaigns"][0]["application_status"], "cancelled")

    def test_same_title_different_plan_is_not_merged_or_extended(self):
        title = "2026年社区工作者招聘公告"
        a = record(1, title, "甲单位社区工作者；报名时间：2026年10月10日至20日。")
        b = record(2, title, "乙单位社区工作者；报名时间：2026年10月10日至20日。")
        extension = record(3, "2026年社区工作者招聘报名延期公告", "报名截止：2026年11月1日17:00。")
        catalog = c.build_catalog([a, b, extension], datetime(2026, 10, 13, tzinfo=c.TZ))
        self.assertEqual(len(catalog["campaigns"]), 2)
        self.assertEqual(len(catalog["unlinked_events"]), 1)
        self.assertTrue(all(p["application_status"] == "unknown" for p in catalog["campaigns"]))

    def test_conflicting_same_day_changes_are_unknown_and_cancellation_stays_cancelled(self):
        title = "2026年社区工作者招聘公告"
        original = record(1, title, "报名时间：2026年10月10日9:00至12日17:00。")
        first = record(2, title.replace("公告", "报名延期公告"), "报名截止延长至2026年10月16日17:00。")
        second = record(3, title.replace("公告", "报名延期公告"), "报名截止延长至2026年10月18日17:00。")
        now = datetime(2026, 10, 15, tzinfo=c.TZ)
        plan = c.build_catalog([original, first, second], now)["campaigns"][0]
        self.assertEqual(plan["application_status"], "unknown")
        self.assertIn("conflicting_application_changes", plan["warnings"])
        cancel = record(4, title.replace("公告", "取消公告"), "取消本次招聘。", "2026-10-12")
        plan = c.build_catalog([original, cancel, first], now)["campaigns"][0]
        self.assertEqual(plan["application_status"], "cancelled")


class RunTests(unittest.TestCase):
    def run_fixture(self, root: Path, output_name: str, client: FakeClient, **options):
        site = options.pop("site", c.CHAOYANG)
        args = c.build_argument_parser(site).parse_args([
            "--output-dir", str(root / output_name), "--run-id", output_name,
            "--state-dir", str(root / "state"), "--log-level", "ERROR"])
        for key, value in options.items():
            setattr(args, key, value)
        logger = c.common.CrawlerLogger("ERROR")
        try:
            code = c.run(site, args, logger, client, datetime(2026, 10, 13, 10, tzinfo=c.TZ))
        finally:
            logger.close()
        summary = json.loads((root / output_name / "summary.json").read_text())
        return code, summary

    def test_beyond_latest_ten_then_incremental_dedup_and_update(self):
        entries = [(f"一般公告{i}", article_url(i), "2026-10-13") for i in range(1, 12)]
        title = "朝阳区2026年社区工作者招聘公告"
        url = article_url(12)
        pages = {c.CHAOYANG.url: list_page(entries, 2),
                 c.CHAOYANG.url + "index_1.html": list_page([(title, url, "2026-10-13")], 2),
                 url: article_page(title, "报名时间：2026年10月15日9:00至16日17:00。")}
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            code, summary = self.run_fixture(root, "first", FakeClient(pages))
            self.assertEqual(code, 0)
            self.assertEqual(summary["new_announcements"], 1)
            self.assertEqual(summary["campaign_counts"]["upcoming"], 1)
            _, second = self.run_fixture(root, "second", FakeClient(pages))
            self.assertEqual(second["new_announcements"], 0)
            self.assertEqual(second["updated_announcements"], 0)
            self.assertEqual(second["sources"][0]["from_date"], "2026-10-06")
            pages[url] = article_page(title, "报名时间：2026年10月15日9:00至17日17:00。")
            _, third = self.run_fixture(root, "third", FakeClient(pages))
            self.assertEqual(third["updated_announcements"], 1)

    def test_limit_does_not_advance_watermark_and_pending_detail_survives(self):
        title = "朝阳区2026年社区工作者招聘公告"
        url = article_url(1)
        pages = {c.CHAOYANG.url: list_page([(title, url, "2026-10-13")], 2),
                 url: RuntimeError("暂时不可用")}
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            code, summary = self.run_fixture(root, "first", FakeClient(pages), max_pages=1)
            self.assertEqual(code, 1)
            self.assertFalse(summary["coverage_complete"])
            state = c.State(root / "state" / "chaoyang.sqlite")
            self.assertIsNone(state.watermark(c.CHAOYANG.url))
            self.assertEqual(state.rows()[0]["pending"], 1)
            state.close()
            pages[c.CHAOYANG.url] = list_page([("一般通知", article_url(2), "2026-10-13")])
            pages[url] = article_page(title, "报名时间：2026年10月15日至16日。")
            code, second = self.run_fixture(root, "second", FakeClient(pages))
            self.assertEqual(code, 0)
            self.assertEqual(second["new_announcements"], 1)

    def test_healthy_zero_matches_succeeds(self):
        with TemporaryDirectory() as tmp:
            code, summary = self.run_fixture(Path(tmp), "zero", FakeClient({c.CHAOYANG.url:
                list_page([("一般通知", article_url(1), "2026-10-13")])}))
            self.assertEqual(code, 0)
            self.assertEqual(summary["record_count"], 0)
            self.assertTrue(summary["coverage_complete"])

    def test_archived_dongcheng_plan_is_found_through_official_search(self):
        title = "东城区2025年公开招考社区工作者公告"
        archived = c.DONGCHENG.url + "202501/t20250102_3978856.html"
        recent = c.DONGCHENG.url + "202610/t20261013_1.html"
        institution = "https://www.bjdch.gov.cn" + c.DONGCHENG.sections[1]
        pages = {c.DONGCHENG.url: list_page([("一般公告", recent, "2026-10-13")]),
                 institution: list_page([("一般公告", institution + "202610/t20261013_2.html", "2026-10-13")]),
                 archived: article_page(title, "报名时间：2025年1月6日10:00至10日16:00。", "2025-01-02")}
        search = {1: {"ok": True, "totalHits": 2, "currentHits": 1, "resultDocs": [
            {"id": "news", "data": {"url": "https://www.bjdch.gov.cn/ywdt/news.html", "titleO": "社区工作者培训", "docDate": "2026-10-13"}}]},
                  2: {"ok": True, "totalHits": 2, "currentHits": 1, "resultDocs": [
            {"id": "archived", "data": {"url": archived, "titleO": title, "docDate": "2025-01-02"}}]}}
        with TemporaryDirectory() as tmp:
            client = FakeClient(pages, search)
            code, summary = self.run_fixture(Path(tmp), "archived", client, site=c.DONGCHENG)
            self.assertEqual(code, 0)
            self.assertEqual(summary["new_announcements"], 1)
            self.assertEqual(summary["campaign_counts"]["closed"], 1)
            self.assertEqual(summary["sources"][-1]["pages_fetched"], 2)
            self.assertEqual(client.search_params[0]["startDateStr"], "2024-10-13")
            self.assertTrue((Path(tmp) / "archived/search-pages/002.json").exists())
            search[1] = {"ok": True, "totalHits": 0, "currentHits": 0, "resultDocs": []}
            second_client = FakeClient(pages, search)
            code, second = self.run_fixture(Path(tmp), "incremental", second_client, site=c.DONGCHENG)
            self.assertEqual(code, 0)
            self.assertEqual(second["new_announcements"], 0)
            self.assertEqual(second_client.search_params[0]["startDateStr"], "2026-10-06")
            self.assertEqual(second["campaign_counts"]["closed"], 1)

    def test_search_limit_loop_and_error_do_not_advance_search_watermark(self):
        url = c.DONGCHENG.url + "202610/t20261013_1.html"
        title = "社区工作者招聘公告"
        payload = {"ok": True, "totalHits": 2, "currentHits": 1,
                   "resultDocs": [{"id": "one", "data": {"url": url, "titleO": title, "docDate": "2026-10-13"}}]}
        for second_page, limit, reason in ((payload, 1, "max_pages"),
                                           (payload, 2, "pagination_loop_or_missing_results"),
                                           (RuntimeError("搜索临时不可用"), 2, "failed")):
            with self.subTest(reason=reason), TemporaryDirectory() as tmp:
                institution = "https://www.bjdch.gov.cn" + c.DONGCHENG.sections[1]
                pages = {c.DONGCHENG.url: list_page([("一般公告", url, "2026-10-13")]),
                         institution: list_page([("一般公告", institution + "202610/t20261013_2.html", "2026-10-13")]),
                         url: article_page(title, "报名时间：2026年10月15日至16日。")}
                code, summary = self.run_fixture(Path(tmp), "partial", FakeClient(pages, {1: payload, 2: second_page}),
                                                 site=c.DONGCHENG, max_pages=limit)
                self.assertEqual(code, 1)
                self.assertEqual(summary["sources"][-1]["stop_reason"], reason)
                self.assertEqual(summary["new_announcements"], 1)  # Discovered work survives a partial scan.
                state = c.State(Path(tmp) / "state/dongcheng.sqlite")
                self.assertIsNone(state.watermark(c.SEARCH_ENDPOINT))
                state.close()


class SearchTests(unittest.TestCase):
    def test_invalid_search_payload_is_not_a_successful_zero(self):
        for payload in ({"ok": False}, {"ok": True, "totalHits": 10, "currentHits": 0},
                        {"ok": True, "totalHits": 1, "currentHits": 1, "resultDocs": [{}]}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                c.parse_search(c.common.HttpResponse(json.dumps(payload).encode(), c.SEARCH_ENDPOINT, 200, {}))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from datetime import date

from crawlers import beijing_chaoyang_recruitment as crawler


LIST_URL = "http://www.bjchy.gov.cn/affair/civilemployed/zhk/"
ARTICLE_URL = (
    "http://www.bjchy.gov.cn/affair/civilemployed/zhk/"
    "4028805a9f737e9b019fd00eed2e51b6.html"
)


class ChaoyangRecruitmentParserTests(unittest.TestCase):
    def test_decodes_declared_gb2312_as_gb18030(self) -> None:
        html = '<meta http-equiv="Content-Type" content="text/html; charset=gb2312">朝阳区招聘'
        response = crawler.HttpResponse(
            body=html.encode("gb18030"),
            final_url=LIST_URL,
            status=200,
            headers={"content-type": "text/html"},
        )

        self.assertIn("朝阳区招聘", crawler.decode_html(response))

    def test_parses_list_entries_in_document_order_and_trims_href(self) -> None:
        html = """
        <ul class="news_list">
          <li><i></i><span>[2026-08-05]</span>
            <a class="news" href="  4028805a9f737e9b019fd00eed2e51b6.html  ">第一篇</a>
          </li>
          <li><span>[2026-08-03]</span>
            <a href="4028805a9f737e9b019fc6c25aab430a.html" class="news">第二篇</a>
          </li>
          <li><span>[2026-08-01]</span><a class="other" href="outside.html">忽略</a></li>
        </ul>
        """

        entries, found_list = crawler.parse_list_page(html, LIST_URL)

        self.assertTrue(found_list)
        self.assertEqual(2, len(entries))
        self.assertEqual("第一篇", entries[0][0])
        self.assertEqual(ARTICLE_URL, entries[0][1])
        self.assertEqual(date(2026, 8, 5), entries[0][2])
        self.assertEqual("第二篇", entries[1][0])

    def test_parses_article_metadata_text_and_only_body_attachments(self) -> None:
        html = """
        <html><head>
          <meta name="ArticleTitle" content="北京市朝阳区公开招聘公告">
          <meta name="PubDate" content="2026-08-05">
          <meta name="ContentSource" content="北京市朝阳区人力资源和社会保障局">
        </head><body>
          <h1 class="content_title">备用标题</h1>
          <div class="sub_title2_left">日期：2026-08-05 11:52 来源：备用来源 打印页面</div>
          <a href="/UserFiles/File/outside.pdf">正文外附件应忽略</a>
          <div class="content_article">
            <p>第一段正文。</p>
            <p><a href=" /UserFiles/File/aabbcc.xlsx ">附件：1.岗位信息目录</a></p>
            <p><a href="/UserFiles/File/guide.docx">2.报考指南</a></p>
            <p><a href="javascript:window.print()">打印</a></p>
            <script>should_not_be_visible()</script>
          </div>
        </body></html>
        """

        article, found_content = crawler.parse_article_page(html, ARTICLE_URL)

        self.assertTrue(found_content)
        self.assertEqual("北京市朝阳区公开招聘公告", article.title)
        self.assertEqual(date(2026, 8, 5), article.published_date)
        self.assertEqual("北京市朝阳区人力资源和社会保障局", article.source)
        self.assertIn("第一段正文。", article.text)
        self.assertNotIn("should_not_be_visible", article.text)
        self.assertEqual(2, len(article.attachments))
        self.assertEqual("1.岗位信息目录", article.attachments[0].display_name)
        self.assertEqual(
            "http://www.bjchy.gov.cn/UserFiles/File/aabbcc.xlsx",
            article.attachments[0].url,
        )

    def test_restricts_urls_to_the_expected_http_site_and_paths(self) -> None:
        self.assertTrue(crawler.is_list_url(LIST_URL))
        self.assertTrue(crawler.is_article_url(ARTICLE_URL))
        self.assertTrue(
            crawler.is_attachment_url("http://www.bjchy.gov.cn/UserFiles/File/example.pdf")
        )
        self.assertFalse(crawler.is_list_url(LIST_URL.replace("http://", "https://")))
        self.assertFalse(crawler.is_article_url(ARTICLE_URL.replace("www.", "")))
        self.assertFalse(crawler.is_attachment_url("http://www.bjchy.gov.cn/other/example.pdf"))

    def test_safe_filename_preserves_extension_and_windows_compatibility(self) -> None:
        self.assertEqual("岗位_信息表.xlsx", crawler.safe_filename("岗位:信息表", ".xlsx"))
        self.assertEqual("_CON.pdf", crawler.safe_filename("CON", ".pdf"))

    def test_rejects_html_disguised_as_an_attachment(self) -> None:
        with self.assertRaisesRegex(ValueError, "contains HTML"):
            crawler.validate_attachment_payload(
                "report.xlsx",
                "text/html; charset=gb2312",
                100,
                b"<!DOCTYPE html>",
            )


if __name__ == "__main__":
    unittest.main()

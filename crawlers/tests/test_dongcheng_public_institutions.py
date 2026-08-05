from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from crawlers import dongcheng_public_institutions as crawler


class DongchengListParserTests(unittest.TestCase):
    def test_parses_articles_in_list_order(self) -> None:
        html = """
        <ul class="list">
          <li class="col-md" data-zd="">
            <i>【置顶】</i>
            <a href="./202608/t20260803_4806203.html" title="第一篇文章">第一篇文章</a>
            <span>2026-08-03</span>
          </li>
          <li class="col-md" data-zd="">
            <a href="./202607/t20260731_4802843.html">第二篇文章</a>
            <span>2026-07-31</span>
          </li>
        </ul>
        """

        entries = crawler.parse_list_page(html, crawler.DEFAULT_LIST_URL)

        self.assertEqual(2, len(entries))
        self.assertEqual("第一篇文章", entries[0].title)
        self.assertEqual("2026-08-03", entries[0].published_date.isoformat())
        self.assertEqual(
            "https://www.bjdch.gov.cn/zwgk/rsxx/sydwzlxx/202607/t20260731_4802843.html",
            entries[1].url,
        )

    def test_ignores_external_and_malformed_entries(self) -> None:
        html = """
        <ul class="list">
          <li class="col-md"><a href="https://example.com/a.html">站外链接</a><span>2026-08-03</span></li>
          <li class="col-md"><a href="./not-an-article.html">错误路径</a><span>2026-08-03</span></li>
          <li class="col-md"><a href="./202608/t20260803_4806203.html">缺少日期</a></li>
        </ul>
        """

        self.assertEqual([], crawler.parse_list_page(html, crawler.DEFAULT_LIST_URL))


class DongchengArticleParserTests(unittest.TestCase):
    def test_parses_metadata_body_and_attachments(self) -> None:
        article_url = (
            "https://www.bjdch.gov.cn/zwgk/rsxx/sydwzlxx/"
            "202608/t20260803_4806203.html"
        )
        html = """
        <html><head>
          <meta name="ArticleTitle" content="公开招聘拟聘人员公示">
          <meta name="PubDate" content="2026-08-03 14:58">
          <meta name="ContentSource" content="东城区人民政府网站">
        </head><body>
          <div id="mainText">
            <div class="view TRS_UEDITOR">
              <p>第一段正文。</p>
              <p>第二段<br>换行内容。</p>
              <script>这段脚本不能进入正文</script>
            </div>
            <div id="filerider">
              <li>附件：<a href="./P020260803540216531230.xls">拟聘人员名单</a></li>
              <li>附件：<a href="./P020260803540216531231.pdf">补充说明</a></li>
            </div>
          </div>
        </body></html>
        """

        article = crawler.parse_article_page(html, article_url)

        self.assertEqual("公开招聘拟聘人员公示", article.title)
        self.assertEqual("2026-08-03", article.published_date.isoformat())
        self.assertEqual("东城区人民政府网站", article.source)
        self.assertIn("第一段正文。", article.text)
        self.assertIn("第二段", article.text)
        self.assertNotIn("这段脚本不能进入正文", article.text)
        self.assertEqual(2, len(article.attachments))
        self.assertEqual("拟聘人员名单", article.attachments[0].display_name)
        self.assertTrue(article.attachments[0].url.endswith("P020260803540216531230.xls"))

    def test_attachment_validator_restricts_host_section_and_extension(self) -> None:
        self.assertTrue(
            crawler.is_attachment_resource_url(
                "https://www.bjdch.gov.cn/zwgk/rsxx/sydwzlxx/202608/file.xlsx"
            )
        )
        self.assertFalse(
            crawler.is_attachment_resource_url(
                "https://example.com/zwgk/rsxx/sydwzlxx/202608/file.xlsx"
            )
        )
        self.assertFalse(
            crawler.is_attachment_resource_url(
                "https://www.bjdch.gov.cn/other-section/file.xlsx"
            )
        )
        self.assertFalse(
            crawler.is_attachment_resource_url(
                "https://www.bjdch.gov.cn/zwgk/rsxx/sydwzlxx/202608/page.html"
            )
        )


class DongchengArgumentsTests(unittest.TestCase):
    def test_defaults_to_ten_articles_and_five_megabytes(self) -> None:
        args = crawler.build_argument_parser().parse_args(
            ["--output-dir", "out", "--run-id", "test-run"]
        )

        self.assertEqual(10, args.max_articles)
        self.assertEqual(5, args.max_attachment_mb)
        crawler.validate_arguments(args)

    def test_atomic_text_writer_supports_the_configured_python_version(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            target = Path(temporary_directory) / "nested" / "article.html"

            crawler.atomic_write_text(target, "测试内容\n")

            self.assertEqual("测试内容\n", target.read_text(encoding="utf-8"))
            self.assertFalse(target.with_name("article.html.tmp").exists())


if __name__ == "__main__":
    unittest.main()

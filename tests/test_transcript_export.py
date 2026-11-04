from html.parser import HTMLParser
import unittest

from transcript_export import render_transcript


class ExportTests(unittest.TestCase):
    def test_txt_keeps_existing_merged_structure(self):
        self.assertEqual(render_transcript([" Prima ", "Seconda"], merged=True),
                         "-----------------\nPart 1\n-----------------\n\nPrima\n\n"
                         "-----------------\nPart 2\n-----------------\n\nSeconda\n")

    def test_markdown_has_level_one_part_headings(self):
        self.assertEqual(render_transcript(["Prima", "Seconda"], "md", merged=True),
                         "# Part 1\n\nPrima\n\n# Part 2\n\nSeconda\n")

    def test_single_plain_exports_do_not_add_part_headings(self):
        for output_format in ("txt", "md"):
            with self.subTest(output_format=output_format):
                self.assertEqual(render_transcript([" È una lezione.\n\nSecondo paragrafo. "], output_format),
                                 "È una lezione.\n\nSecondo paragrafo.\n")

    def test_html_preserves_text_and_escapes_markup(self):
        class DocumentParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.tags, self.text = [], []

            def handle_starttag(self, tag, attrs):
                self.tags.append(tag)

            def handle_data(self, data):
                self.text.append(data)

        text = 'È <script>alert("x")</script> & 2 < 3\n\nNuovo paragrafo.'
        html = render_transcript([text, "Fine"], "html", merged=True,
                                 title="Lezione <1> & 2", language="it")
        parser = DocumentParser()
        parser.feed(html)
        self.assertEqual(parser.tags.count("h1"), 2)
        self.assertNotIn("script", parser.tags)
        self.assertIn(text, "".join(parser.text))
        self.assertIn('<html lang="it">', html)
        self.assertIn("<title>Lezione &lt;1&gt; &amp; 2</title>", html)
        self.assertIn("@media print", html)

    def test_html_single_and_auto_language(self):
        html = render_transcript(["Testo"], "html")
        self.assertIn("<html>", html)
        self.assertNotIn("<h1>", html)

    def test_unsupported_format_is_rejected(self):
        with self.assertRaises(ValueError):
            render_transcript(["Testo"], "pdf")


if __name__ == "__main__":
    unittest.main()

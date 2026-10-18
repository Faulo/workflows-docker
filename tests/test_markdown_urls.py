import sys
import unittest
from pathlib import Path

from markdown_it import MarkdownIt

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from markdown_urls import rewrite_markdown

ROOT = Path(__file__).resolve().parents[1]
BLOB = "https://github.com/caller/source/blob/main/"
RAW = "https://raw.githubusercontent.com/caller/source/main/"


class MarkdownURLTests(unittest.TestCase):
    def rewrite(self, markdown):
        return rewrite_markdown(markdown, "caller/source", "main", ROOT)

    def tokens(self, markdown):
        result = []

        def visit(tokens):
            for token in tokens:
                result.append(token)
                visit(token.children or [])

        visit(
            MarkdownIt("commonmark").enable(["table", "strikethrough"]).parse(markdown)
        )
        return result

    def urls(self, markdown):
        return [
            token.attrGet("href") if token.type == "link_open" else token.attrGet("src")
            for token in self.tokens(markdown)
            if token.type in {"link_open", "image"}
        ]

    def test_repository_root_resolution_and_live_branch(self):
        source = "[a](guide.md) [b](./guide.md) [c](/guide.md) [d](docs/../guide.md) [e](../guide.md)"
        self.assertEqual([BLOB + "guide.md"] * 5, self.urls(self.rewrite(source)))
        self.assertEqual(
            ["https://github.com/caller/source/tree/main/examples"],
            self.urls(self.rewrite("[examples](examples)")),
        )

    def test_only_fragment_links_stay_relative(self):
        source = "[local](#part) [empty fragment](#) [elsewhere](guide.md#part) [query](?q=1#part) [empty]()"
        self.assertEqual(
            [
                "#part",
                "#",
                BLOB + "guide.md#part",
                "https://github.com/caller/source?q=1#part",
                "https://github.com/caller/source",
            ],
            self.urls(self.rewrite(source)),
        )

    def test_absolute_urls_and_schemes_survive_network_paths_become_absolute(self):
        source = "[site](https://example.com/a?q=1#part) [mail](mailto:a@example.com) ![badge](//example.com/badge.svg)"
        self.assertEqual(
            [
                "https://example.com/a?q=1#part",
                "mailto:a@example.com",
                "https://example.com/badge.svg",
            ],
            self.urls(self.rewrite(source)),
        )

    def test_linked_images_titles_and_queries(self):
        source = '[![Logo](images/logo.svg?v=2 "Logo")](guide.md#install "Guide")'
        result = self.rewrite(source)
        self.assertEqual(
            [BLOB + "guide.md#install", RAW + "images/logo.svg?v=2"], self.urls(result)
        )
        self.assertEqual(
            ["Guide", "Logo"],
            [
                t.attrGet("title")
                for t in self.tokens(result)
                if t.type in {"link_open", "image"}
            ],
        )

    def test_reference_variants_and_shared_link_image_reference(self):
        source = '[Guide][ Shared ] ![Logo][shared] ![shared][] [shared]\n\n[shared]: logo.svg "Title"\n'
        self.assertEqual(
            [BLOB + "logo.svg", RAW + "logo.svg", RAW + "logo.svg", BLOB + "logo.svg"],
            self.urls(self.rewrite(source)),
        )

    def test_inline_image_label_does_not_change_unrelated_reference(self):
        source = "![Same](real.png) [Guide][Same]\n\n[Same]: guide.md\n"
        self.assertEqual(
            [RAW + "real.png", BLOB + "guide.md"], self.urls(self.rewrite(source))
        )

    def test_destinations_with_spaces_parentheses_escapes_unicode_and_percent_encoding(
        self,
    ):
        source = "[Space][s] [Paren](a(b).md) [Escaped](a\\(b\\).md) [Unicode](grüße.md) [Encoded](a%20b.md)\n\n[s]: <a b.md>\n"
        expected = [
            BLOB + "a%20b.md",
            BLOB + "a(b).md",
            BLOB + "a(b).md",
            BLOB + "gr%C3%BC%C3%9Fe.md",
            BLOB + "a%20b.md",
        ]
        self.assertEqual(expected, self.urls(self.rewrite(source)))
        self.assertEqual(
            [BLOB + "a%2Fb.md"], self.urls(self.rewrite("[encoded slash](a%2Fb.md)"))
        )

    def test_list_continuations_nested_lists_quotes_tables_and_tasks(self):
        source = "- Details:\n    [guide](guide.md)\n\n    - [nested](nested.md)\n\n> [quote](quote.md)\n\n| Link |\n| --- |\n| [table](table.md) |\n\n- [x] ~~[task](task.md)~~\n"
        result = self.rewrite(source)
        self.assertEqual(
            [
                BLOB + name + ".md"
                for name in ("guide", "nested", "quote", "table", "task")
            ],
            self.urls(result),
        )
        self.assertIn("table_open", [t.type for t in self.tokens(result)])
        self.assertIn("s_open", [t.type for t in self.tokens(result)])
        self.assertIn("[x]", result)

    def test_code_and_escaped_literal_links_are_not_rewritten(self):
        source = (
            r"\[Example](guide.md)"
            + "\n\n`[inline](inline.md)`\n\n```md\n[example](code.md)\n````\n\n    [indented](indented.md)\n\n[actual](actual.md)\n"
        )
        before, after = self.tokens(source), self.tokens(self.rewrite(source))
        self.assertEqual([BLOB + "actual.md"], self.urls(self.rewrite(source)))
        self.assertEqual(
            [
                t.content
                for t in before
                if t.type in {"code_inline", "fence", "code_block"}
            ],
            [
                t.content
                for t in after
                if t.type in {"code_inline", "fence", "code_block"}
            ],
        )
        self.assertIn(
            "[Example](guide.md)", "".join(t.content for t in after if t.type == "text")
        )

    def test_html_is_parsed_including_unquoted_attributes_and_apostrophes(self):
        source = '<a href="guide.md?a=1&amp;b=2#part">read</a><img src="images/bob\'s.png"><img src=logo.png><a href="#part">local</a>'
        result = self.rewrite(source)
        self.assertIn(f'href="{BLOB}guide.md?a=1&amp;b=2#part"', result)
        self.assertIn(f'src="{RAW}images/bob&#x27;s.png"', result)
        self.assertIn(f'src="{RAW}logo.png"', result)
        self.assertIn('href="#part"', result)

    def test_html_comments_and_code_examples_remain_literal(self):
        source = '<!-- <img src="comment.png"> -->\n\n`<img src="inline.png">`\n\n```html\n<img src="code.png">\n```\n'
        result = self.rewrite(source)
        self.assertNotIn("github", result)
        self.assertIn('<!-- <img src="comment.png"> -->', result)

    def test_html_uri_components_are_encoded_and_non_url_attributes_are_untouched(self):
        result = self.rewrite(
            '<a href="guide.md?q=a b#some heading">read</a><div data="value">data</div>'
        )
        self.assertIn(f'href="{BLOB}guide.md?q=a%20b#some%20heading"', result)
        self.assertIn('<div data="value">data</div>', result)

    def test_missing_caller_identity_fails_clearly(self):
        for repository, branch in ((None, "main"), ("caller/source", None)):
            with self.assertRaisesRegex(ValueError, "calling"):
                rewrite_markdown("[a](a)", repository, branch, ROOT)


if __name__ == "__main__":
    unittest.main()

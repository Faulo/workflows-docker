"""Resolve documentation URLs in a parsed GitHub Flavored Markdown document."""

from html import escape
from html.parser import HTMLParser
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit

from configure import require


class RepositoryURLs:
    def __init__(self, repository, branch, root):
        require(
            isinstance(repository, str)
            and len(repository.split("/")) == 2
            and all(
                part and all(c.isalnum() or c in "_.-" for c in part)
                for part in repository.split("/")
            ),
            "overviewFile URL resolution requires the calling GITHUB_REPOSITORY",
        )
        require(
            isinstance(branch, str)
            and branch
            and not any(c in branch for c in "\r\n\0"),
            "overviewFile URL resolution requires the calling repository's default branch",
        )
        self.repository = repository
        self.branch = quote(branch, safe="")
        self.root = root

    def resolve(self, destination, image=False):
        # Markdown's parser has already decoded entities and backslash escapes.
        # URI resolution starts at the repository root, independent of the
        # overview's directory. urljoin performs RFC relative-path resolution.
        uri = urlsplit(destination)
        if uri.scheme or destination.startswith("#"):
            return destination
        if uri.netloc or destination.startswith("//"):
            return urljoin("https:", destination)
        resolved = urlsplit(urljoin("https://repository.invalid/", destination))
        path = quote(resolved.path.lstrip("/"), safe="/%:@!$&'()*+,;=-._~")
        if image:
            base = f"https://raw.githubusercontent.com/{self.repository}/{self.branch}/{path}"
        elif not path:
            base = f"https://github.com/{self.repository}"
        else:
            kind = "tree" if (self.root / unquote(path)).is_dir() else "blob"
            base = f"https://github.com/{self.repository}/{kind}/{self.branch}/{path}"
        return urlunsplit(
            (
                *urlsplit(base)[:3],
                quote(resolved.query, safe="/%?:@!$&'()*+,;=-._~"),
                quote(resolved.fragment, safe="/%?:@!$&'()*+,;=-._~"),
            )
        )


class HTMLURLs(HTMLParser):
    """Rewrite URL attributes only in HTML nodes recognized by Markdown's parser."""

    def __init__(self, urls):
        super().__init__(convert_charrefs=False)
        self.urls = urls
        self.output = []

    def start_tag(self, tag, attrs, closing):
        updated = []
        for name, value in attrs:
            if value is not None and (
                name
                in {
                    "href",
                    "src",
                    "poster",
                    "cite",
                    "action",
                    "formaction",
                }
                or (tag == "object" and name == "data")
            ):
                value = self.urls.resolve(value, image=name in {"src", "poster"})
            updated.append((name, value))
        if updated == attrs:
            self.output.append(self.get_starttag_text())
        else:
            attributes = "".join(
                f' {name}="{escape(value, quote=True)}"'
                if value is not None
                else f" {name}"
                for name, value in updated
            )
            self.output.append(f"<{tag}{attributes}{closing}>")

    def handle_starttag(self, tag, attrs):
        self.start_tag(tag, attrs, "")

    def handle_startendtag(self, tag, attrs):
        self.start_tag(tag, attrs, " /")

    def handle_endtag(self, tag):
        self.output.append(f"</{tag}>")

    def handle_data(self, data):
        self.output.append(data)

    def handle_entityref(self, name):
        self.output.append(f"&{name};")

    def handle_charref(self, name):
        self.output.append(f"&#{name};")

    def handle_comment(self, data):
        self.output.append(f"<!--{data}-->")

    def handle_decl(self, decl):
        self.output.append(f"<!{decl}>")

    def handle_pi(self, data):
        self.output.append(f"<?{data}>")


def rewrite_markdown(markdown, repository, branch, root):
    # Dependencies are needed only by the configuration job, which snapshots
    # the resulting Markdown. Native build/publication jobs need no parser.
    from mdformat._util import build_mdit
    from mdformat.renderer import MDRenderer

    urls = RepositoryURLs(repository, branch, root)
    parser = build_mdit(MDRenderer, extensions={"gfm"})
    # Resolve each reference use independently: one reference may be used both
    # as a link and an image, which need different GitHub endpoints.
    parser.options["store_labels"] = False
    environment = {}
    tokens = parser.parse(markdown, environment)

    def visit(nodes):
        for node in nodes:
            if node.type == "link_open":
                node.attrSet("href", urls.resolve(node.attrGet("href")))
            elif node.type == "image":
                node.attrSet("src", urls.resolve(node.attrGet("src"), image=True))
            elif node.type in {"html_inline", "html_block"}:
                html = HTMLURLs(urls)
                html.feed(node.content)
                html.close()
                node.content = "".join(html.output)
            if node.children:
                visit(node.children)

    visit(tokens)
    result = parser.renderer.render(tokens, parser.options, environment)
    return result if markdown.endswith("\n") else result.removesuffix("\n")

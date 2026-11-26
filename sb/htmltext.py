"""arXiv's HTML (LaTeXML) to sections, on html.parser: h2 and h3 start a section, paragraphs are its text."""
import re
from html.parser import HTMLParser

SKIP_TAGS = {"h1", "script", "style", "noscript", "nav", "header", "footer", "button", "svg", "table", "head", "select", "form"}
SKIP_CLASSES = {"ds-skip-link", "infobox", "ltx_page_header", "ltx_page_footer", "ltx_authors", "ltx_abstract", "ltx_dates", "ltx_role_affiliation", "ltx_bibblock_links",
                "ltx_pagination", "ltx_tag_bibitem", "package-alerts", "ltx_role_institutetext"}
VOID = {"br", "img", "meta", "link", "input", "hr", "area", "base", "col", "embed", "source", "track", "wbr"}
BLOCKS = {"p", "li", "div", "section", "figcaption", "ul", "ol", "blockquote", "article", "h1", "h2", "h3", "h4", "h5", "h6", "dd", "dt"}


class _Parser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.sections = []
        self.title = None
        self._buf = []
        self._paras = []
        self._head = None
        self._head_tag = None
        self._skip = None
        self._skip_depth = 0
        self._math = 0

    def _flush_para(self):
        t = re.sub(r"\s+", " ", "".join(self._buf)).strip()
        self._buf = []
        if t:
            self._paras.append(t)

    def _close_section(self):
        self._flush_para()
        if self._paras:
            self.sections.append({"title": self.title or "Preamble", "text": "\n\n".join(self._paras)})
        self._paras = []

    def handle_starttag(self, tag, attrs):
        if tag in VOID:
            return
        a = dict(attrs)
        if self._skip == "head" and tag == "body":
            self._skip = None  # </head> is optional in HTML; the body's start ends the head
        if self._skip:
            if tag == self._skip:
                self._skip_depth += 1
            return
        cls = a.get("class") or ""
        if tag in SKIP_TAGS or SKIP_CLASSES.intersection(cls.split()):
            self._skip, self._skip_depth = tag, 1
            return
        if tag == "math":
            self._math += 1
            alt = a.get("alttext")
            if alt:
                self._buf.append(" " + alt.strip() + " ")
            return
        if self._math:
            return
        if tag in BLOCKS:
            self._flush_para()
        if tag in ("h2", "h3"):
            self._close_section()
            self._head, self._head_tag = [], tag

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if self._skip:
            if tag == self._skip:
                self._skip_depth -= 1
                if self._skip_depth == 0:
                    self._skip = None
            return
        if tag == "math":
            self._math = max(0, self._math - 1)
            return
        if self._math:
            return
        if self._head is not None and tag == self._head_tag:
            t = re.sub(r"\s+", " ", "".join(self._head)).strip()
            self._head, self._head_tag = None, None
            self.title = t or None
            return
        if tag in BLOCKS:
            self._flush_para()

    def handle_data(self, data):
        if self._skip or self._math:
            return
        (self._head if self._head is not None else self._buf).append(data)


def sections(html):
    p = _Parser()
    p.feed(html)
    p.close()
    p._close_section()
    return [s for s in p.sections if s["title"].strip().lower() != "abstract"]

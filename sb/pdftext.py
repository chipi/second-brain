"""PDF to sections: pypdf page text, cleaned, split on headings. pypdf is imported only here and only when a PDF is read."""
import io
import os
import re
import shutil
import subprocess
import tempfile

LIGATURES = {"/f_f_i": "ffi", "/f_f_l": "ffl", "/f_i": "fi", "/f_f": "ff", "/f_l": "fl", "/f_t": "ft", "/t_t": "tt", "/T_h": "Th", "/Q_u": "Qu",
             "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st"}
_LIG = re.compile("|".join(re.escape(k) for k in sorted(LIGATURES, key=len, reverse=True)))
# a word after a line-final hyphen that makes it a real compound ("English-to-German"), not a split word
KEEP_HYPHEN = {"to", "and", "or", "of", "in", "on", "by", "for", "based", "free", "level", "wise", "like", "specific", "aware", "driven"}

NUMBERED = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,3})\.?\s+([A-Z][^\n]{1,78})$")
ROMAN = re.compile(r"^([IVX]{1,5})\.\s+([A-Z][A-Z0-9 ,\-:&]{2,60})$")
ANY_CASE = {"abstract", "introduction", "conclusion", "conclusions", "references", "bibliography", "acknowledgments", "acknowledgements",
            "acknowledgment", "acknowledgement", "results", "discussion", "methods", "method", "materials and methods", "related work", "background",
            "results and discussion"}
CAPS_ONLY = {"RELATED WORK", "BACKGROUND", "METHOD", "METHODS", "METHODOLOGY", "RESULTS", "DISCUSSION", "EXPERIMENTS", "EVALUATION"}
APPENDIX = re.compile(r"^Appendix(\s+[A-Z0-9])?\b[^\n]{0,60}$")
ABSTRACT_MAX = 3000
SCAN_WORDS_PER_PAGE = 50
GARBLED_BELOW = 0.6
OCR_MAX_PAGES = 60


class PdfUnavailable(RuntimeError):
    """pypdf is not installed: a run-level problem, never a per-paper one."""


class PdfError(Exception):
    pass


def clean(text):
    text = _LIG.sub(lambda m: LIGATURES[m.group(0)], text).replace("\x00", "")
    return "\n".join(ln for ln in text.splitlines() if not re.fullmatch(r"\s*\d{1,4}\s*", ln))


def page_texts(body):
    try:
        import logging
        from pypdf import PdfReader
    except ImportError:
        raise PdfUnavailable("pypdf is not installed: run `make venv`") from None
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    try:
        reader = PdfReader(io.BytesIO(body), strict=False)
        return [p.extract_text() or "" for p in reader.pages]
    except Exception as e:
        raise PdfError(f"{type(e).__name__}: {e}") from None


def is_scanned(pages):
    words = sum(len(p.split()) for p in pages)
    return words < 30 or (len(pages) > 2 and words / len(pages) < SCAN_WORDS_PER_PAGE)


_EDGE = ".,;:!?()[]{}\"'“”‘’-–—*"
_VOWEL = re.compile(r"[aeiouyAEIOUY]")
_CONSONANTS = re.compile(r"[^aeiouyAEIOUY]{6}")


def real_words(text):
    """How many tokens look like words: 2 to 24 letters, a vowel, no run of six consonants (a measurable stand-in for a dictionary)."""
    n = 0
    for tok in text.split():
        t = tok.strip(_EDGE)
        if 2 <= len(t) <= 24 and t.isascii() and t.isalpha() and _VOWEL.search(t) and not _CONSONANTS.search(t):
            n += 1
    return n


def is_garbled(pages):
    """A text layer that is mostly not words: a bad legacy OCR layer."""
    toks = sum(len(p.split()) for p in pages)
    return toks >= 100 and real_words("\n".join(pages)) < GARBLED_BELOW * toks


class Ocr:
    """pdftoppm (poppler) renders the pages, tesseract reads them. `run` and `which` are injectable so tests need neither tool."""

    def __init__(self, run=subprocess.run, which=shutil.which, max_pages=OCR_MAX_PAGES, dpi=300, timeout=180):
        self.run, self.which, self.max_pages, self.dpi, self.timeout = run, which, max_pages, dpi, timeout

    def available(self):
        return bool(self.which("pdftoppm") and self.which("tesseract"))

    def pages(self, body, n_pages):
        """(texts of the first max_pages pages, failed page count)."""
        last = min(n_pages, self.max_pages)
        with tempfile.TemporaryDirectory(prefix="sb-ocr-") as tmp:
            src = os.path.join(tmp, "in.pdf")
            with open(src, "wb") as f:
                f.write(body)
            self.run(["pdftoppm", "-r", str(self.dpi), "-png", "-f", "1", "-l", str(last), src, os.path.join(tmp, "p")],
                     capture_output=True, timeout=self.timeout * max(1, last // 4), check=True)
            images = sorted((n for n in os.listdir(tmp) if n.startswith("p-") and n.endswith(".png")), key=lambda n: int(re.sub(r"\D", "", n)))
            texts, failed = [], 0
            for name in images:
                try:
                    r = self.run(["tesseract", os.path.join(tmp, name), "-", "-l", "eng"], capture_output=True, timeout=self.timeout)
                    out = r.stdout.decode("utf-8", "replace") if isinstance(r.stdout, bytes) else r.stdout
                    if r.returncode != 0:
                        raise OSError("tesseract failed")
                    texts.append(out)
                except (subprocess.SubprocessError, OSError):
                    texts.append("")
                    failed += 1
            return texts, failed


def heading(line, state):
    """The section title a line starts, or None. `state['top']` tracks the last top-level number so stray numbered lines are refused."""
    s = line.strip()
    if not s or len(s) > 90:
        return None
    if s.lower().rstrip(":") in ANY_CASE and s != s.lower():
        return s.rstrip(":").title() if s.isupper() else s.rstrip(":")
    if s.rstrip(":") in CAPS_ONLY:
        return s.rstrip(":").title()
    if APPENDIX.match(s):
        return s
    m = ROMAN.match(s)
    if m:
        return f"{m.group(1)} {m.group(2).title()}"
    m = NUMBERED.match(s)
    if m:
        num, title = m.groups()
        words = title.split()
        if len(words) > 12 or title[-1] in ".,;:" or "..." in title or re.fullmatch(r"[\d.,%-]+", words[-1]) or not re.search(r"[A-Za-z]{3}", title) or sum(c.isdigit() for c in title) > len(title) // 3:
            return None
        top = int(num.split(".")[0])
        sub = "." in num
        if not (top == state["top"] and sub or top == state["top"] + 1 or (state["top"] == 0 and top <= 2)):
            return None
        state["top"] = top
        return f"{num} {title}"
    return None


def reflow(block):
    """Lines to paragraphs: hyphenated line breaks joined, wrapped lines merged, a paragraph ends on a short sentence-final line."""
    lines = [ln.strip() for ln in block if ln.strip()]
    if not lines:
        return ""
    longest = max(len(ln) for ln in lines)
    paras, cur, last = [], "", 0
    for ln in lines:
        if not cur:
            cur = ln
        elif re.search(r"[A-Za-z]-$", cur) and ln[0].islower():
            first = re.match(r"[a-z]+", ln).group(0)
            cur = cur + ln if first in KEEP_HYPHEN else cur[:-1] + ln
        elif cur[-1] in ".?!:" and last < 0.7 * longest:
            paras.append(cur)
            cur = ln
        else:
            cur += " " + ln
        last = len(ln)
    paras.append(cur)
    return "\n\n".join(paras)


def split_headings(pages, drop_abstract=True):
    """[{title, text}] when at least two headings are found, else None."""
    state = {"top": 0}
    parts, title, buf, found = [], "Front matter", [], 0
    for page in pages:
        for ln in page.splitlines():
            h = heading(ln, state)
            if h:
                found += 1
                parts.append((title, buf))
                title, buf = h, []
            else:
                buf.append(ln)
    parts.append((title, buf))
    if found < 2:
        return None
    out = []
    for t, buf in parts:
        text = reflow(buf)
        if not text:
            continue
        if drop_abstract and t.lower() == "abstract" and len(text) <= ABSTRACT_MAX:
            continue
        out.append({"title": t, "text": text})
    return out


def page_sections(pages):
    return [{"title": f"page {i}", "text": reflow(p.splitlines())} for i, p in enumerate(pages, 1) if p.strip()]


def _ocr(pages, body, ocr, info):
    """The OCR'd pages when they hold more real words than the same pages of the text layer, else None. Notes what happened in info['ocr']."""
    o = info["ocr"] = {"tried": False}
    if ocr is None or not ocr.available():
        o["why"] = "no OCR tool"
        return None
    o["tried"] = True
    try:
        texts, failed = ocr.pages(body, len(pages))
    except (subprocess.SubprocessError, OSError) as e:
        o["why"] = f"OCR failed ({type(e).__name__})"
        return None
    texts = [clean(t) for t in texts]
    n = len(texts)
    o.update(pages=n, failed_pages=failed, capped=len(pages) > n, of=len(pages), words=sum(len(t.split()) for t in texts))
    o["real_words"], o["layer_real_words"] = real_words("\n".join(texts)), real_words("\n".join(pages[:n]))
    return texts if o["real_words"] > o["layer_real_words"] and o["words"] >= 30 else None


def extract(body, drop_abstract=True, ocr=None):
    """(sections, info) from PDF bytes. info: pages, words, kind ('pdf' or 'ocr'), scanned (no usable text, sections empty), split, ocr (what OCR did).

    OCR runs when the text layer is under ~50 words a page over more than two pages, or mostly not words, and is kept only if it reads more real words."""
    pages = [clean(p) for p in page_texts(body)]
    info = {"pages": len(pages), "kind": "pdf", "ocr": None}
    if is_scanned(pages) or is_garbled(pages):
        better = _ocr(pages, body, ocr, info)
        if better is not None:
            pages, info["kind"] = better, "ocr"
    info["words"] = sum(len(p.split()) for p in pages)
    info["scanned"] = info["words"] < 30 if info["kind"] == "ocr" else is_scanned(pages)
    if info["scanned"]:
        info["split"] = None
        return [], info
    secs = split_headings(pages, drop_abstract)
    info["split"] = "headings" if secs else "pages"
    return secs or page_sections(pages), info

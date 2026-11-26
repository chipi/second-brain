"""The upgrade pass: for every cached text that is abstract only, look for a legal open full text (HTML, then PDF) and record what was tried."""
import json
import os
import urllib.parse

from . import htmltext, ids, pdftext
from .fetch import MAX_BODY, FetchError, _now, _ws, text_path, write_text
from .seed import load_manifests

NTRS_HOST = "https://ntrs.nasa.gov"
MAX_CANDIDATES = 4
OCR_NOTE = "text read by OCR (tesseract); expect recognition errors"
NOTES = {"scanned": "scanned pdf, OCR found no text", "scanned_no_ocr_tool": "scanned, no OCR tool"}

# outcome -> (final, reason shown by `sb status`)
OUTCOMES = {
    "html": (True, None), "pdf": (True, None), "ocr": (True, None),
    "scanned_no_ocr_tool": (False, "scanned, no OCR tool"),
    "paywalled": (True, "paywalled"),
    "no_pdf_in_ntrs": (True, "no pdf in NTRS"),
    "no_open_fulltext": (True, "no open full text"),
    "scanned": (True, "scanned"),
    "pdf_unreadable": (True, "pdf unreadable"),
    "fetch_failed": (False, "fetch failed"),
}


def ntrs_pdf_link(body):
    """The first PDF link of an NTRS record as an absolute URL, or None."""
    for d in json.loads(body).get("downloads") or []:
        link = ((d or {}).get("links") or {}).get("pdf")
        if link:
            return link if link.startswith("http") else NTRS_HOST + (link if link.startswith("/") else "/" + link)
    return None


def openalex_pdf_urls(body):
    """Candidate open-access URLs of an OpenAlex work, best first: the best location's PDF, the oa_url, other locations' PDFs."""
    w = json.loads(body)
    out = [((w.get("best_oa_location") or {}).get("pdf_url")), (w.get("open_access") or {}).get("oa_url")]
    out += [(loc or {}).get("pdf_url") for loc in w.get("locations") or []]
    return list(dict.fromkeys(u for u in out if u))


def crossref_pdf_urls(body):
    links = (json.loads(body)["message"].get("link")) or []
    return [x["URL"] for x in links if x.get("URL") and (x.get("content-type") or "").lower() == "application/pdf"]


class _Run:
    """One paper's attempt: the steps tried, and whether any miss may change on a retry."""

    def __init__(self, fetcher, doc, ocr):
        self.fetcher, self.doc, self.ocr, self.tried, self.transient = fetcher, doc, ocr, [], False
        self.sections, self.kind, self.url, self.ocr_info = None, None, None, None
        self.scanned = self.no_tool = self.unreadable = self.no_pdf = False

    def step(self, name, result):
        self.tried.append({"step": name, "result": result})

    def get(self, name, url):
        """(status, body) or None; a miss is recorded."""
        try:
            status, body = self.fetcher.get(url)
        except FetchError as e:
            self.transient = True
            self.step(name, f"fetch error: {e}")
            return None
        if status != 200:
            self.transient = self.transient or status >= 500
            self.step(name, f"HTTP {status}")
            return None
        return status, body

    def json_get(self, name, url, parse):
        got = self.get(name, url)
        if got is None:
            return None
        try:
            return parse(got[1])
        except (ValueError, KeyError, TypeError, AttributeError) as e:
            self.step(name, f"unparseable answer ({type(e).__name__})")
            return None

    def html(self, vid):
        got = self.get("arxiv-html", f"https://arxiv.org/html/{vid}")
        if got is None:
            return False
        secs = htmltext.sections(got[1].decode("utf-8", "replace"))
        if not secs:
            self.step("arxiv-html", "page had no sections")
            return False
        self.sections, self.kind, self.url = secs, "html", f"https://arxiv.org/html/{vid}"
        self.step("arxiv-html", f"ok, {len(secs)} sections")
        return True

    def pdf(self, name, url):
        got = self.get(name, url)
        if got is None:
            return False
        body = got[1]
        if len(body) > MAX_BODY:
            self.step(name, "pdf over the size cap")
            return False
        if not body.lstrip()[:5].startswith(b"%PDF"):
            self.step(name, "answer is not a pdf")
            return False
        try:
            secs, info = pdftext.extract(body, drop_abstract=bool(self.doc.get("abstract")), ocr=self.ocr)
        except pdftext.PdfError as e:
            self.unreadable = True
            self.step(name, f"unreadable pdf ({e})")
            return False
        o = info["ocr"] or {}
        if info["scanned"]:
            if o.get("why") == "no OCR tool":
                self.no_tool = True
                self.step(name, f"scanned pdf, no OCR tool: {info['words']} words on {info['pages']} pages")
            else:
                self.scanned = True
                self.step(name, f"scanned pdf, OCR found no text: {o.get('why') or str(o.get('words', 0)) + ' words'} on {info['pages']} pages")
            return False
        self.sections, self.kind, self.url, self.ocr_info = secs, info["kind"], url, info["ocr"]
        how = f"by {info['split']}"
        if info["kind"] == "ocr":
            how = (f"by {info['split']}, OCR of {o['pages']} of {o['of']} pages" + (" (capped)" if o["capped"] else "") +
                   f", {o['real_words']} real words vs {o['layer_real_words']} in the text layer, {o['failed_pages']} pages failed")
        self.step(name, f"ok, {info['pages']} pages, {len(secs)} sections {how}")
        return True

    def pdf_candidates(self, name, urls):
        for u in urls[:MAX_CANDIDATES]:
            if self.pdf(name, u):
                return True
            if self.scanned or self.no_tool:
                return False
        return False


def _arxiv(r, m):
    vid = ids.bare(r.doc.get("versioned_id") or m["id"])
    return r.html(vid) or r.pdf("arxiv-pdf", f"https://arxiv.org/pdf/{vid}")


def _ntrs(r, m):
    rec = r.json_get("ntrs-record", "https://ntrs.nasa.gov/api/citations/" + ids.bare(m["id"]), lambda b: {"link": ntrs_pdf_link(b)})
    if rec is None:
        return False
    if not rec["link"]:
        r.step("ntrs-record", "no pdf link in the record")
        r.no_pdf = True
        return False
    return r.pdf("ntrs-pdf", rec["link"])


def _doi(r, m):
    doi = urllib.parse.quote(ids.bare(m["id"]), safe="/()")
    urls = r.json_get("openalex", "https://api.openalex.org/works/https://doi.org/" + doi, openalex_pdf_urls) or []
    if urls and r.pdf_candidates("openalex-pdf", urls):
        return True
    if r.scanned:
        return False
    urls = r.json_get("crossref-links", "https://api.crossref.org/works/" + doi, crossref_pdf_urls) or []
    if urls and r.pdf_candidates("crossref-pdf", urls):
        return True
    if not any(t["step"] in ("openalex-pdf", "crossref-pdf") for t in r.tried):
        r.step("doi", "no open-access pdf listed")
    return False


def upgrade_one(m, doc, fetcher, ocr=None):
    """(new doc, outcome) for one abstract-only text; the doc carries a `fulltext` record whatever the outcome."""
    k = ids.kind(m["id"])
    r = _Run(fetcher, doc, ocr)
    ok = {"arxiv": _arxiv, "ntrs": _ntrs, "doi": _doi}.get(k, lambda r, m: r.step("id", f"no full-text route for {k!r}") or False)(r, m)
    if ok:
        outcome = r.kind
    elif r.scanned:
        outcome = "scanned"
    elif r.no_tool:
        outcome = "scanned_no_ocr_tool"
    elif r.transient:
        outcome = "fetch_failed"
    elif r.unreadable:
        outcome = "pdf_unreadable"
    elif r.no_pdf:
        outcome = "no_pdf_in_ntrs"
    elif k == "doi":
        outcome = "paywalled"
    else:
        outcome = "no_open_fulltext"
    new = dict(doc)
    if ok:
        new.update(sections=r.sections, source_kind=r.kind, abstract_only=False)
        new.pop("note", None)
        if r.kind == "ocr":
            capped = r.ocr_info["capped"]
            new["note"] = OCR_NOTE + (f"; only the first {r.ocr_info['pages']} of {r.ocr_info['of']} pages" if capped else "")
    elif outcome in NOTES:
        new["note"] = NOTES[outcome]
    rec = {"outcome": outcome, "final": OUTCOMES[outcome][0], "tried": r.tried, "at": _now()}
    if ok:
        rec["url"] = r.url
        if r.ocr_info and r.ocr_info.get("tried"):
            rec["ocr"] = r.ocr_info
    new["fulltext"] = rec
    return new, outcome


def fulltext_all(content, data, fetcher, limit=None, only=None, retry=False, ocr=None):
    ocr = ocr or pdftext.Ocr()
    wanted_ids = {ids.normalize(o) for o in only} if only else None
    stats = {"wanted": 0, "html": 0, "pdf": 0, "ocr": 0, "abstract": 0, "failed": 0, "skipped_final": 0, "no_text": 0}
    outcomes = {}
    for m in load_manifests(content):
        if wanted_ids is not None and m["id"] not in wanted_ids:
            continue
        path = text_path(data, m["id"])
        if not os.path.exists(path):
            stats["no_text"] += 1
            continue
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        if doc.get("source_kind") in ("html", "pdf", "ocr"):
            continue
        if (doc.get("fulltext") or {}).get("final") and not retry:
            stats["skipped_final"] += 1
            continue
        if limit is not None and stats["wanted"] >= limit:
            break
        stats["wanted"] += 1
        new, outcome = upgrade_one(m, doc, fetcher, ocr)
        write_text(data, m["id"], new)
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
        if outcome in ("html", "pdf", "ocr"):
            stats[outcome] += 1
        elif outcome == "fetch_failed":
            stats["failed"] += 1
        else:
            stats["abstract"] += 1
        last = new["fulltext"]["tried"][-1:] or [{}]
        fetcher.log(f"{outcome:16} {m['id']}  [{_ws(str(last[0].get('result', '')))}]")
    stats["outcomes"] = outcomes
    return stats

import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
import unittest.mock

import support
from pdffix import make_pdf
from sb import build, fetch, fulltext, pdftext, report, seed
from test_fetch import PAGE, Clock, FakeHttp

try:
    import pypdf  # noqa: F401
    HAVE_PYPDF = True
except ImportError:
    HAVE_PYPDF = False
needs_pypdf = unittest.skipUnless(HAVE_PYPDF, "pypdf is not installed (make venv)")

BODY = "Sentence one of the body talks about orbits and transfers in some detail here. " * 3
PAPER = [
    ["Fixture Paper Title", "Abstract", "A short abstract of the fixture paper.", "1 Introduction", BODY, BODY, "2 Method", BODY, "2.1 Details",
     "The details of the method are described here, with informa-", "tion split over a line and English-", "to-German compounds kept."],
    [BODY, BODY, "3 Results", BODY, BODY, "References", "[1] A. Author. A Fixture Reference. 1999.", "[2] B. Author. Another Reference. 2001."],
]
NO_HEADINGS = [[BODY] * 3, [BODY] * 3]
SCAN = [[], [], []]


GARBLED = [[("xkqzvtbn brtsnplk " * 12).strip()] * 4 for _ in range(3)]


class FakeOcr:
    """Stands in for pdftext.Ocr: no tool is run."""

    def __init__(self, texts, available=True, failed=0, max_pages=60):
        self.texts, self.avail, self.failed, self.max_pages, self.calls = texts, available, failed, max_pages, 0

    def available(self):
        return self.avail

    def pages(self, body, n_pages):
        self.calls += 1
        return self.texts[:min(n_pages, self.max_pages)], self.failed


def seed_lines():
    rows = [("arxiv:2001.00001", True, 2020), ("arxiv:1501.00003", False, 2015), ("ntrs:19700000001", False, 1970),
            ("doi:10.1000/open.1", False, 2019), ("doi:10.1000/closed.2", False, 2018)]
    return "".join(json.dumps({"vertical": "space" if i % 2 else "ai", "subtopic": "t", "id": pid, "title": f"Paper {i}", "year": y, "html": h}) + "\n"
                   for i, (pid, h, y) in enumerate(rows))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.content, self.data = os.path.join(self.tmp, "c"), os.path.join(self.tmp, "d")
        os.makedirs(os.path.join(self.content, "seed"))
        with open(os.path.join(self.content, "seed", "s.jsonl"), "w") as f:
            f.write(seed_lines())
        seed.seed(self.content)
        self.clock = Clock()
        for pid, vid in (("arxiv:2001.00001", "arxiv:2001.00001v2"), ("arxiv:1501.00003", "arxiv:1501.00003v1"), ("ntrs:19700000001", None),
                         ("doi:10.1000/open.1", None), ("doi:10.1000/closed.2", None)):
            fetch.write_text(self.data, pid, {"id": pid, "versioned_id": vid or pid, "title": "T", "authors": [], "year": 2000, "abstract": "An abstract.",
                                              "sections": [], "source_kind": "abstract", "abstract_only": True, "fetched_at": "2026-11-26T00:00:00Z"})

    def run_ft(self, routes, only=None, ocr=None, **kw):
        self.http = FakeHttp(routes)
        f = fetch.Fetcher(self.http, sleep=self.clock.sleep, clock=self.clock.now)
        return fulltext.fulltext_all(self.content, self.data, f, only=only, ocr=ocr or FakeOcr([], available=False), **kw)

    def text(self, pid):
        with open(fetch.text_path(self.data, pid)) as f:
            return json.load(f)

    def urls(self):
        return self.http.calls


class Parsing(unittest.TestCase):
    def test_ntrs_pdf_link_is_made_absolute_and_a_record_without_one_gives_none(self):
        body = json.dumps({"downloads": [{"links": {"original": "/api/x.docx"}}, {"links": {"pdf": "/api/citations/1/downloads/a.pdf"}}]})
        self.assertEqual(fulltext.ntrs_pdf_link(body), "https://ntrs.nasa.gov/api/citations/1/downloads/a.pdf")
        self.assertIsNone(fulltext.ntrs_pdf_link(json.dumps({"downloads": []})))
        self.assertIsNone(fulltext.ntrs_pdf_link("{}"))

    def test_openalex_candidates_best_location_first_then_oa_url_then_others_without_duplicates(self):
        w = json.dumps({"best_oa_location": {"pdf_url": "https://a/x.pdf"}, "open_access": {"oa_url": "https://b/landing"},
                        "locations": [{"pdf_url": "https://a/x.pdf"}, {"pdf_url": None}, {"pdf_url": "https://c/y.pdf"}]})
        self.assertEqual(fulltext.openalex_pdf_urls(w), ["https://a/x.pdf", "https://b/landing", "https://c/y.pdf"])
        self.assertEqual(fulltext.openalex_pdf_urls(json.dumps({"best_oa_location": None, "open_access": {"oa_url": None}})), [])

    def test_crossref_keeps_only_pdf_links(self):
        body = json.dumps({"message": {"link": [{"URL": "https://p/a.xml", "content-type": "text/xml"}, {"URL": "https://p/a.pdf", "content-type": "application/pdf"}]}})
        self.assertEqual(fulltext.crossref_pdf_urls(body), ["https://p/a.pdf"])
        self.assertEqual(fulltext.crossref_pdf_urls(json.dumps({"message": {}})), [])

    def test_ligature_artifacts_and_unicode_ligatures_are_fixed(self):
        self.assertEqual(pdftext.clean("pro/f_it /T_he /f_f_i/f_l ﬁnd oﬀ /t_ten /Q_uery"), "profit The ffifl find off tten Query")

    def test_page_number_lines_are_dropped(self):
        self.assertEqual(pdftext.clean("text\n 12 \nmore"), "text\nmore")

    def test_hyphen_join_drops_the_hyphen_but_keeps_a_compound(self):
        self.assertEqual(pdftext.reflow(["the informa-", "tion age and English-", "to-German pairs"]), "the information age and English-to-German pairs")

    def test_a_line_starting_with_a_non_ascii_lowercase_letter_joins(self):
        self.assertEqual(pdftext.reflow(["the r-", "\u00e9sum\u00e9 of a caf-", "\u00e9"]), "the r\u00e9sum\u00e9 of a caf\u00e9")

    def test_headings(self):
        st = {"top": 0}
        got = [pdftext.heading(x, st) for x in ["1 Introduction", "2. Related Work", "2.1 Sub part", "ABSTRACT", "REFERENCES", "I. INTRODUCTION",
                                                "7 Conclusion", "3 Results ... 5", "2014 English-French data", "A plain sentence here.", "3 Results 12"]]
        self.assertEqual(got, ["1 Introduction", "2 Related Work", "2.1 Sub part", "Abstract", "References", "I Introduction", None, None, None, None, None])


@needs_pypdf
class PdfToSections(unittest.TestCase):
    def test_headings_split_references_kept_and_abstract_dropped(self):
        secs, info = pdftext.extract(make_pdf(PAPER))
        self.assertEqual(info["split"], "headings")
        self.assertEqual([s["title"] for s in secs], ["Front matter", "1 Introduction", "2 Method", "2.1 Details", "3 Results", "References"])
        by = {s["title"]: s["text"] for s in secs}
        self.assertIn("information split over a line and English-to-German compounds", by["2.1 Details"])
        self.assertIn("Another Reference", by["References"])

    def test_abstract_is_kept_when_the_source_had_none(self):
        secs, _ = pdftext.extract(make_pdf(PAPER), drop_abstract=False)
        self.assertEqual(secs[1]["title"], "Abstract")

    def test_fewer_than_two_headings_falls_back_to_pages(self):
        secs, info = pdftext.extract(make_pdf(NO_HEADINGS))
        self.assertEqual((info["split"], [s["title"] for s in secs]), ("pages", ["page 1", "page 2"]))

    def test_a_blank_pdf_is_scanned(self):
        secs, info = pdftext.extract(make_pdf(SCAN))
        self.assertEqual((secs, info["scanned"]), ([], True))

    @needs_pypdf
    def test_a_pdf_pypdf_refuses_is_read_by_poppler(self):
        self.assertEqual(pdftext.page_texts(b"%PDF-1.4 not really", fallback=lambda body: ["one", "two"]), ["one", "two"])
        with self.assertRaises(pdftext.PdfError):
            pdftext.page_texts(b"%PDF-1.4 not really", fallback=lambda body: None)

    def test_poppler_pages_splits_on_form_feeds(self):
        done = subprocess.CompletedProcess([], 0, stdout="page one\fpage two\f".encode())
        self.assertEqual(pdftext.poppler_pages(b"%PDF", run=lambda *a, **k: done, which=lambda n: "/usr/bin/" + n), ["page one", "page two"])
        self.assertIsNone(pdftext.poppler_pages(b"%PDF", which=lambda n: None))

        def fails(*a, **k):
            raise subprocess.CalledProcessError(1, a[0])
        self.assertIsNone(pdftext.poppler_pages(b"%PDF", run=fails, which=lambda n: "/usr/bin/" + n))

    def test_garbage_is_a_pdf_error(self):
        with self.assertRaises(pdftext.PdfError):
            pdftext.extract(b"%PDF-1.4 not really")


class Upgrade(Base):
    def test_html_beats_pdf_and_the_html_url_is_the_versioned_id(self):
        out = self.run_ft({"https://arxiv.org/html/2001.00001v2": [(200, PAGE)]}, only=["arxiv:2001.00001"])
        self.assertEqual((out["html"], out["pdf"]), (1, 0))
        self.assertEqual(self.urls(), ["https://arxiv.org/html/2001.00001v2"])
        d = self.text("arxiv:2001.00001")
        self.assertEqual((d["source_kind"], d["abstract_only"], d["fulltext"]["outcome"], d["fulltext"]["final"]), ("html", False, "html", True))
        self.assertEqual([s["title"] for s in d["sections"]], ["1 Intro", "2 Method"])

    def test_an_old_paper_the_seed_did_not_flag_still_gets_an_html_attempt_then_a_pdf_attempt(self):
        out = self.run_ft({}, only=["arxiv:1501.00003"])
        self.assertEqual(self.urls(), ["https://arxiv.org/html/1501.00003v1", "https://arxiv.org/pdf/1501.00003v1"])
        d = self.text("arxiv:1501.00003")
        self.assertEqual((out["abstract"], d["fulltext"]["outcome"], d["fulltext"]["final"], d["source_kind"]), (1, "no_open_fulltext", True, "abstract"))
        self.assertEqual([t["step"] for t in d["fulltext"]["tried"]], ["arxiv-html", "arxiv-pdf"])
        self.assertEqual(d["fulltext"]["tried"][0]["result"], "HTTP 404")

    def test_the_pdf_gets_a_three_second_gap_on_arxiv(self):
        self.run_ft({"https://arxiv.org/html/": [(404, "")], "https://arxiv.org/pdf/": [(404, "")]}, only=["arxiv:1501.00003"])
        self.assertEqual(self.clock.sleeps, [3.0])

    @needs_pypdf
    def test_arxiv_pdf_when_there_is_no_html(self):
        out = self.run_ft({"https://arxiv.org/html/": [(404, "")], "https://arxiv.org/pdf/1501.00003v1": [(200, make_pdf(PAPER))]}, only=["arxiv:1501.00003"])
        self.assertEqual(out["pdf"], 1)
        d = self.text("arxiv:1501.00003")
        self.assertEqual((d["source_kind"], d["abstract_only"], d["fulltext"]["outcome"]), ("pdf", False, "pdf"))
        self.assertEqual(d["sections"][-1]["title"], "References")
        self.assertNotIn("note", d)

    def test_ntrs_record_without_a_pdf_ends_abstract_with_its_reason(self):
        self.run_ft({"https://ntrs.nasa.gov/api/citations/19700000001": [(200, json.dumps({"downloads": []}))]}, only=["ntrs:19700000001"])
        d = self.text("ntrs:19700000001")
        self.assertEqual((d["source_kind"], d["fulltext"]["outcome"], d["fulltext"]["final"]), ("abstract", "no_pdf_in_ntrs", True))

    @needs_pypdf
    def test_ntrs_pdf_is_fetched_from_the_prefixed_link(self):
        rec = json.dumps({"downloads": [{"links": {"pdf": "/api/citations/19700000001/downloads/x.pdf"}}]})
        self.run_ft({"https://ntrs.nasa.gov/api/citations/19700000001/downloads/x.pdf": [(200, make_pdf(PAPER))],
                     "https://ntrs.nasa.gov/api/citations/19700000001": [(200, rec)]}, only=["ntrs:19700000001"])
        self.assertEqual(self.text("ntrs:19700000001")["source_kind"], "pdf")

    @needs_pypdf
    def test_doi_takes_the_openalex_pdf_and_sends_no_identity(self):
        oa = json.dumps({"best_oa_location": {"pdf_url": "https://repo.example/open.pdf"}})
        self.run_ft({"https://api.openalex.org/": [(200, oa)], "https://repo.example/open.pdf": [(200, make_pdf(PAPER))]}, only=["doi:10.1000/open.1"])
        self.assertEqual(self.urls()[0], "https://api.openalex.org/works/https://doi.org/10.1000/open.1")
        self.assertEqual(self.text("doi:10.1000/open.1")["source_kind"], "pdf")
        self.assertFalse([u for u in self.urls() if "mailto" in u or "@" in u])

    @needs_pypdf
    def test_crossref_pdf_link_is_the_second_try(self):
        cr = json.dumps({"message": {"link": [{"URL": "https://pub.example/a.pdf", "content-type": "application/pdf"}]}})
        self.run_ft({"https://api.openalex.org/": [(404, "")], "https://api.crossref.org/": [(200, cr)], "https://pub.example/a.pdf": [(200, make_pdf(PAPER))]},
                    only=["doi:10.1000/open.1"])
        self.assertEqual(self.text("doi:10.1000/open.1")["source_kind"], "pdf")
        self.assertEqual([t["step"] for t in self.text("doi:10.1000/open.1")["fulltext"]["tried"]], ["openalex", "crossref-pdf"])

    def test_a_doi_without_an_open_pdf_is_paywalled_and_final(self):
        out = self.run_ft({"https://api.openalex.org/": [(200, json.dumps({"best_oa_location": None}))],
                           "https://api.crossref.org/": [(200, json.dumps({"message": {}}))]}, only=["doi:10.1000/closed.2"])
        d = self.text("doi:10.1000/closed.2")
        self.assertEqual((out["abstract"], d["source_kind"], d["fulltext"]["outcome"], d["fulltext"]["final"]), (1, "abstract", "paywalled", True))
        self.assertEqual(d["fulltext"]["tried"][-1], {"step": "doi", "result": "no open-access pdf listed"})

    def test_an_oa_link_that_answers_html_or_403_stays_paywalled(self):
        oa = json.dumps({"best_oa_location": {"pdf_url": "https://pub.example/a.pdf"}, "open_access": {"oa_url": "https://pub.example/landing"}})
        self.run_ft({"https://api.openalex.org/": [(200, oa)], "https://pub.example/a.pdf": [(403, "")], "https://pub.example/landing": [(200, "<html>")],
                     "https://api.crossref.org/": [(404, "")]}, only=["doi:10.1000/closed.2"])
        d = self.text("doi:10.1000/closed.2")
        self.assertEqual(d["fulltext"]["outcome"], "paywalled")
        self.assertIn({"step": "openalex-pdf", "result": "answer is not a pdf"}, d["fulltext"]["tried"])

    def test_a_pdf_over_the_cap_is_refused(self):
        old, fulltext.MAX_BODY = fulltext.MAX_BODY, 100
        self.addCleanup(setattr, fulltext, "MAX_BODY", old)
        oa = json.dumps({"best_oa_location": {"pdf_url": "https://pub.example/a.pdf"}})
        self.run_ft({"https://api.openalex.org/": [(200, oa)], "https://pub.example/a.pdf": [(200, b"%PDF-1.4" + b"x" * 200)],
                     "https://api.crossref.org/": [(404, "")]}, only=["doi:10.1000/closed.2"])
        self.assertIn({"step": "openalex-pdf", "result": "pdf over the size cap"}, self.text("doi:10.1000/closed.2")["fulltext"]["tried"])

    SCAN_ROUTES = {"https://arxiv.org/html/": [(404, "")], "https://arxiv.org/pdf/": [(200, make_pdf(SCAN))]}

    @needs_pypdf
    def test_a_scan_is_read_by_ocr_and_recorded_as_kind_ocr(self):
        ocr = FakeOcr([BODY + "\n1 Introduction\n" + BODY] * 3)
        out = self.run_ft(self.SCAN_ROUTES, only=["arxiv:1501.00003"], ocr=ocr)
        d = self.text("arxiv:1501.00003")
        self.assertEqual((out["ocr"], d["source_kind"], d["abstract_only"], d["fulltext"]["outcome"], d["fulltext"]["final"]), (1, "ocr", False, "ocr", True))
        self.assertIn("OCR", d["note"])
        self.assertEqual((d["fulltext"]["ocr"]["pages"], d["fulltext"]["ocr"]["capped"]), (3, False))
        self.assertTrue(d["sections"])

    @needs_pypdf
    def test_a_scan_ocr_cannot_read_ends_abstract_with_a_final_reason(self):
        self.run_ft(self.SCAN_ROUTES, only=["arxiv:1501.00003"], ocr=FakeOcr(["", "", ""]))
        d = self.text("arxiv:1501.00003")
        self.assertEqual((d["source_kind"], d["note"], d["fulltext"]["outcome"], d["fulltext"]["final"]), ("abstract", "scanned pdf, OCR found no text", "scanned", True))

    @needs_pypdf
    def test_a_scan_without_the_ocr_tools_is_recorded_and_retried_once_they_exist(self):
        self.run_ft(self.SCAN_ROUTES, only=["arxiv:1501.00003"], ocr=FakeOcr([], available=False))
        d = self.text("arxiv:1501.00003")
        self.assertEqual((d["source_kind"], d["note"], d["fulltext"]["outcome"], d["fulltext"]["final"]), ("abstract", "scanned, no OCR tool", "scanned_no_ocr_tool", False))
        self.assertEqual(report.status(self.content, self.data)["abstract_only"]["arxiv:1501.00003"], "scanned, no OCR tool")
        out = self.run_ft(self.SCAN_ROUTES, only=["arxiv:1501.00003"], ocr=FakeOcr([BODY] * 3))
        self.assertEqual((out["wanted"], out["ocr"]), (1, 1))


class Resumable(Base):
    CLOSED = {"https://api.openalex.org/": [(200, json.dumps({}))], "https://api.crossref.org/": [(200, json.dumps({"message": {}}))]}

    def test_a_server_error_is_retried_next_run_and_a_final_outcome_is_not_until_retry(self):
        out = self.run_ft({"https://api.openalex.org/": [(500, "")], "https://api.crossref.org/": [(500, "")]}, only=["doi:10.1000/closed.2"])
        self.assertEqual(out["failed"], 1)
        self.assertEqual(self.text("doi:10.1000/closed.2")["fulltext"], {**self.text("doi:10.1000/closed.2")["fulltext"], "outcome": "fetch_failed", "final": False})
        out = self.run_ft(self.CLOSED, only=["doi:10.1000/closed.2"])
        self.assertEqual((out["wanted"], out["abstract"]), (1, 1))
        self.assertTrue(self.text("doi:10.1000/closed.2")["fulltext"]["final"])
        out = self.run_ft(self.CLOSED, only=["doi:10.1000/closed.2"])
        self.assertEqual((out["wanted"], out["skipped_final"], self.urls()), (0, 1, []))
        out = self.run_ft(self.CLOSED, only=["doi:10.1000/closed.2"], retry=True)
        self.assertEqual((out["wanted"], bool(self.urls())), (1, True))

    def test_html_and_pdf_papers_are_skipped_and_a_missing_text_is_counted(self):
        self.run_ft({"https://arxiv.org/html/2001.00001v2": [(200, PAGE)]}, only=["arxiv:2001.00001"])
        os.remove(fetch.text_path(self.data, "ntrs:19700000001"))
        out = self.run_ft({"https://arxiv.org/html/2001.00001v2": [(200, PAGE)]}, only=["arxiv:2001.00001", "ntrs:19700000001"])
        self.assertEqual((out["wanted"], out["no_text"], self.urls()), (0, 1, []))

    def test_limit_counts_papers_tried(self):
        out = self.run_ft({}, limit=2)
        self.assertEqual(out["wanted"], 2)

    def test_writes_are_atomic_no_tmp_left(self):
        self.run_ft({}, only=["arxiv:1501.00003"])
        self.assertFalse([n for n in os.listdir(os.path.join(self.data, "texts")) if n.endswith(".tmp")])


class Status(Base):
    def test_counts_reasons_failures_and_staleness(self):
        os.remove(fetch.text_path(self.data, "doi:10.1000/open.1"))
        with open(os.path.join(self.data, "failures.json"), "w") as f:
            json.dump({"doi:10.1000/open.1": "doi: HTTP 500"}, f)
        self.run_ft({"https://arxiv.org/html/2001.00001v2": [(200, PAGE)], "https://ntrs.nasa.gov/": [(200, json.dumps({"downloads": []}))],
                     "https://api.openalex.org/": [(200, json.dumps({}))], "https://api.crossref.org/": [(200, json.dumps({"message": {}}))]},
                    only=["arxiv:2001.00001", "ntrs:19700000001", "doi:10.1000/closed.2"])
        st = report.status(self.content, self.data)
        o = st["overall"]
        self.assertEqual((o["manifests"], o["texts"], o["not_fetched"], o["no_abstract_no_body"]), (5, 4, 1, 1))
        self.assertEqual(o["source_kind"], {"html": 1, "pdf": 0, "ocr": 0, "abstract": 3})
        self.assertEqual(o["abstract_only_reasons"], {"no pdf in NTRS": 1, "paywalled": 1, "not tried": 1})
        self.assertEqual(st["abstract_only"]["doi:10.1000/closed.2"], "paywalled")
        self.assertEqual(st["failures"]["count"], 1)
        self.assertEqual(set(st["verticals"]), {"ai", "space"})
        self.assertEqual(sum(v["manifests"] for v in st["verticals"].values()), 5)
        self.assertFalse(st["index"]["present"])
        build.build(self.content, self.data, "tv")
        ix = report.status(self.content, self.data)["index"]
        self.assertEqual((ix["present"], ix["version"], ix["papers_with_text"], ix["texts_on_disk"], ix["stale"]), (True, "index-tv", 4, 4, False))
        later = time.time() + 100
        os.utime(fetch.text_path(self.data, "ntrs:19700000001"), (later, later))
        ix = report.status(self.content, self.data)["index"]
        self.assertEqual((ix["texts_newer_than_index"], ix["stale"]), (1, True))
        self.assertIn("STALE", report.render(report.status(self.content, self.data)))
        json.dumps(st)


class OcrRules(unittest.TestCase):
    def test_real_words_counts_wordlike_tokens_only(self):
        self.assertEqual(pdftext.real_words("The orbit, (strengths) of x1 qzxkvwb 12 -- a Lambert's"), 4)

    def test_garbled_is_a_measured_share_of_non_words(self):
        self.assertTrue(pdftext.is_garbled([" ".join(["xkqzvtbn", "brtsnplk"] * 60)]))
        self.assertFalse(pdftext.is_garbled([BODY * 3]))
        self.assertFalse(pdftext.is_garbled(["xkqzvtbn brtsnplk"]))

    def test_the_ocr_runs_pdftoppm_then_tesseract_per_page_and_survives_a_page_that_fails(self):
        calls = []

        def run(args, **kw):
            calls.append(args)
            if args[0] == "pdftoppm":
                for n in (1, 2, 10):
                    open(args[-1] + f"-{n:02d}.png", "wb").close()
                return type("R", (), {"returncode": 0, "stdout": b""})()
            if args[1].endswith("p-02.png"):
                raise subprocess.TimeoutExpired(args, 1)
            return type("R", (), {"returncode": 0, "stdout": ("text of " + os.path.basename(args[1])).encode()})()
        ocr = pdftext.Ocr(run=run, which=lambda n: "/usr/bin/" + n, max_pages=3)
        texts, failed = ocr.pages(b"%PDF", 20)
        self.assertEqual((texts, failed), (["text of p-01.png", "", "text of p-10.png"], 1))
        self.assertEqual(calls[0][:4], ["pdftoppm", "-r", "300", "-png"])
        self.assertEqual(calls[0][calls[0].index("-l") + 1], "3")
        self.assertEqual(calls[1][2:], ["-", "-l", "eng"])

    def test_the_tools_are_missing_when_which_says_so(self):
        self.assertFalse(pdftext.Ocr(which=lambda n: "/x/pdftoppm" if n == "pdftoppm" else None).available())

    @unittest.skipUnless(shutil.which("pdftoppm") and shutil.which("tesseract"), "pdftoppm and tesseract are not installed")
    def test_the_real_tools_read_a_generated_page(self):
        body = make_pdf([["Orbital transfer problems are solved by iteration"] * 4])
        with unittest.mock.patch.object(pdftext, "OCR_MAX_PAGES", 1):
            texts, failed = pdftext.Ocr().pages(body, 1)
        self.assertEqual(failed, 0)
        self.assertIn("transfer", texts[0].lower())


@needs_pypdf
class OcrChoice(unittest.TestCase):
    def test_ocr_wins_for_a_garbled_text_layer_and_the_layer_wins_when_ocr_is_worse(self):
        good = [BODY] * 3
        secs, info = pdftext.extract(make_pdf(GARBLED), ocr=FakeOcr(good))
        self.assertEqual((info["kind"], info["scanned"], info["ocr"]["capped"]), ("ocr", False, False))
        self.assertGreater(info["ocr"]["real_words"], info["ocr"]["layer_real_words"])
        secs, info = pdftext.extract(make_pdf(GARBLED), ocr=FakeOcr(["xkqzvtbn brtsnplk " * 20] * 3))
        self.assertEqual(info["kind"], "pdf")

    def test_a_clean_text_layer_never_calls_ocr(self):
        ocr = FakeOcr([BODY] * 2)
        pdftext.extract(make_pdf(PAPER), ocr=ocr)
        self.assertEqual(ocr.calls, 0)

    def test_pages_past_the_cap_are_recorded_as_capped(self):
        secs, info = pdftext.extract(make_pdf([[]] * 5), ocr=FakeOcr([BODY] * 5, max_pages=2))
        self.assertEqual((info["kind"], info["ocr"]["pages"], info["ocr"]["of"], info["ocr"]["capped"]), ("ocr", 2, 5, True))
        self.assertEqual([s["title"] for s in secs], ["page 1", "page 2"])

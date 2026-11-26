import json
import os
import shutil
import tempfile
import unittest

import support
from pdffix import make_pdf
from sb import build, fetch, fulltext, ids, report, seed, store
from test_fetch import Clock, FakeHttp
from test_fulltext import BODY, PAPER, SCAN, FakeOcr, needs_pypdf

A = "https://example.org/notes/a"
B = "https://example.com/blog/b"
C = "https://example.net/c"
PDF_URL = "https://example.org/files/report.pdf"
ARTICLE = "<html><head><title>T</title></head><body><nav>Menu Home</nav><article><h2>Setup</h2><p>%s</p><h2>Results</h2><p>%s</p></article><footer>Contact</footer></body></html>"
GOOD = ARTICLE % ("Orchids need light and water in measured amounts. " * 8, "The zebrafish module routes events between agents. " * 8)
THIN = "<html><body><h2>Home</h2><p>Welcome. Sign in.</p></body></html>"


class Ids(unittest.TestCase):
    def test_normalize_lowercases_scheme_and_host_drops_the_fragment_and_keeps_the_rest_exact(self):
        self.assertEqual(ids.normalize("url:HTTPS://Example.ORG/Notes/A?q=B&x=1#sec"), "url:https://example.org/Notes/A?q=B&x=1")
        self.assertEqual(ids.normalize(" url:https://example.org:8080/a/ "), "url:https://example.org:8080/a/")
        self.assertEqual(ids.normalize("url:https://example.org"), "url:https://example.org")
        self.assertEqual(ids.normalize("URL:http://example.org/a#x"), "url:http://example.org/a")

    def test_safe_id_is_a_short_safe_filename_that_differs_per_url_and_never_collides_on_case_of_the_path(self):
        a, b = ids.safe_id("url:" + A), ids.safe_id("url:" + A.upper().replace("HTTPS", "https"))
        self.assertRegex(a, r"^url_example\.org_[0-9a-f]{16}$")
        self.assertNotEqual(a, b)
        self.assertEqual(ids.safe_id("url:HTTPS://EXAMPLE.org/notes/a#f"), a)
        long = ids.safe_id("url:https://" + "x" * 300 + ".example.org/" + "p" * 5000)
        self.assertLessEqual(len(long), 70)
        self.assertRegex(long, r"^[a-z0-9._-]+$")

    def test_links_is_the_url_and_a_url_must_be_absolute_http(self):
        self.assertEqual(ids.links("url:" + A), {"url": A})
        for ok in (A, "http://example.org", "https://user@example.org/x"):
            self.assertTrue(ids.is_url(ok), ok)
        for bad in ("example.org/a", "file:///etc/passwd", "ftp://example.org/a", "https:///a", "/a"):
            self.assertFalse(ids.is_url(bad), bad)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.content, self.data = os.path.join(self.tmp, "c"), os.path.join(self.tmp, "d")
        os.makedirs(os.path.join(self.content, "seed"))
        rows = [("url:" + A, "Page A", 2024), ("url:" + B, "Page B", None), ("url:" + C, "Page C", 2023), ("url:" + PDF_URL, "Report", 2022)]
        with open(os.path.join(self.content, "seed", "r.jsonl"), "w") as f:
            for pid, title, year in rows:
                f.write(json.dumps({"vertical": "ai", "subtopic": "notes", "id": pid, "title": title, "year": year, "why": f"Fixture: {title}", "html": True}) + "\n")
        seed.seed(self.content)
        self.clock = Clock()

    def fetch(self, routes=None):
        self.http = FakeHttp(routes or {})
        f = fetch.Fetcher(self.http, sleep=self.clock.sleep, clock=self.clock.now)
        return fetch.fetch_all(self.content, self.data, f)

    def run_ft(self, routes, only=None, ocr=None, **kw):
        self.http = FakeHttp(routes)
        f = fetch.Fetcher(self.http, sleep=self.clock.sleep, clock=self.clock.now)
        return fulltext.fulltext_all(self.content, self.data, f, only=only, ocr=ocr or FakeOcr([], available=False), **kw)

    def text(self, url):
        with open(fetch.text_path(self.data, "url:" + url)) as f:
            return json.load(f)


class Seed(Base):
    def test_manifests_are_written_under_the_safe_id_and_equal_urls_merge(self):
        with open(os.path.join(self.content, "seed", "dup.jsonl"), "w") as f:
            f.write(json.dumps({"vertical": "software", "subtopic": "x", "id": "url:HTTPS://Example.org/notes/a#top", "title": "Page A again", "year": 2024}) + "\n")
        st = seed.seed(self.content)
        self.assertEqual((st["entries"], st["merged_in_several_verticals"]), (4, 1))
        with open(os.path.join(self.content, "papers", ids.safe_id("url:" + A) + ".json")) as f:
            m = json.load(f)
        self.assertEqual((m["id"], sorted(m["verticals"])), ("url:" + A, ["ai", "software"]))

    def test_a_url_that_is_not_absolute_http_stops_the_seed(self):
        with open(os.path.join(self.content, "seed", "bad.jsonl"), "w") as f:
            f.write(json.dumps({"vertical": "ai", "id": "url:file:///etc/passwd", "title": "x"}) + "\n")
        with self.assertRaises(SystemExit):
            seed.seed(self.content)


class Fetch(Base):
    def test_fetch_takes_the_metadata_from_the_seed_and_touches_no_network(self):
        stats = self.fetch()
        self.assertEqual((stats["wanted"], stats["abstract"], stats["failed"]), (4, 4, 0))
        self.assertEqual(self.http.calls, [])
        d = self.text(A)
        self.assertEqual((d["title"], d["year"], d["source_kind"], d["abstract_only"], d["sections"]), ("Page A", 2024, "abstract", True, []))


class Upgrade(Base):
    def setUp(self):
        super().setUp()
        self.fetch()

    def test_an_html_page_becomes_sections_without_its_menu_and_footer(self):
        out = self.run_ft({A: [(200, GOOD)]}, only=["url:" + A])
        self.assertEqual((out["html"], out["abstract"]), (1, 0))
        d = self.text(A)
        self.assertEqual((d["source_kind"], d["abstract_only"], d["fulltext"]["outcome"], d["fulltext"]["url"]), ("html", False, "html", A))
        self.assertEqual([s["title"] for s in d["sections"]], ["Setup", "Results"])
        self.assertNotIn("Menu", json.dumps(d["sections"]))
        self.assertNotIn("note", d)

    def test_pages_of_one_host_are_a_second_apart_and_arxiv_gaps_are_unchanged(self):
        self.run_ft({"https://example.org/": [(200, GOOD)]}, only=["url:" + A, "url:" + PDF_URL])
        self.assertEqual(self.clock.sleeps, [1.0])
        self.clock.sleeps.clear()
        f = fetch.Fetcher(FakeHttp({}), sleep=self.clock.sleep, clock=self.clock.now)
        f.get("https://api.crossref.org/works/x")
        f.get("https://api.crossref.org/works/y")
        self.assertEqual(self.clock.sleeps, [])

    def test_a_thin_page_is_recorded_abstract_only_final_and_not_retried_until_retry(self):
        out = self.run_ft({B: [(200, THIN)]}, only=["url:" + B])
        d = self.text(B)
        self.assertEqual((out["abstract"], d["source_kind"], d["abstract_only"], d["fulltext"]["outcome"], d["fulltext"]["final"]), (1, "abstract", True, "thin_page", True))
        self.assertIn("thin", d["fulltext"]["tried"][0]["result"])
        self.assertEqual(report.status(self.content, self.data)["abstract_only"]["url:" + B], "page had too little text")
        self.assertEqual(self.run_ft({B: [(200, GOOD)]}, only=["url:" + B])["wanted"], 0)
        self.assertEqual(self.run_ft({B: [(200, GOOD)]}, only=["url:" + B], retry=True)["html"], 1)

    @needs_pypdf
    def test_a_page_that_is_a_pdf_goes_through_pdftext(self):
        out = self.run_ft({PDF_URL: [(200, make_pdf(PAPER))]}, only=["url:" + PDF_URL])
        d = self.text(PDF_URL)
        self.assertEqual((out["pdf"], d["source_kind"], d["abstract_only"], d["fulltext"]["outcome"], d["fulltext"]["url"]), (1, "pdf", False, "pdf", PDF_URL))
        self.assertEqual(d["fulltext"]["tried"][0]["step"], "page-pdf")
        self.assertEqual(d["sections"][-1]["title"], "References")

    @needs_pypdf
    def test_a_pdf_served_at_a_page_url_is_found_by_its_bytes(self):
        self.run_ft({A: [(200, make_pdf(PAPER))]}, only=["url:" + A])
        self.assertEqual(self.text(A)["source_kind"], "pdf")

    @needs_pypdf
    def test_a_scanned_pdf_page_gets_the_ocr_fallback(self):
        out = self.run_ft({PDF_URL: [(200, make_pdf(SCAN))]}, only=["url:" + PDF_URL], ocr=FakeOcr([BODY + "\n1 Introduction\n" + BODY] * 3))
        d = self.text(PDF_URL)
        self.assertEqual((out["ocr"], d["source_kind"], d["fulltext"]["outcome"]), (1, "ocr", "ocr"))
        self.assertIn("OCR", d["note"])

    def test_one_dead_page_never_stops_the_batch_and_each_miss_has_its_reason(self):
        routes = {A: [fetch.FetchError("down")], B: [(404, "")], C: [(500, "boom")], PDF_URL: [(200, b"%PDF-1.4 garbage")]}
        out = self.run_ft(routes)
        self.assertEqual((out["wanted"], out["failed"], out["abstract"]), (4, 2, 2))
        self.assertEqual(out["outcomes"], {"fetch_failed": 2, "no_open_fulltext": 1, "pdf_unreadable": 1})
        self.assertFalse(self.text(A)["fulltext"]["final"])
        self.assertEqual(self.text(B)["fulltext"]["tried"], [{"step": "page", "result": "HTTP 404"}])

    def test_a_manifest_that_is_not_an_http_url_is_a_miss_not_a_fetch(self):
        path = os.path.join(self.content, "papers", ids.safe_id("url:" + C) + ".json")
        with open(path) as f:
            m = json.load(f)
        m["id"] = "url:file:///etc/passwd"
        with open(path, "w") as f:
            json.dump(m, f)
        fetch.write_text(self.data, m["id"], {"id": m["id"], "title": "x", "sections": [], "source_kind": "abstract", "abstract_only": True})
        self.run_ft({}, only=[m["id"]])
        self.assertEqual(self.http.calls, [])


class Serve(Base):
    def test_search_and_paper_show_the_url_as_the_link(self):
        self.fetch()
        self.run_ft({A: [(200, GOOD)]}, only=["url:" + A])
        build.build(self.content, self.data, "u")
        idx = store.Index(self.data)
        self.addCleanup(idx.close)
        hit = idx.search("zebrafish module")[0]
        self.assertEqual((hit["paper"], hit["url"], hit["section"]), ("url:" + A, A, "Results"))
        self.assertEqual(idx.paper("url:HTTPS://Example.org/notes/a#x")["links"], {"url": A})
        self.assertEqual(idx.search("Page B")[0]["paper"], "url:" + B)


if __name__ == "__main__":
    unittest.main()

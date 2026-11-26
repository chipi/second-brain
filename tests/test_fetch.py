import json
import os
import shutil
import tempfile
import unittest

import support
from sb import fetch, seed

ATOM = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">%s</feed>"""
ENTRY = """<entry><id>http://arxiv.org/abs/%(id)sv%(v)s</id><published>%(year)s-03-01T00:00:00Z</published>
<title>%(title)s
 continued</title><summary>  An abstract
 over lines.  </summary><author><name>Ada Example</name></author><author><name>Carl X</name></author></entry>"""
PAGE = "<html><body><article><h2><span>1 </span>Intro</h2><p>Hello html world.</p><h2>2 Method</h2><p>Methods here.</p></article></body></html>"


class FakeHttp:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get(self, url):
        self.calls.append(url)
        for prefix, answer in self.routes.items():
            if url.startswith(prefix):
                a = answer.pop(0) if isinstance(answer, list) and len(answer) > 1 else (answer[0] if isinstance(answer, list) else answer)
                if isinstance(a, Exception):
                    raise a
                status, body = a
                return status, body if isinstance(body, bytes) else body.encode(), {}
        return 404, b"", {}


class Clock:
    def __init__(self):
        self.t, self.sleeps = 0.0, []

    def now(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.content, self.data = os.path.join(self.tmp, "c"), os.path.join(self.tmp, "d")
        shutil.copytree(os.path.join(support.FIXTURES, "content"), self.content)
        seed.seed(self.content)
        self.clock = Clock()

    def fetcher(self, routes):
        self.http = FakeHttp(routes)
        return fetch.Fetcher(self.http, sleep=self.clock.sleep, clock=self.clock.now)

    def text(self, name):
        with open(os.path.join(self.data, "texts", name)) as f:
            return json.load(f)


class ArxivBatch(Base):
    def test_one_batched_call_html_for_flagged_papers_and_a_three_second_gap(self):
        atom = ATOM % (ENTRY % {"id": "2001.00001", "v": 2, "year": 2020, "title": "Frontier"} + ENTRY % {"id": "2002.00002", "v": 1, "year": 2021, "title": "Repair"})
        f = self.fetcher({"https://export.arxiv.org/api/query": [(200, atom)], "https://arxiv.org/html/": [(200, PAGE), (404, "")]})
        stats = fetch.fetch_all(self.content, self.data, f, only=["arxiv:2001.00001", "arxiv:2002.00002"])
        self.assertEqual(stats, {"wanted": 2, "html": 1, "abstract": 1, "failed": 0})
        api = [u for u in self.http.calls if "export.arxiv.org" in u]
        self.assertEqual(len(api), 1)
        self.assertIn("id_list=2001.00001,2002.00002", api[0])
        a = self.text("arxiv_2001.00001.json")
        self.assertEqual((a["source_kind"], a["versioned_id"], a["year"]), ("html", "arxiv:2001.00001v2", 2020))
        self.assertEqual(a["title"], "Frontier continued")
        self.assertEqual(a["abstract"], "An abstract over lines.")
        self.assertEqual(a["authors"], ["Ada Example", "Carl X"])
        self.assertEqual([s["title"] for s in a["sections"]], ["1 Intro", "2 Method"])
        self.assertIn("https://arxiv.org/html/2001.00001v2", self.http.calls)
        b = self.text("arxiv_2002.00002.json")
        self.assertEqual((b["source_kind"], b["abstract_only"]), ("abstract", True))
        self.assertIn("HTTP 404", b["note"])

    def test_calls_to_the_api_are_three_seconds_apart(self):
        f = self.fetcher({"https://export.arxiv.org/api/query": [(200, ATOM % "")]})
        f.get("https://export.arxiv.org/api/query?id_list=1")
        f.get("https://export.arxiv.org/api/query?id_list=2")
        self.assertEqual(self.clock.sleeps, [3.0])

    def test_429_backs_off_and_then_succeeds(self):
        f = self.fetcher({"https://export.arxiv.org/": [(429, ""), (429, ""), (200, "ok")]})
        self.assertEqual(f.get("https://export.arxiv.org/api/query?id_list=1"), (200, b"ok"))
        self.assertEqual([s for s in self.clock.sleeps if s >= 20], [20, 40])

    def test_429_forever_gives_up_after_a_few_minutes_and_is_a_failure(self):
        f = self.fetcher({"https://export.arxiv.org/": [(429, "")]})
        stats = fetch.fetch_all(self.content, self.data, f, only=["arxiv:2001.00001"])
        self.assertEqual(stats["failed"], 1)
        self.assertLessEqual(sum(self.clock.sleeps), 600)
        self.assertGreaterEqual(sum(s for s in self.clock.sleeps if s >= 20), 240)
        with open(os.path.join(self.data, "failures.json")) as fh:
            self.assertIn("429", json.load(fh)["arxiv:2001.00001"])
        self.assertFalse(os.path.exists(os.path.join(self.data, "texts", "arxiv_2001.00001.json")))


class OtherSourcesAndRuns(Base):
    NTRS = json.dumps({"title": "Universal Variable", "abstract": "An NTRS abstract.", "authorAffiliations": [{"meta": {"author": {"name": "C. Test"}}}],
                       "publications": [{"publicationDate": "1970-05-01T00:00:00.0000000+00:00"}]})
    CROSSREF = json.dumps({"message": {"title": ["Porkchop"], "author": [{"given": "D.", "family": "Nobody"}], "issued": {"date-parts": [[2001]]},
                                        "abstract": "<jats:p>Plots of <jats:italic>launch</jats:italic> windows.</jats:p>"}})

    def test_ntrs_and_crossref_are_abstract_only_and_jats_is_stripped(self):
        f = self.fetcher({"https://ntrs.nasa.gov/api/citations/20000000001": [(200, self.NTRS)],
                          "https://api.crossref.org/works/10.1000/fixture.2": [(200, self.CROSSREF)]})
        stats = fetch.fetch_all(self.content, self.data, f, only=["ntrs:20000000001", "doi:10.1000/fixture.2"])
        self.assertEqual(stats, {"wanted": 2, "html": 0, "abstract": 2, "failed": 0})
        n = self.text("ntrs_20000000001.json")
        self.assertEqual((n["abstract"], n["year"], n["authors"], n["abstract_only"]), ("An NTRS abstract.", 1970, ["C. Test"], True))
        d = self.text("doi_10.1000_fixture.2.json")
        self.assertEqual((d["abstract"], d["year"], d["authors"]), ("Plots of launch windows.", 2001, ["D. Nobody"]))

    def test_a_failure_is_recorded_and_the_run_goes_on_and_cached_texts_are_skipped(self):
        f = self.fetcher({"https://ntrs.nasa.gov/": [(500, "boom")], "https://api.crossref.org/": [(200, self.CROSSREF)]})
        stats = fetch.fetch_all(self.content, self.data, f, only=["ntrs:20000000001", "doi:10.1000/fixture.2"])
        self.assertEqual((stats["failed"], stats["abstract"]), (1, 1))
        with open(os.path.join(self.data, "failures.json")) as fh:
            self.assertEqual(list(json.load(fh)), ["ntrs:20000000001"])
        f2 = self.fetcher({"https://ntrs.nasa.gov/": [(200, self.NTRS)]})
        stats = fetch.fetch_all(self.content, self.data, f2, only=["ntrs:20000000001", "doi:10.1000/fixture.2"])
        self.assertEqual(stats["wanted"], 1)
        self.assertEqual(len(self.http.calls), 1)
        self.assertFalse(os.path.exists(os.path.join(self.data, "failures.json")))

    def test_a_network_error_is_a_failure_not_a_crash_and_limit_applies(self):
        f = self.fetcher({"https://ntrs.nasa.gov/": [fetch.FetchError("down")], "https://api.crossref.org/": [(200, "not json")]})
        stats = fetch.fetch_all(self.content, self.data, f, only=["ntrs:20000000001", "doi:10.1000/fixture.2"])
        self.assertEqual(stats["failed"], 2)
        f = self.fetcher({})
        self.assertEqual(fetch.fetch_all(self.content, self.data, f, limit=1)["wanted"], 1)


if __name__ == "__main__":
    unittest.main()

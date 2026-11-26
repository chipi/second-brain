import json
import os
import shutil
import tempfile
import unittest

import support
from sb import build, htmltext, ids, seed


class Ids(unittest.TestCase):
    def test_safe_ids(self):
        self.assertEqual(ids.safe_id("arxiv:2001.00001"), "arxiv_2001.00001")
        self.assertEqual(ids.safe_id("doi:10.1007/BF00048484"), "doi_10.1007_bf00048484")
        self.assertEqual(ids.safe_id("ntrs:20000000001"), "ntrs_20000000001")

    def test_normalize_drops_arxiv_version(self):
        self.assertEqual(ids.normalize("arxiv:2001.00001v3"), "arxiv:2001.00001")
        self.assertEqual(ids.normalize("doi:10.1000/ABC"), "doi:10.1000/abc")


class Seed(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.content = os.path.join(self.tmp, "c")
        shutil.copytree(os.path.join(support.FIXTURES, "content"), self.content)

    def manifest(self, name):
        with open(os.path.join(self.content, "papers", name)) as f:
            return json.load(f)

    def test_one_manifest_per_id_and_a_shared_id_merges_its_verticals(self):
        stats = seed.seed(self.content)
        self.assertEqual(stats["entries"], 4)
        self.assertEqual(sorted(os.listdir(os.path.join(self.content, "papers"))),
                         ["arxiv_2001.00001.json", "arxiv_2002.00002.json", "doi_10.1000_fixture.2.json", "ntrs_20000000001.json"])
        m = self.manifest("arxiv_2002.00002.json")
        self.assertEqual(m["verticals"], ["ai", "software"])
        self.assertEqual(m["subtopics"], {"ai": "agents", "software": "repair"})
        self.assertIn("ai reading", m["why"]["ai"])
        self.assertIn("software reading", m["why"]["software"])
        self.assertEqual((m["approver"], m["approved_at"]), ("seeded", "2026-11-26"))
        self.assertEqual(m["source"], ["seed/ai.jsonl", "seed/software.jsonl"])
        self.assertTrue(m["html"])
        self.assertEqual(self.manifest("ntrs_20000000001.json")["source"], "seed/space.jsonl")

    def test_idempotent_and_a_manifest_approver_survives(self):
        seed.seed(self.content)
        path = os.path.join(self.content, "papers", "arxiv_2001.00001.json")
        m = self.manifest("arxiv_2001.00001.json")
        m["approver"], m["approved_at"] = "operator", "2026-12-01"
        with open(path, "w") as f:
            json.dump(m, f)
        again = seed.seed(self.content)
        self.assertEqual(again["written"], 0)
        self.assertEqual(again["kept_other_approver"], 1)
        got = self.manifest("arxiv_2001.00001.json")
        self.assertEqual((got["approver"], got["approved_at"]), ("operator", "2026-12-01"))
        self.assertEqual(seed.seed(self.content)["written"], 0)


class Chunking(unittest.TestCase):
    def test_short_text_is_one_chunk(self):
        self.assertEqual(build.chunk_text("a\n\nb"), ["a\n\nb"])

    def test_long_text_splits_on_paragraphs_under_the_limit(self):
        paras = [("p%d " % i) * 200 for i in range(6)]
        pieces = build.chunk_text("\n\n".join(paras), 2000)
        self.assertGreater(len(pieces), 1)
        self.assertTrue(all(len(p) <= 2000 for p in pieces))
        self.assertEqual("\n\n".join(pieces).replace("\n\n", " ").split(), " ".join(paras).split())
        for p in pieces:
            self.assertTrue(any(p.startswith(x.strip()[:10]) for x in paras))

    def test_a_giant_paragraph_is_cut_at_spaces(self):
        pieces = build.chunk_text("word " * 1000, 2000)
        self.assertTrue(all(len(p) <= 2000 for p in pieces))
        self.assertEqual(" ".join(pieces).split(), ["word"] * 1000)


HTML = """<html><head><title>x</title><script>var a = 1;</script></head><body>
<nav><a href="/">Home</a> navigation junk</nav>
<header class="ltx_page_header">page header junk</header>
<a class="ds-skip-link" href="#x">skip link junk</a><div class="infobox">license junk</div>
<article class="ltx_document ltx_authors_1line">
<h1 class="ltx_title ltx_title_document">The Paper Title</h1>
<div class="ltx_authors">Some Author, University junk</div>
<div class="ltx_abstract"><h6 class="ltx_title ltx_title_abstract">Abstract</h6><p>The abstract text.</p></div>
<section class="ltx_section"><h2 class="ltx_title ltx_title_section"><span class="ltx_tag ltx_tag_section">1 </span>Introduction</h2>
<div class="ltx_para"><p class="ltx_p">First paragraph with <math alttext="x^2"><semantics><mi>x</mi><annotation>junk</annotation></semantics></math> inline math.</p></div>
<div class="ltx_para"><p class="ltx_p">Second   paragraph.</p></div>
<section class="ltx_subsection"><h3 class="ltx_title ltx_title_subsection"><span class="ltx_tag">1.1 </span>Background</h3>
<p class="ltx_p">Background text.</p><table><tr><td>table junk</td></tr></table><button>Report issue</button></section>
</section>
<section class="ltx_bibliography"><h2 class="ltx_title ltx_title_bibliography">References</h2>
<ul><li class="ltx_bibitem"><span class="ltx_tag ltx_tag_bibitem">[1]</span> Author. A cited work. 1999.</li><li class="ltx_bibitem">Other. Another. 2000.</li></ul></section>
</article><footer class="ltx_page_footer">footer junk</footer></body></html>"""


class Html(unittest.TestCase):
    def test_sections_from_headings_with_paragraphs(self):
        secs = htmltext.sections(HTML)
        self.assertEqual([s["title"] for s in secs], ["1 Introduction", "1.1 Background", "References"])
        intro = secs[0]["text"]
        self.assertEqual(intro.split("\n\n")[0], "First paragraph with x^2 inline math.")
        self.assertEqual(intro.split("\n\n")[1], "Second paragraph.")
        self.assertEqual(secs[1]["text"], "Background text.")
        self.assertEqual(secs[2]["text"].split("\n\n"), ["Author. A cited work. 1999.", "Other. Another. 2000."])
        blob = " ".join(s["text"] for s in secs)
        for junk in ("junk", "Report issue", "Home", "var a"):
            self.assertNotIn(junk, blob)

    def test_page_without_headings_is_one_preamble_section_or_none(self):
        self.assertEqual(htmltext.sections("<html><body></body></html>"), [])
        self.assertEqual(htmltext.sections("<body><p>just text</p></body>")[0]["title"], "Preamble")


if __name__ == "__main__":
    unittest.main()

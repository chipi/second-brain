"""`sb status`: what the library holds and why a paper is abstract only. Read-only; touches nothing."""
import json
import os
import sqlite3

from . import ids
from .fulltext import OUTCOMES
from .seed import load_manifests

FULL = ("html", "pdf", "ocr")


def _reason(doc):
    """Why a text is abstract only."""
    ft = doc.get("fulltext")
    if not ft:
        return "not tried"
    return OUTCOMES.get(ft.get("outcome"), (None, ft.get("outcome") or "not tried"))[1]


def _tally():
    return {"manifests": 0, "texts": 0, "source_kind": {"html": 0, "pdf": 0, "ocr": 0, "abstract": 0}, "abstract_only_reasons": {},
            "not_fetched": 0, "no_abstract_no_body": 0}


def _count(t, doc):
    t["manifests"] += 1
    if doc is None:
        t["not_fetched"] += 1
        t["no_abstract_no_body"] += 1
        return
    t["texts"] += 1
    kind = doc.get("source_kind") or "abstract"
    t["source_kind"][kind] = t["source_kind"].get(kind, 0) + 1
    if kind not in FULL:
        r = _reason(doc)
        t["abstract_only_reasons"][r] = t["abstract_only_reasons"].get(r, 0) + 1
    if not doc.get("abstract") and not doc.get("sections"):
        t["no_abstract_no_body"] += 1


def _index(data, texts_dir, n_texts):
    link = os.path.join(data, "current")
    db_path = os.path.realpath(os.path.join(link, "library.sqlite"))
    if not os.path.exists(db_path):
        return {"present": False}
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        version = db.execute("SELECT value FROM meta WHERE key='version'").fetchone()[0]
        chunks = db.execute("SELECT count(*) FROM chunks").fetchone()[0]
        with_text = db.execute("SELECT count(*) FROM papers WHERE source_kind IS NOT NULL").fetchone()[0]
    finally:
        db.close()
    built = os.path.getmtime(db_path)
    newer = 0
    if os.path.isdir(texts_dir):
        newer = sum(1 for n in os.listdir(texts_dir) if n.endswith(".json") and os.path.getmtime(os.path.join(texts_dir, n)) > built)
    return {"present": True, "version": version, "chunks": chunks, "papers_with_text": with_text, "texts_on_disk": n_texts,
            "texts_newer_than_index": newer, "stale": newer > 0 or with_text != n_texts}


def status(content, data):
    texts_dir = os.path.join(data, "texts")
    overall, per_v = _tally(), {}
    abstract_only = {}
    for m in load_manifests(content):
        path = os.path.join(texts_dir, ids.safe_id(m["id"]) + ".json")
        doc = None
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                doc = json.load(f)
        _count(overall, doc)
        for v in m.get("verticals") or ["(none)"]:
            _count(per_v.setdefault(v, _tally()), doc)
        if doc is not None and (doc.get("source_kind") or "abstract") not in FULL:
            abstract_only[m["id"]] = _reason(doc)
    fails = {}
    fpath = os.path.join(data, "failures.json")
    if os.path.exists(fpath):
        with open(fpath, encoding="utf-8") as f:
            fails = json.load(f)
    n_texts = overall["texts"]
    return {"overall": overall, "verticals": dict(sorted(per_v.items())), "abstract_only": dict(sorted(abstract_only.items())),
            "failures": {"count": len(fails), "entries": fails}, "index": _index(data, texts_dir, n_texts)}


def _line(name, t):
    k = t["source_kind"]
    out = [f"{name}: {t['manifests']} manifests, {t['texts']} texts  (html {k.get('html', 0)}, pdf {k.get('pdf', 0)}, ocr {k.get('ocr', 0)}, abstract {k.get('abstract', 0)})"]
    if t["abstract_only_reasons"]:
        out.append("    abstract only: " + ", ".join(f"{r} {n}" for r, n in sorted(t["abstract_only_reasons"].items())))
    out.append(f"    not fetched {t['not_fetched']}, no abstract and no body {t['no_abstract_no_body']}")
    return out


def render(st):
    lines = _line("overall", st["overall"])
    for v, t in st["verticals"].items():
        lines += _line(v, t)
    f = st["failures"]
    lines.append(f"failures.json: {f['count']} entries" + "".join(f"\n    {k}: {v}" for k, v in list(f["entries"].items())[:10]))
    ix = st["index"]
    if not ix["present"]:
        lines.append("index: none (run `sb build`)")
    else:
        lines.append(f"index {ix['version']}: {ix['chunks']} chunks, {ix['papers_with_text']} papers with text vs {ix['texts_on_disk']} texts on disk, "
                     f"{ix['texts_newer_than_index']} texts newer than the index: " + ("STALE" if ix["stale"] else "current"))
    return "\n".join(lines)

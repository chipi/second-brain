import json
import os
import re
import sqlite3
import subprocess
import uuid
from datetime import datetime, timezone

from . import ids
from .seed import load_manifests

CHUNK = 2000

SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE papers (
  id TEXT PRIMARY KEY, safe_id TEXT, title TEXT, year INTEGER, verticals TEXT, subtopics TEXT, why TEXT, approver TEXT,
  approved_at TEXT, source TEXT, authors TEXT, versioned_id TEXT, source_kind TEXT, abstract_only INTEGER, abstract TEXT
);
CREATE TABLE sections (paper TEXT, position INTEGER, title TEXT, text TEXT, PRIMARY KEY (paper, position));
CREATE VIRTUAL TABLE chunks USING fts5(
  paper UNINDEXED, section UNINDEXED, position UNINDEXED, verticals UNINDEXED, title, text, tokenize = 'porter unicode61'
);
"""


def chunk_text(text, size=CHUNK):
    """Pieces of at most `size` chars, cut on paragraph boundaries; a paragraph longer than `size` is cut at a space."""
    pieces, cur = [], ""
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        while len(para) > size:
            cut = para.rfind(" ", 0, size)
            cut = cut if cut > size // 2 else size
            if cur:
                pieces.append(cur)
                cur = ""
            pieces.append(para[:cut].strip())
            para = para[cut:].strip()
        if not para:
            continue
        if cur and len(cur) + 2 + len(para) > size:
            pieces.append(cur)
            cur = para
        else:
            cur = f"{cur}\n\n{para}" if cur else para
    if cur:
        pieces.append(cur)
    return pieces


def content_version(content):
    try:
        out = subprocess.run(["git", "-C", content, "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=20)
        sha = out.stdout.strip()
        return sha if out.returncode == 0 and sha else "nogit"
    except (OSError, subprocess.SubprocessError):
        return "nogit"


def _new_dir(data, version):
    n = 1
    while True:
        name = f"index-{version}" if n == 1 else f"index-{version}-{n}"
        path = os.path.join(data, name)
        if not os.path.exists(path):
            return name, path
        n += 1


def build(content, data, version=None):
    version = version or content_version(content)
    manifests = load_manifests(content)
    os.makedirs(data, exist_ok=True)
    name, final = _new_dir(data, version)
    tmpdir = final + ".building"
    os.makedirs(tmpdir)
    db = sqlite3.connect(os.path.join(tmpdir, "library.sqlite"))
    try:
        db.executescript(SCHEMA)
        stats = {"papers": 0, "with_text": 0, "chunks": 0}
        for m in sorted(manifests, key=lambda x: x["id"]):
            text = None
            tpath = os.path.join(data, "texts", ids.safe_id(m["id"]) + ".json")
            if os.path.exists(tpath):
                with open(tpath, encoding="utf-8") as f:
                    text = json.load(f)
            t = text or {}
            title = m["title"]
            abstract = t.get("abstract") or ""
            verticals = m.get("verticals") or []
            vcol = " " + " ".join(verticals) + " "
            db.execute("INSERT INTO papers VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                m["id"], ids.safe_id(m["id"]), title, m.get("year") or t.get("year"), json.dumps(verticals), json.dumps(m.get("subtopics") or {}),
                json.dumps(m.get("why") or {}), m.get("approver"), m.get("approved_at"), json.dumps(m.get("source")), json.dumps(t.get("authors") or []),
                t.get("versioned_id") or m["id"], t.get("source_kind"), 1 if t.get("abstract_only", text is not None and not t.get("sections")) else 0,
                abstract))
            db.execute("INSERT INTO sections VALUES (?,?,?,?)", (m["id"], 0, "Abstract", abstract))
            db.execute("INSERT INTO chunks (paper, section, position, verticals, title, text) VALUES (?,?,?,?,?,?)",
                       (m["id"], "Abstract", 0, vcol, title, abstract))
            stats["chunks"] += 1
            stats["papers"] += 1
            if text:
                stats["with_text"] += 1
            for pos, sec in enumerate(t.get("sections") or [], 1):
                db.execute("INSERT INTO sections VALUES (?,?,?,?)", (m["id"], pos, sec["title"], sec["text"]))
                for piece in chunk_text(sec["text"]):
                    db.execute("INSERT INTO chunks (paper, section, position, verticals, title, text) VALUES (?,?,?,?,?,?)",
                               (m["id"], sec["title"], pos, vcol, "", piece))
                    stats["chunks"] += 1
        db.executemany("INSERT INTO meta VALUES (?,?)", [
            ("version", name), ("commit", version), ("built_at", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))])
        db.commit()
        db.execute("INSERT INTO chunks(chunks) VALUES ('optimize')")
        db.commit()
    except BaseException:
        db.close()
        raise
    db.close()
    os.rename(tmpdir, final)
    link = os.path.join(data, "current")
    tmplink = os.path.join(data, f".current-{uuid.uuid4().hex[:8]}")
    os.symlink(name, tmplink)
    os.replace(tmplink, link)
    stats["version"] = name
    return stats

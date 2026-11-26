"""Read side of an index: what the CLI and the MCP server both call. Opens the index read-only."""
import calendar
import json
import os
import re
import sqlite3
import time

from . import ids

READ_CAP = 12000
MAX_K = 20


class BadInput(ValueError):
    pass


class NotFound(LookupError):
    pass


class Index:
    def __init__(self, data):
        path = os.path.realpath(os.path.join(data, "current", "library.sqlite"))
        if not os.path.exists(path):
            raise NotFound(f"no index at {data}/current: run `sb build`")
        self.db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        self.db.row_factory = sqlite3.Row
        self.version = self.db.execute("SELECT value FROM meta WHERE key='version'").fetchone()[0]

    def close(self):
        self.db.close()

    def stats(self):
        """Counts and build time for the metrics; built_at is Unix seconds, or None when the index does not say."""
        one = lambda sql: self.db.execute(sql).fetchone()[0]
        built = self.db.execute("SELECT value FROM meta WHERE key='built_at'").fetchone()
        try:
            at = calendar.timegm(time.strptime(built[0], "%Y-%m-%dT%H:%M:%SZ")) if built else None
        except ValueError:
            at = None
        return {"version": self.version, "papers": one("SELECT count(*) FROM papers"), "chunks": one("SELECT count(*) FROM chunks"), "built_at": at}

    def search(self, query, vertical=None, k=8):
        if not isinstance(query, str) or not query.strip():
            raise BadInput("query must be a non-empty string")
        if isinstance(k, bool) or not isinstance(k, int) or not 1 <= k <= MAX_K:
            raise BadInput(f"k must be an integer from 1 to {MAX_K}")
        terms = [t for t in re.findall(r"\w+", query.lower()) if len(t) > 1]
        if not terms:
            raise BadInput("query has no searchable words")
        match = " OR ".join('"%s"' % t for t in dict.fromkeys(terms))
        sql = ("SELECT chunks.paper, p.title, p.year, chunks.section, snippet(chunks, -1, '[', ']', '…', 24) AS snip, "
               "bm25(chunks, 0, 0, 0, 0, 1.5, 1.0) AS score FROM chunks JOIN papers p ON p.id = chunks.paper WHERE chunks MATCH ?")
        args = [match]
        if vertical is not None:
            if not isinstance(vertical, str) or not re.fullmatch(r"[\w-]+", vertical):
                raise BadInput("vertical must be a plain name")
            sql += " AND chunks.verticals LIKE ?"
            args.append(f"% {vertical} %")
        sql += " ORDER BY score LIMIT ?"
        args.append(k)
        rows = self.db.execute(sql, args).fetchall()
        out = [{"paper": r["paper"], "title": r["title"], "year": r["year"], "section": r["section"],
                "snippet": r["snip"], "score": round(-r["score"], 4), "index_version": self.version} for r in rows]
        for o in out:
            if ids.kind(o["paper"]) == "url":
                o["url"] = ids.bare(o["paper"])
        return out

    def _paper_row(self, pid):
        if not isinstance(pid, str) or not pid.strip():
            raise BadInput("paper must be a non-empty string")
        pid = ids.normalize(pid)
        row = self.db.execute("SELECT * FROM papers WHERE id = ?", (pid,)).fetchone()
        if row is None:
            raise NotFound(f"no paper {pid!r} in the library")
        return row

    def read(self, paper, section=None):
        row = self._paper_row(paper)
        secs = self.db.execute("SELECT position, title, text FROM sections WHERE paper = ? ORDER BY position", (row["id"],)).fetchall()
        if section is None:
            return {"paper": row["id"], "title": row["title"], "abstract": row["abstract"], "sections": [s["title"] for s in secs if s["position"] > 0],
                    "source_kind": row["source_kind"], "index_version": self.version}
        if not isinstance(section, str) or not section.strip():
            raise BadInput("section must be a non-empty string")
        want = section.strip().lower()
        hit = [s for s in secs if s["title"].lower() == want]
        if not hit and want.isdigit():
            hit = [s for s in secs if s["position"] == int(want)]
        if not hit:
            hit = [s for s in secs if want in s["title"].lower()]
        if len({s["title"] for s in hit}) != 1:
            names = [s["title"] for s in (hit or secs) if s["position"] > 0]
            raise NotFound(f"section {section!r} " + ("is ambiguous" if hit else "not found") + f"; sections: {names}")
        s = hit[0]
        text = s["text"]
        if len(text) > READ_CAP:
            text = text[:READ_CAP] + f"…[cut at {READ_CAP} of {len(s['text'])} chars]"
        return {"paper": row["id"], "title": row["title"], "section": s["title"], "text": text, "index_version": self.version}

    def paper(self, pid):
        row = self._paper_row(pid)
        return {"id": row["id"], "title": row["title"], "authors": json.loads(row["authors"]), "year": row["year"],
                "verticals": json.loads(row["verticals"]), "subtopics": json.loads(row["subtopics"]), "why": json.loads(row["why"]),
                "approver": row["approver"], "approved_at": row["approved_at"], "source": json.loads(row["source"]),
                "source_kind": row["source_kind"], "versioned_id": row["versioned_id"],
                "links": ids.links(row["id"], row["versioned_id"]), "index_version": self.version}

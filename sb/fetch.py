import html
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from . import htmltext, ids
from .seed import load_manifests

ATOM = "{http://www.w3.org/2005/Atom}"
UA = "second-brain/0.1 (personal research corpus)"
BACKOFF = (20, 40, 80, 160)
MAX_BODY = 40 << 20


class FetchError(Exception):
    pass


class Http:
    def get(self, url):
        """(status, body, headers) for any HTTP answer; FetchError only when there is none."""
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, r.read(MAX_BODY), r.headers
        except urllib.error.HTTPError as e:
            try:
                return e.code, e.read(1 << 20), e.headers
            finally:
                e.close()
        except (urllib.error.URLError, OSError) as e:
            raise FetchError(f"{url}: {type(e).__name__}: {getattr(e, 'reason', e)}") from None


class Fetcher:
    def __init__(self, http=None, sleep=time.sleep, clock=time.monotonic, api_gap=3.0, html_gap=1.0, log=lambda m: None):
        self.http, self.sleep, self.clock, self.log = http or Http(), sleep, clock, log
        self.gaps = {"export.arxiv.org": api_gap, "arxiv.org": html_gap}
        self.last = {}

    def get(self, url):
        host = urllib.parse.urlsplit(url).hostname
        for wait in (0,) + BACKOFF:
            if wait:
                self.log(f"429 from {host}: waiting {wait}s")
                self.sleep(wait)
            gap = self.gaps.get(host)
            if gap and host in self.last:
                left = gap - (self.clock() - self.last[host])
                if left > 0:
                    self.sleep(left)
            try:
                status, body, headers = self.http.get(url)
            finally:
                self.last[host] = self.clock()
            if status != 429:
                return status, body
            ra = headers.get("Retry-After") if headers else None
            if ra and ra.isdigit():
                self.sleep(min(int(ra), 300))
        raise FetchError(f"{url}: HTTP 429 after {len(BACKOFF)} retries")


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ws(s):
    return re.sub(r"\s+", " ", s or "").strip()


def _year(s):
    m = re.match(r"(\d{4})", str(s or ""))
    return int(m.group(1)) if m else None


def parse_arxiv_atom(xml_bytes):
    """{base id: {title, authors, abstract, year, version}} from an arXiv API answer."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        raise FetchError(f"arXiv answer is not XML ({e})") from None
    out = {}
    for ent in root.findall(ATOM + "entry"):
        title = _ws(ent.findtext(ATOM + "title"))
        m = re.search(r"/abs/(.+?)(v\d+)?$", (ent.findtext(ATOM + "id") or "").strip())
        if not m or title == "Error":
            continue
        out[m.group(1)] = {
            "title": title,
            "authors": [_ws(a.findtext(ATOM + "name")) for a in ent.findall(ATOM + "author")],
            "abstract": _ws(ent.findtext(ATOM + "summary")),
            "year": _year(ent.findtext(ATOM + "published")),
            "version": m.group(2) or "",
        }
    return out


def strip_jats(s):
    return _ws(html.unescape(re.sub(r"<[^>]+>", " ", s or "")))


def parse_crossref(body):
    msg = json.loads(body)["message"]
    authors = [_ws(f"{a.get('given', '')} {a.get('family', a.get('name', ''))}") for a in msg.get("author") or []]
    year = None
    for k in ("issued", "published-print", "published-online", "created"):
        parts = (msg.get(k) or {}).get("date-parts") or [[None]]
        if parts[0] and parts[0][0]:
            year = parts[0][0]
            break
    return {"title": _ws((msg.get("title") or [""])[0]), "authors": authors, "abstract": strip_jats(msg.get("abstract")), "year": year}


def parse_ntrs(body):
    d = json.loads(body)
    authors = []
    for a in d.get("authorAffiliations") or []:
        n = ((a.get("meta") or {}).get("author") or {}).get("name")
        if n:
            authors.append(_ws(n))
    date = d.get("distributionDate") or d.get("created") or ""
    for p in d.get("publications") or []:
        date = p.get("publicationDate") or date
        break
    return {"title": _ws(d.get("title")), "authors": authors, "abstract": _ws(d.get("abstract")), "year": _year(date)}


def text_path(data, pid):
    return os.path.join(data, "texts", ids.safe_id(pid) + ".json")


def write_text(data, pid, doc):
    path = text_path(data, pid)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _doc(m, meta, sections, kind, versioned=None, note=None):
    doc = {"id": m["id"], "versioned_id": versioned or m["id"], "title": meta.get("title") or m["title"], "authors": meta.get("authors") or [],
           "year": meta.get("year") or m.get("year"), "abstract": meta.get("abstract") or "", "sections": sections, "source_kind": kind,
           "abstract_only": kind == "abstract", "fetched_at": _now()}
    if note:
        doc["note"] = note
    return doc


def fetch_all(content, data, fetcher, limit=None, only=None):
    wanted_ids = {ids.normalize(o) for o in only} if only else None
    todo = []
    for m in load_manifests(content):
        if wanted_ids is not None and m["id"] not in wanted_ids:
            continue
        if os.path.exists(text_path(data, m["id"])):
            continue
        todo.append(m)
    if limit is not None:
        todo = todo[:limit]
    stats = {"wanted": len(todo), "html": 0, "abstract": 0, "failed": 0}
    failures = {}

    def fail(m, why):
        stats["failed"] += 1
        failures[m["id"]] = why
        fetcher.log(f"FAIL {m['id']}: {why}")

    def done(m, doc):
        write_text(data, m["id"], doc)
        stats["html" if doc["source_kind"] == "html" else "abstract"] += 1
        fetcher.log(f"ok   {m['id']} ({doc['source_kind']}, {len(doc['sections'])} sections)" + (f" [{doc['note']}]" if doc.get("note") else ""))

    arx = [m for m in todo if ids.kind(m["id"]) == "arxiv"]
    for i in range(0, len(arx), 50):
        batch = arx[i:i + 50]
        try:
            url = "https://export.arxiv.org/api/query?id_list=" + ",".join(ids.bare(m["id"]) for m in batch) + f"&max_results={len(batch)}"
            status, body = fetcher.get(url)
            if status != 200:
                raise FetchError(f"arXiv API HTTP {status}")
            metas = parse_arxiv_atom(body)
        except FetchError as e:
            for m in batch:
                fail(m, str(e))
            continue
        for m in batch:
            meta = metas.get(ids.bare(m["id"]))
            if meta is None:
                fail(m, "not in arXiv's answer")
                continue
            versioned = m["id"] + meta["version"]
            secs, note = [], None
            if m.get("html"):
                try:
                    status, body = fetcher.get(f"https://arxiv.org/html/{ids.bare(versioned)}")
                    if status == 200:
                        secs = htmltext.sections(body.decode("utf-8", "replace"))
                        if not secs:
                            note = "html had no sections"
                    else:
                        note = f"html unavailable (HTTP {status})"
                except FetchError as e:
                    note = f"html fetch failed: {e}"
            done(m, _doc(m, meta, secs, "html" if secs else "abstract", versioned, note))

    for m in todo:
        k = ids.kind(m["id"])
        if k == "arxiv":
            continue
        try:
            if k == "ntrs":
                status, body = fetcher.get("https://ntrs.nasa.gov/api/citations/" + ids.bare(m["id"]))
                parse = parse_ntrs
            elif k == "doi":
                status, body = fetcher.get("https://api.crossref.org/works/" + urllib.parse.quote(ids.bare(m["id"]), safe="/()"))
                parse = parse_crossref
            else:
                raise FetchError(f"unknown id kind {k!r}")
            if status != 200:
                raise FetchError(f"HTTP {status}")
            meta = parse(body)
        except (FetchError, ValueError, KeyError, TypeError) as e:
            fail(m, f"{k}: {e}")
            continue
        done(m, _doc(m, meta, [], "abstract", note=None if meta["abstract"] else "no abstract in the source"))

    fpath = os.path.join(data, "failures.json")
    os.makedirs(data, exist_ok=True)
    if failures:
        with open(fpath, "w", encoding="utf-8") as f:
            json.dump(failures, f, indent=1)
    elif os.path.exists(fpath):
        os.remove(fpath)
    return stats

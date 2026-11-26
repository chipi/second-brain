import json
import os

from . import ids

APPROVED_AT = "2026-11-26"


def _read_seed(path):
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if line:
                try:
                    yield json.loads(line)
                except ValueError as e:
                    raise SystemExit(f"{path}:{n}: not JSON ({e})")


def seed(content):
    """Write one manifest per seed entry into content/papers; returns (written, merged, kept)."""
    sdir = os.path.join(content, "seed")
    entries = {}
    for name in sorted(os.listdir(sdir)):
        if not name.endswith(".jsonl"):
            continue
        for e in _read_seed(os.path.join(sdir, name)):
            pid = ids.normalize(e["id"])
            m = entries.setdefault(pid, {"id": pid, "verticals": [], "subtopics": {}, "title": e["title"], "year": e.get("year"),
                                         "why": {}, "html": False, "sources": []})
            v = e["vertical"]
            if v not in m["verticals"]:
                m["verticals"].append(v)
            m["subtopics"][v] = e.get("subtopic")
            m["why"][v] = e.get("why")
            m["html"] = m["html"] or bool(e.get("html"))
            src = f"seed/{name}"
            if src not in m["sources"]:
                m["sources"].append(src)
    pdir = os.path.join(content, "papers")
    os.makedirs(pdir, exist_ok=True)
    written = merged = kept = 0
    for pid in sorted(entries):
        m = entries[pid]
        path = os.path.join(pdir, ids.safe_id(pid) + ".json")
        old = None
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                old = json.load(f)
        out = {
            "id": pid, "verticals": m["verticals"], "subtopics": m["subtopics"], "title": m["title"], "year": m["year"],
            "why": m["why"], "html": m["html"],
            "approver": (old or {}).get("approver", "seeded"), "approved_at": (old or {}).get("approved_at", APPROVED_AT),
            "source": m["sources"][0] if len(m["sources"]) == 1 else m["sources"],
        }
        if old and (old.get("approver") != "seeded"):
            kept += 1
        if len(m["verticals"]) > 1:
            merged += 1
        if old == out:
            continue
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2, sort_keys=False)
            f.write("\n")
        os.replace(tmp, path)
        written += 1
    return {"entries": len(entries), "written": written, "merged_in_several_verticals": merged, "kept_other_approver": kept}


def load_manifests(content):
    pdir = os.path.join(content, "papers")
    out = []
    for name in sorted(os.listdir(pdir)):
        if name.endswith(".json"):
            with open(os.path.join(pdir, name), encoding="utf-8") as f:
                out.append(json.load(f))
    return out

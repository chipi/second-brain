import re
import urllib.parse

_VERSION = re.compile(r"v\d+$")


def safe_id(pid):
    return re.sub(r"[^a-z0-9._-]+", "_", pid.lower()).strip("_")


def kind(pid):
    return pid.split(":", 1)[0]


def bare(pid):
    return pid.split(":", 1)[1]


def normalize(pid):
    """The manifest form of an id: arXiv versions dropped, DOIs lower-cased, whitespace stripped."""
    pid = pid.strip()
    if ":" not in pid:
        return pid
    k, rest = pid.split(":", 1)
    k = k.lower()
    if k == "arxiv":
        rest = _VERSION.sub("", rest)
    elif k == "doi":
        rest = rest.lower()
    return f"{k}:{rest}"


def links(pid, versioned=None):
    k, rest = kind(pid), bare(pid)
    if k == "arxiv":
        v = versioned or pid
        v = bare(v)
        return {"arxiv": f"https://arxiv.org/abs/{v}", "pdf": f"https://arxiv.org/pdf/{v}"}
    if k == "ntrs":
        return {"ntrs": f"https://ntrs.nasa.gov/citations/{rest}"}
    if k == "doi":
        return {"doi": "https://doi.org/" + urllib.parse.quote(rest, safe="/()")}
    return {}

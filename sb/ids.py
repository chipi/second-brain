import hashlib
import re
import urllib.parse

_VERSION = re.compile(r"v\d+$")
_URL = re.compile(r"^(https?)://([^/?#]*)([^#]*)", re.I)


def normalize_url(url):
    """Scheme and host lower-cased, the fragment dropped, everything else exact."""
    url = url.strip()
    m = _URL.match(url)
    if not m:
        return url.split("#", 1)[0]
    userinfo, at, host = m.group(2).rpartition("@")
    return f"{m.group(1).lower()}://{userinfo}{at}{host.lower()}{m.group(3)}"


def is_url(rest):
    """True for an absolute http(s) URL with a host."""
    m = _URL.match(rest)
    return bool(m and m.group(2).rpartition("@")[2])


def safe_id(pid):
    if pid.lower().startswith("url:"):
        rest = normalize_url(pid.split(":", 1)[1])
        host = re.sub(r"[^a-z0-9.-]+", "_", (urllib.parse.urlsplit(rest).hostname or "").lower())[:40].strip("_.-") or "host"
        return f"url_{host}_{hashlib.sha256(rest.encode()).hexdigest()[:16]}"
    return re.sub(r"[^a-z0-9._-]+", "_", pid.lower()).strip("_")


def kind(pid):
    return pid.split(":", 1)[0]


def bare(pid):
    return pid.split(":", 1)[1]


def normalize(pid):
    """The manifest form of an id: arXiv versions dropped, DOIs lower-cased, URLs by normalize_url, whitespace stripped."""
    pid = pid.strip()
    if ":" not in pid:
        return pid
    k, rest = pid.split(":", 1)
    k = k.lower()
    if k == "arxiv":
        rest = _VERSION.sub("", rest)
    elif k == "doi":
        rest = rest.lower()
    elif k == "url":
        rest = normalize_url(rest)
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
    if k == "url":
        return {"url": rest}
    return {}

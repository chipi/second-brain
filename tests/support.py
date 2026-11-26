import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from sb import build, seed  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def make_library(version="fx"):
    """A temp content dir seeded from the fixture lists and a temp data dir holding the fixture texts, built; (tmp, content, data)."""
    tmp = tempfile.mkdtemp(prefix="sb-test-")
    content, data = os.path.join(tmp, "content"), os.path.join(tmp, "data")
    shutil.copytree(os.path.join(FIXTURES, "content"), content)
    shutil.copytree(os.path.join(FIXTURES, "data"), data)
    seed.seed(content)
    build.build(content, data, version)
    return tmp, content, data

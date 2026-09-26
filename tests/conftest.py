import os
import sys
import tempfile
from pathlib import Path

import pytest

# Keep tests away from the real queue, history and saved position.
_tmp = Path(tempfile.mkdtemp(prefix="rmlabels-test-"))
os.environ["RMLABELS_DATA"] = str(_tmp / "data")
os.environ["RMLABELS_CONFIG"] = str(_tmp / "config.toml")
(_tmp / "config.toml").write_text(f'out_dir = "{_tmp / "out"}"\n[presets]\nreturn = "Return to:\\nTest Person\\n1 Test Road"\n')
(_tmp / "out").mkdir()

FIXTURES = Path(__file__).parent / "fixtures"
PRIVATE = FIXTURES / "private"


@pytest.fixture
def tmp_out():
    return _tmp / "out"


def private(name):
    p = PRIVATE / name
    if not p.exists():
        pytest.skip(f"private fixture {name} not present")
    return p

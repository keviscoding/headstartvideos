import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _isolated_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("ALEX_V2_DATA_DIR", str(tmp_path / "alex_v2_data"))
    for k in ("ALEX_V2_MODE", "ALEX_V2_AB_SPLIT", "ALEX_V2_MODEL"):
        monkeypatch.delenv(k, raising=False)
    yield

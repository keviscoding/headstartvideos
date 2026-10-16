"""Your own AI presenter (cr_presenters)."""
import io
import sys
import types
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
import cr_presenters as presenters  # noqa: E402


def photo(size=(800, 1000)):
    buf = io.BytesIO()
    Image.new("RGB", size, (180, 140, 120)).save(buf, "JPEG")
    return buf.getvalue()


def test_a_presenter_is_saved_from_a_photo_and_listed_first(tmp_path, monkeypatch):
    row = presenters.save(photo(), name="Frank Miller", gender="male", folder=tmp_path)
    assert row["id"].startswith("my-frank-miller-") and row["mine"] and Path(row["file"]).is_file()
    assert "reference photo" in row["persona"]
    fake = types.ModuleType("avatar")
    fake.library = lambda gender="": [{"id": "anchor", "gender": "male", "file": "x.png"}]
    monkeypatch.setitem(sys.modules, "avatar", fake)
    monkeypatch.setattr(presenters, "MY_DIR", tmp_path)
    presenters.install()
    assert [r["id"] for r in fake.library("male")] == [row["id"], "anchor"]
    assert [r["id"] for r in fake.library("female")] == ["anchor"], "filtered by voice"
    assert presenters.remove(row["id"], tmp_path) and presenters.rows(tmp_path) == []


def test_unusable_photos_and_missing_details_are_refused(tmp_path):
    with pytest.raises(ValueError, match="name"):
        presenters.save(photo(), name=" ", gender="male", folder=tmp_path)
    with pytest.raises(ValueError, match="male or female"):
        presenters.save(photo(), name="A", gender="robot", folder=tmp_path)
    with pytest.raises(ValueError, match="sharper"):
        presenters.save(photo((200, 200)), name="A", gender="female", folder=tmp_path)
    with pytest.raises(ValueError, match="could not be read"):
        presenters.save(b"not a photo", name="A", gender="female", folder=tmp_path)

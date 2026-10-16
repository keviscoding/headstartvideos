"""QR codes for calls to action (cr_qr), checked by decoding them."""
import random
import string
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
import cr_qr  # noqa: E402


def test_codes_have_the_right_size_and_finder_patterns():
    for text, ver in (("hi", 1), ("https://example.com/" + "a" * 60, 5), ("https://example.com/" + "b" * 190, 10)):
        m = cr_qr.matrix(text)
        assert len(m) == ver * 4 + 17
        finder = [row[:7] for row in m[:7]]
        assert all(finder[0]) and all(finder[6]) and finder[3][2:5] == [True, True, True] and not finder[1][1]
    with pytest.raises(ValueError, match="shorter link"):
        cr_qr.matrix("https://example.com/" + "c" * 400)


def test_codes_decode_back_to_the_link(tmp_path):
    cv2 = pytest.importorskip("cv2")
    readers = [cv2.QRCodeDetector()] + ([cv2.QRCodeDetectorAruco()] if hasattr(cv2, "QRCodeDetectorAruco") else [])
    random.seed(11)
    for n in (8, 30, 64, 100, 150, 200):
        text = "https://example.com/" + "".join(random.choice(string.ascii_letters + string.digits) for _ in range(n))
        img = cv2.imread(str(cr_qr.png(text[:200], tmp_path / f"{n}.png", scale=8)))
        assert any(r.detectAndDecode(img)[0] == text[:200] for r in readers), n

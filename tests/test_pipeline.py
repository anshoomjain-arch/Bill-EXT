"""Slow tests: real PaddleOCR run on the sample bill (run with: pytest -m slow)."""
import cv2
import pytest

from billocr import process_image
from billocr.extract import extract
from billocr.ocr import load_image, run_ocr
from conftest import SAMPLES, normalise

pytestmark = pytest.mark.slow
IMAGE = SAMPLES / "electricity_bill_sample.png"


def test_ocr_then_extract_matches_sample_output(expected):
    img = load_image(IMAGE)
    result = extract(run_ocr(img), img)
    assert normalise(result["fields"]) == expected["fields"]
    assert result["checks"] == expected["checks"]


def test_upscaled_upload_gives_same_result(expected):
    """A larger copy of the same bill (e.g. a higher-resolution scan) is scaled back before OCR."""
    ok, png = cv2.imencode(".png", cv2.resize(cv2.imread(str(IMAGE)), None, fx=2, fy=2,
                                              interpolation=cv2.INTER_CUBIC))
    j = process_image(png.tobytes())
    assert j["summary"]["checks_passed"] == j["summary"]["checks_total"]
    exp = {(s, f): v for s, f, v in expected["fields"]}
    for f in j["fields"]:
        if f["field"] in ("Bill Date", "Total Payable Amount", "Consumer Name", "Net Billed Units"):
            want = exp[f["section"], f["field"]]
            want = f"{want.day}-{want:%b-%Y}" if hasattr(want, "day") else want
            assert f["value"] == want

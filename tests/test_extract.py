"""Fast tests: extraction rules on the saved OCR result (no PaddleOCR needed)."""
import json

import pytest

from billocr.excel import write_xlsx
from billocr.extract import extract, to_json
from billocr.ocr import load_image, read_csv
from conftest import SAMPLES, normalise


@pytest.fixture(scope="module")
def result():
    return extract(read_csv(SAMPLES / "ocr_result.csv"), load_image(SAMPLES / "electricity_bill_sample.png"))


def test_fields_match_sample_output(result, expected):
    got = normalise(result["fields"])
    assert [(s, f) for s, f, _ in got] == [(s, f) for s, f, _ in expected["fields"]]
    for g, e in zip(got, expected["fields"]):
        assert g == e, f"{g[1]}: got {g[2]!r}, expected {e[2]!r}"


def test_checks_match_sample_output(result, expected):
    assert result["checks"] == expected["checks"]
    assert all(c[1] == "PASS" for c in result["checks"])


def test_rules_tolerate_ocr_of_resized_bill(expected):
    """OCR of a 2x upscaled copy scaled back to 596 px: misread labels ("Pavable", "Green Enery")."""
    r = extract(read_csv(SAMPLES / "ocr_result_upscaled.csv"), None)
    for g, e in zip(normalise(r["fields"]), expected["fields"]):
        if g[1] == "Connection Type / Tariff":   # the bill's "Domestic..." is read as "Domestice"
            assert g[2].startswith(e[2])
            continue
        assert g == e, f"{g[1]}: got {g[2]!r}, expected {e[2]!r}"
    assert all(c[1] == "PASS" for c in r["checks"]), [c for c in r["checks"] if c[1] != "PASS"]


def test_excel_roundtrip(result, expected, tmp_path):
    from openpyxl import load_workbook
    out = tmp_path / "out.xlsx"
    write_xlsx(result, out)
    rows = [tuple(r) for r in load_workbook(out)["Sheet1"].iter_rows(min_row=2, values_only=True)]
    assert rows == expected["fields"]


def test_json_is_serialisable_and_formatted(result):
    j = to_json(result)
    json.dumps(j)
    by_name = {(f["section"], f["field"]): f["value"] for f in j["fields"]}
    assert by_name["2. Consumer & Connection", "Account Number"] == "1257530000"
    assert by_name["3. Billing & Payment", "Bill Date"] == "1-Sep-2025"
    assert by_name["3. Billing & Payment", "Total Payable Amount"] == 16599
    assert by_name["4. Electricity Charges", "Energy Charges - Gross"] == 5540.35
    assert j["summary"]["checks_passed"] == j["summary"]["checks_total"] == 14


def test_empty_ocr_does_not_crash():
    r = extract([], None)
    assert any(c[0] == "No required field is empty" and c[1] == "REVIEW" for c in r["checks"])


def test_bad_image_bytes_rejected():
    with pytest.raises(ValueError):
        load_image(b"not an image")


def test_image_scaled_to_reference_width():
    import cv2
    img = load_image(SAMPLES / "electricity_bill_sample.png")
    big = cv2.resize(img, None, fx=2, fy=2)
    assert load_image(big).shape == img.shape

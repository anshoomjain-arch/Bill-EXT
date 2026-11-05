"""
Web UI end-to-end in a headless browser against scripts/mock_api.py (canned OCR results).

Browser: Microsoft Edge on Windows (always installed there), Playwright's bundled Chromium
elsewhere (run `python -m playwright install chromium` once). Override with BILL_TEST_BROWSER=
msedge | chrome | chromium.
"""
import os
import sys

import pytest

from conftest import ROOT, SAMPLES

pw = pytest.importorskip("playwright.sync_api")
sys.path.insert(0, str(ROOT / "scripts"))
import mock_api  # noqa: E402

PORT = 8766
IMAGE = SAMPLES / "electricity_bill_sample.png"


@pytest.fixture(scope="module")
def server():
    mock_api.PORT = PORT
    srv = mock_api.serve(PORT)
    yield f"http://127.0.0.1:{PORT}"
    srv.shutdown()


@pytest.fixture(scope="module")
def browser():
    with pw.sync_playwright() as p:
        channel = os.environ.get("BILL_TEST_BROWSER") or ("msedge" if sys.platform == "win32" else "chromium")
        b = p.chromium.launch(channel=None if channel == "chromium" else channel, headless=True)
        yield b
        b.close()


def open_page(browser, server, single):
    mock_api.SINGLE = single
    ctx = browser.new_context(accept_downloads=True)
    pg = ctx.new_page()
    pg.add_init_script("window.BILL_POLL_MS = 200;")
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(server)
    return ctx, pg, errors


@pytest.fixture
def page(browser, server):
    """Pipeline 1 only - how the live site behaves without BILL_QWEN_API_URL."""
    ctx, pg, errors = open_page(browser, server, single=True)
    yield pg
    ctx.close()
    assert not errors, errors


@pytest.fixture
def dual(browser, server):
    """Both pipelines."""
    ctx, pg, errors = open_page(browser, server, single=False)
    yield pg
    ctx.close()
    mock_api.SINGLE = False
    assert not errors, errors


def upload(page, **file):
    page.set_input_files("#file", **file) if file else page.set_input_files("#file", str(IMAGE))
    page.click("#submit")


def table(page):
    rows = page.locator("#fields tbody tr")
    return {tuple(r[:2]): r[2:] for r in (rows.nth(i).locator("td").all_inner_texts() for i in range(rows.count()))}


# ---------------------------------------------------------------- pipeline 1 only
def test_upload_shows_table_matching_sample_output(page, expected):
    upload(page)
    page.wait_for_selector("#results:not([hidden])", timeout=15000)
    assert page.locator("#fields thead th").all_inner_texts() == ["Section", "Field Name", "Extracted Value"]
    got = table(page)
    assert len(got) == len(expected["fields"]) == 69
    assert got["2. Consumer & Connection", "Account Number"] == ["1257530000"]
    assert got["2. Consumer & Connection", "Consumer Name"] == ["Mrs. RANJANA GUPTA"]
    assert got["3. Billing & Payment", "Bill Date"] == ["1-Sep-2025"]
    assert got["3. Billing & Payment", "Total Payable Amount"] == ["16,599"]
    assert got["4. Electricity Charges", "Energy Charges - Gross"] == ["5,540.35"]
    assert got["4. Electricity Charges", "Green Energy Charges - Subsidy"] == ["—"]
    assert page.inner_text("#summary") == "63 values filled"
    assert page.locator("#llm").is_hidden() and page.locator("#pipelines").is_hidden()
    assert page.inner_text("#status").startswith("Done")


def test_corrupt_image_shows_error(page):
    upload(page, files={"name": "bill.png", "mimeType": "image/png", "buffer": b"not an image"})
    page.wait_for_selector("#status.error", timeout=15000)
    assert "Could not read the image" in page.inner_text("#status")
    assert page.locator("#results").is_hidden()


def test_pdf_rejected_before_upload(page):
    page.set_input_files("#file", files={"name": "bill.pdf", "mimeType": "application/pdf", "buffer": b"%PDF"})
    assert page.locator("#submit").is_disabled()
    assert "PNG or JPEG" in page.inner_text("#status")


def test_csv_download(page):
    upload(page)
    page.wait_for_selector("#results:not([hidden])", timeout=15000)
    with page.expect_download() as d:
        page.click("#download")
    text = open(d.value.path(), encoding="utf-8-sig").read().splitlines()
    assert text[0] == '"Section","Field Name","Extracted Value"'
    assert '"2. Consumer & Connection","Account Number","1257530000"' in text
    assert len(text) == 70


# ---------------------------------------------------------------- both pipelines
def wait_both_done(pg):
    # finished = status text present and no spinner (covers done and any error message)
    pg.wait_for_function("['status-rules','status-qwen'].every(id => { const e = document.getElementById(id);"
                         " return e.textContent && !e.querySelector('.spinner'); })", timeout=20000)


def test_dual_compares_both_pipelines(dual):
    upload(dual)
    wait_both_done(dual)
    assert dual.inner_text("#status-rules").startswith("Done")
    assert dual.inner_text("#status-qwen").startswith("Done")
    assert dual.locator("#fields thead th").all_inner_texts() == [
        "Section", "Field Name", "Rules (pipeline 1)", "Qwen (pipeline 2)", "Match"]
    got = table(dual)
    assert len(got) == 69
    assert got["3. Billing & Payment", "Total Payable Amount"] == ["16,599", "16,599", "✓"]
    addr = got["2. Consumer & Connection", "Consumer Address"]
    assert addr[1].endswith("UP IND") and addr[2] == "✗"
    assert got["3. Billing & Payment", "Billing Period (Months)"] == ["1", "—", "✗"]
    assert dual.locator("tr.differ-row").count() == 2
    assert dual.inner_text("#summary") == ("Rules: 63 values filled · Qwen: 62 values filled · "
                                           "same value on 67/69 fields")


def test_dual_shows_llm_token_usage(dual):
    upload(dual)
    wait_both_done(dual)
    assert dual.locator("#llm").is_visible()
    assert dual.inner_text("#llm-model") == "qwen.qwen3-32b-v1:0"
    assert dual.inner_text("#llm-calls") == "3 (majority vote)"
    assert dual.inner_text("#llm-in") == "6,123"
    assert dual.inner_text("#llm-out") == "1,456"
    assert dual.inner_text("#llm-total") == "7,579"
    assert dual.inner_text("#llm-time") == "41.2 s"
    assert dual.inner_text("#llm-ocr") == "1.0 s"


def test_dual_csv_has_both_columns(dual):
    upload(dual)
    wait_both_done(dual)
    with dual.expect_download() as d:
        dual.click("#download")
    text = open(d.value.path(), encoding="utf-8-sig").read().splitlines()
    assert text[0] == '"Section","Field Name","Rules (pipeline 1)","Qwen (pipeline 2)"'
    assert '"3. Billing & Payment","Billing Period (Months)","1",""' in text


def test_qwen_failure_does_not_block_rules_result(dual):
    dual.route("**/qwen/jobs", lambda route: route.fulfill(
        status=500, content_type="application/json", body='{"error": "Pipeline 2 is down"}'))
    upload(dual)
    wait_both_done(dual)
    assert dual.inner_text("#status-rules").startswith("Done")
    assert "Pipeline 2 is down" in dual.inner_text("#status-qwen")
    got = table(dual)
    assert got["3. Billing & Payment", "Total Payable Amount"][0] == "16,599"
    assert dual.locator("#llm").is_hidden()
    assert dual.inner_text("#summary") == "Rules: 63 values filled"

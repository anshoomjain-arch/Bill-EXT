"""OCR Lambda handler with a fake S3 client and a stubbed OCR step (fast, no AWS, no PaddleOCR)."""
import io
import json
import sys

import pytest

from conftest import ROOT

sys.path.insert(0, str(ROOT / "container" / "ocr"))
import app  # noqa: E402


class FakeS3:
    def __init__(self, objects):
        self.objects = dict(objects)

    def get_object(self, Bucket, Key):
        data = self.objects[Bucket, Key]
        return {"Body": io.BytesIO(data), "ContentLength": len(data)}

    def put_object(self, Bucket, Key, Body, ContentType):
        self.objects[Bucket, Key] = Body


def s3_event(bucket, key):
    return {"Records": [{"s3": {"bucket": {"name": bucket}, "object": {"key": key}}}]}


@pytest.fixture
def fake_s3(monkeypatch):
    s3 = FakeS3({("b", "uploads/job-1.png"): b"png-bytes", ("b", "uploads/job-2.png"): b"broken"})
    monkeypatch.setattr(app, "_s3", s3)

    def fake_process(data):
        if data == b"broken":
            raise ValueError("Could not read the image (unsupported or corrupt file)")
        return {"fields": [], "checks": [], "flagged": [], "summary": {"checks_passed": 0}}
    monkeypatch.setattr(app, "process_image", fake_process)
    return s3


def test_success_writes_result_json(fake_s3):
    out = app.handler(s3_event("b", "uploads/job-1.png"))
    assert out[0]["status"] == "done"
    saved = json.loads(fake_s3.objects["b", "results/job-1.json"])
    assert saved["job_id"] == "job-1" and saved["status"] == "done" and "fields" in saved["result"]


def test_failure_writes_error_json(fake_s3):
    app.handler(s3_event("b", "uploads/job-2.png"))
    saved = json.loads(fake_s3.objects["b", "results/job-2.json"])
    assert saved["status"] == "error" and "corrupt" in saved["error"]


def test_url_encoded_key(fake_s3):
    fake_s3.objects["b", "uploads/my bill.png"] = b"png-bytes"
    app.handler(s3_event("b", "uploads/my+bill.png"))
    assert ("b", "results/my bill.json") in fake_s3.objects


def test_oversized_upload_rejected(fake_s3, monkeypatch):
    monkeypatch.setattr(app, "MAX_BYTES", 3)
    app.handler(s3_event("b", "uploads/job-1.png"))
    assert json.loads(fake_s3.objects["b", "results/job-1.json"])["status"] == "error"

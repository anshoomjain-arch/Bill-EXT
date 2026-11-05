"""API Lambda with a fake S3 for results and real (offline) presigning with dummy credentials."""
import importlib.util
import json

from datetime import datetime, timedelta, timezone

import boto3
import pytest
from botocore.exceptions import ClientError

NOW = datetime.now(timezone.utc)

from conftest import ROOT

spec = importlib.util.spec_from_file_location("api_app", ROOT / "api" / "app.py")
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)


class FakeS3:
    """Presigning is done by a real boto3 client (pure local signing, no network)."""
    def __init__(self, objects):
        self.objects = objects
        self.real = boto3.client("s3", region_name="us-east-1", aws_access_key_id="AKIAFAKE",
                                 aws_secret_access_key="fake")

    def generate_presigned_post(self, **kw):
        return self.real.generate_presigned_post(**kw)

    def get_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        import io
        return {"Body": io.BytesIO(self.objects[Bucket, Key])}

    def head_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        return {}

    def list_objects_v2(self, Bucket, Prefix):
        return {"Contents": [{"Key": k, "LastModified": self.modified.get(k, NOW)}
                             for b, k in self.objects if b == Bucket and k.startswith(Prefix)]}

    modified = {}


@pytest.fixture
def fake(monkeypatch):
    s3 = FakeS3({})
    monkeypatch.setattr(api, "_s3", s3)
    monkeypatch.setattr(api, "BUCKET", "bills-bucket")
    return s3


def call(route, body=None, job_id=None):
    r = api.handler({"routeKey": route, "body": json.dumps(body) if body is not None else None,
                     "pathParameters": {"job_id": job_id} if job_id else None})
    return r["statusCode"], json.loads(r["body"])


def test_create_job_returns_presigned_post(fake):
    code, body = call("POST /jobs", {"content_type": "image/png"})
    assert code == 201
    job_id, up = body["job_id"], body["upload"]
    assert api.JOB_ID.fullmatch(job_id)
    assert up["url"].startswith("https://bills-bucket.s3.amazonaws.com")
    assert up["fields"]["key"] == f"uploads/{job_id}.png"
    assert up["fields"]["Content-Type"] == "image/png"
    policy = json.loads(__import__("base64").b64decode(up["fields"]["policy"]))
    assert ["content-length-range", 1, 10 * 1024 * 1024] in policy["conditions"]


@pytest.mark.parametrize("body", [{"content_type": "application/pdf"}, {}, None])
def test_create_job_rejects_other_types(fake, body):
    assert call("POST /jobs", body)[0] == 400


def test_bad_json_rejected(fake):
    r = api.handler({"routeKey": "POST /jobs", "body": "{nope"})
    assert r["statusCode"] == 400


def test_get_job_processing_then_done(fake):
    jid = "a" * 32
    assert call("GET /jobs/{job_id}", job_id=jid) == (200, {"job_id": jid, "status": "processing"})
    fake.objects["bills-bucket", f"results/{jid}.json"] = json.dumps(
        {"job_id": jid, "status": "done", "result": {"fields": []}}).encode()
    code, body = call("GET /jobs/{job_id}", job_id=jid)
    assert code == 200 and body["status"] == "done"


@pytest.mark.parametrize("jid", ["../secret", "UPLOADS", "a" * 31, "results/x"])
def test_get_job_rejects_bad_ids(fake, jid):
    assert call("GET /jobs/{job_id}", job_id=jid)[0] == 404


def test_busy_when_two_jobs_running(fake):
    for j in ("a" * 32, "b" * 32):
        fake.objects["bills-bucket", f"uploads/{j}.png"] = b"x"
    code, body = call("POST /jobs", {"content_type": "image/png"})
    assert code == 429 and "try again" in body["error"]


def test_finished_and_old_uploads_do_not_count(fake):
    fake.objects["bills-bucket", f"uploads/{'a' * 32}.png"] = b"x"            # running
    fake.objects["bills-bucket", f"uploads/{'b' * 32}.png"] = b"x"            # finished
    fake.objects["bills-bucket", f"results/{'b' * 32}.json"] = b"{}"
    fake.objects["bills-bucket", f"uploads/{'c' * 32}.png"] = b"x"            # older than 16 min
    fake.modified = {f"uploads/{'c' * 32}.png": NOW - timedelta(minutes=30)}
    assert call("POST /jobs", {"content_type": "image/png"})[0] == 201


def test_unknown_route(fake):
    assert call("DELETE /jobs")[0] == 404

"""
API Lambda (zip, behind API Gateway HTTP API).

POST /jobs              {"content_type": "image/png" | "image/jpeg"}
    -> 201 {"job_id": ..., "upload": {"url": ..., "fields": {...}}}
       The browser uploads the bill straight to S3 with that presigned POST form
       (max 10 MB, enforced by S3). The upload triggers the OCR Lambda.
GET  /jobs/{job_id}
    -> 200 {"job_id": ..., "status": "processing"}         until the OCR Lambda finishes
    -> 200 contents of results/<job_id>.json                status "done" or "error"
"""
import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone

import boto3
from botocore.exceptions import ClientError

BUCKET = os.environ.get("BUCKET", "")
MAX_BYTES = 10 * 1024 * 1024
UPLOAD_TTL = 600   # seconds the upload form stays valid
TYPES = {"image/png": "png", "image/jpeg": "jpg"}
JOB_ID = re.compile(r"[0-9a-f]{32}")
# Cost / shared-account guard: the account's Lambda concurrency (10) can't be reserved per function,
# so new jobs are refused while this many bills are still being read (OCR runs up to 15 min).
MAX_ACTIVE_JOBS = int(os.environ.get("MAX_ACTIVE_JOBS", "2"))
ACTIVE_WINDOW = timedelta(minutes=16)

_s3 = None


def s3():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3")
    return _s3


def response(status, body):
    return {"statusCode": status, "headers": {"Content-Type": "application/json"}, "body": json.dumps(body)}


def result_exists(job_id):
    try:
        s3().head_object(Bucket=BUCKET, Key=f"results/{job_id}.json")
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] in ("NoSuchKey", "404", "NotFound"):
            return False
        raise


def active_jobs():
    """Uploads from the last 16 minutes that have no result yet (= OCR still running)."""
    since = datetime.now(timezone.utc) - ACTIVE_WINDOW
    recent = s3().list_objects_v2(Bucket=BUCKET, Prefix="uploads/").get("Contents", [])
    ids = [o["Key"].rsplit("/", 1)[-1].rsplit(".", 1)[0] for o in recent if o["LastModified"] >= since]
    return sum(1 for j in ids if not result_exists(j))


def create_job(event):
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return response(400, {"error": "Body must be JSON"})
    content_type = body.get("content_type")
    if content_type not in TYPES:
        return response(400, {"error": "Only PNG or JPEG images are accepted"})
    if active_jobs() >= MAX_ACTIVE_JOBS:
        return response(429, {"error": f"{MAX_ACTIVE_JOBS} bills are already being read. "
                                       "Please try again in a few minutes."})
    job_id = uuid.uuid4().hex
    upload = s3().generate_presigned_post(
        Bucket=BUCKET,
        Key=f"uploads/{job_id}.{TYPES[content_type]}",
        Fields={"Content-Type": content_type},
        Conditions=[{"Content-Type": content_type}, ["content-length-range", 1, MAX_BYTES]],
        ExpiresIn=UPLOAD_TTL,
    )
    return response(201, {"job_id": job_id, "upload": upload})


def get_job(job_id):
    if not JOB_ID.fullmatch(job_id or ""):
        return response(404, {"error": "Unknown job"})
    try:
        obj = s3().get_object(Bucket=BUCKET, Key=f"results/{job_id}.json")
    except ClientError as e:
        if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
            return response(200, {"job_id": job_id, "status": "processing"})
        raise
    return response(200, json.loads(obj["Body"].read()))


def handler(event, context=None):
    route = event.get("routeKey", "")
    if route == "POST /jobs":
        return create_job(event)
    if route == "GET /jobs/{job_id}":
        return get_job((event.get("pathParameters") or {}).get("job_id"))
    return response(404, {"error": "Not found"})

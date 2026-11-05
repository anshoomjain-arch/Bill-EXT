"""
OCR Lambda (container image).

Triggered by S3 when a bill is uploaded to   uploads/<job_id>.<ext>
Writes the result (or the error) to          results/<job_id>.json
    {"job_id": ..., "status": "done",  "result": {fields, checks, flagged, summary}}
    {"job_id": ..., "status": "error", "error": "..."}

For local testing (docker run + Lambda emulator) it can also be invoked directly with
    {"image_b64": "<base64 image>"}   -> returns the result dict, no S3 involved
"""
import base64
import json
import os
import time
from urllib.parse import unquote_plus

from billocr import process_image

RESULTS_PREFIX = os.environ.get("RESULTS_PREFIX", "results/")
MAX_BYTES = 10 * 1024 * 1024

_s3 = None


def s3():
    global _s3
    if _s3 is None:
        import boto3
        _s3 = boto3.client("s3")
    return _s3


def job_id_from_key(key):
    return key.rsplit("/", 1)[-1].rsplit(".", 1)[0]


def put_result(bucket, job_id, body):
    s3().put_object(Bucket=bucket, Key=f"{RESULTS_PREFIX}{job_id}.json",
                    Body=json.dumps(body).encode(), ContentType="application/json")


def handle_upload(bucket, key):
    job_id = job_id_from_key(key)
    started = time.time()
    try:
        obj = s3().get_object(Bucket=bucket, Key=key)
        if obj["ContentLength"] > MAX_BYTES:
            raise ValueError("Image is larger than 10 MB")
        result = process_image(obj["Body"].read())
        body = {"job_id": job_id, "status": "done", "result": result}
    except Exception as e:   # the UI shows this message instead of polling forever
        print(f"job {job_id} failed: {type(e).__name__}: {e}")
        body = {"job_id": job_id, "status": "error", "error": str(e) or type(e).__name__}
    body["seconds"] = round(time.time() - started, 1)
    put_result(bucket, job_id, body)
    print(f"job {job_id}: {body['status']} in {body['seconds']}s")
    return body


def handler(event, context=None):
    if "image_b64" in event:
        return process_image(base64.b64decode(event["image_b64"]))
    return [handle_upload(r["s3"]["bucket"]["name"], unquote_plus(r["s3"]["object"]["key"]))
            for r in event.get("Records", [])]

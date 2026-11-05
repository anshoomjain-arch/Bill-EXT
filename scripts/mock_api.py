"""
Local stand-in for both backends (API Gateway + S3 + OCR Lambda), to try the web UI without AWS.

    python scripts/mock_api.py            # instant canned results for both pipelines
    python scripts/mock_api.py --single   # only pipeline 1 (as the site works without BILL_QWEN_API_URL)
    python scripts/mock_api.py --real     # real PaddleOCR (+ real Qwen on Bedrock for pipeline 2)
Then open http://localhost:8765

Pipeline 1 is served at /jobs, pipeline 2 at /qwen/jobs. The canned pipeline 2 answer is the
pipeline 1 result with two values changed and made-up token counts, to exercise the comparison.
"""
import copy
import json
import sys
import threading
import uuid
from email.parser import BytesParser
from email.policy import HTTP
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from billocr.extract import extract, to_json  # noqa: E402
from billocr.ocr import load_image, read_csv  # noqa: E402

PORT = 8765
REAL = "--real" in sys.argv
SINGLE = "--single" in sys.argv
PROCESSING_POLLS = 1           # polls answered "processing" before the canned result appears
jobs = {}                      # job_id -> {"pipeline", "polls", "result"}

FAKE_LLM = {"model_id": "qwen.qwen3-32b-v1:0", "calls": 3, "usable_answers": 3,
            "input_tokens": 6123, "output_tokens": 1456,
            "total_tokens": 7579, "latency_ms": 41234, "seconds": 41.5, "stop_reason": "end_turn"}


def canned(pipeline, img):
    result = to_json(extract(read_csv(ROOT / "samples" / "ocr_result.csv"), img))
    if pipeline == "rules":
        return result
    q = copy.deepcopy(result)
    for f in q["fields"]:
        if f["field"] == "Consumer Address":
            f["value"] += " KANPUR NAGAR UP IND"
        elif f["field"] == "Billing Period (Months)":
            f["value"] = None
    return {"fields": q["fields"], "summary": {"values_filled": sum(f["value"] is not None for f in q["fields"])},
            "llm": FAKE_LLM, "ocr_seconds": 1.0}


def process(job_id, data):
    pipeline = jobs[job_id]["pipeline"]
    try:
        if REAL:
            if pipeline == "rules":
                from billocr import process_image
                result = process_image(data)
            else:
                import importlib.util
                spec = importlib.util.spec_from_file_location("qwen_app", ROOT / "container" / "qwen" / "app.py")
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                result = mod.process(data)
        else:
            result = canned(pipeline, load_image(data))   # same image validation as the real Lambda
        jobs[job_id]["result"] = {"job_id": job_id, "status": "done", "result": result, "seconds": 1.0}
    except Exception as e:
        jobs[job_id]["result"] = {"job_id": job_id, "status": "error", "error": str(e)}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(ROOT / "web"), **k)

    def log_message(self, *a):
        pass

    def send_json(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def route(self):
        """('/qwen/jobs/x') -> ('qwen', '/jobs/x')"""
        if self.path.startswith("/qwen/"):
            return "qwen", self.path[len("/qwen"):]
        return "rules", self.path

    def do_POST(self):
        pipeline, path = self.route()
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        if path == "/jobs":
            ct = json.loads(body or b"{}").get("content_type")
            if ct not in ("image/png", "image/jpeg"):
                return self.send_json(400, {"error": "Only PNG or JPEG images are accepted"})
            job_id = uuid.uuid4().hex
            jobs[job_id] = {"pipeline": pipeline, "polls": 0, "result": None}
            prefix = "/qwen" if pipeline == "qwen" else ""
            return self.send_json(201, {"job_id": job_id, "upload": {
                "url": f"http://{self.headers['Host']}{prefix}/mock-s3",
                "fields": {"key": f"uploads/{job_id}.png", "Content-Type": ct, "policy": "mock"}}})
        if path == "/mock-s3":
            msg = BytesParser(policy=HTTP).parsebytes(
                b"Content-Type: " + self.headers["Content-Type"].encode() + b"\r\n\r\n" + body)
            parts = {p.get_param("name", header="content-disposition"): p for p in msg.iter_parts()}
            if list(parts)[-1] != "file":
                self.send_response(400)   # S3 ignores fields after the file
                return self.end_headers()
            job_id = parts["key"].get_content().split("/")[1].split(".")[0]
            threading.Thread(target=process, args=(job_id, parts["file"].get_payload(decode=True)),
                             daemon=True).start()
            self.send_response(204)
            return self.end_headers()
        self.send_json(404, {"error": "Not found"})

    def do_GET(self):
        pipeline, path = self.route()
        if path.startswith("/jobs/"):
            job = jobs.get(path.split("/")[2])
            if not job or job["pipeline"] != pipeline:
                return self.send_json(404, {"error": "Unknown job"})
            job["polls"] += 1
            if job["result"] is None or job["polls"] <= PROCESSING_POLLS:
                return self.send_json(200, {"status": "processing"})
            return self.send_json(200, job["result"])
        if path.startswith("/config.js"):
            js = f'window.BILL_API_URL = "";\nwindow.BILL_QWEN_API_URL = "{"" if SINGLE else "/qwen"}";\n'
            data = js.encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            return self.wfile.write(data)
        super().do_GET()


def serve(port=PORT):
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


if __name__ == "__main__":
    mode = "real OCR" if REAL else "canned results"
    print(f"Mock API ({mode}, {'pipeline 1 only' if SINGLE else 'both pipelines'}) on http://localhost:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()

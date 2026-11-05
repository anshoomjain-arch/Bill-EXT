"""
End-to-end test of the deployed backend: create job -> presigned upload -> poll -> compare with
samples/Sample_Output.xlsx.

    python scripts/smoke_test.py https://<api-id>.execute-api.ap-southeast-2.amazonaws.com [image]
"""
import sys
import time
from pathlib import Path

import requests
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
api = sys.argv[1].rstrip("/")
image = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "samples" / "electricity_bill_sample.png"
ctype = "image/png" if image.suffix.lower() == ".png" else "image/jpeg"

r = requests.post(f"{api}/jobs", json={"content_type": ctype}, timeout=30)
print("POST /jobs", r.status_code)
r.raise_for_status()
job = r.json()
up = requests.post(job["upload"]["url"], data=job["upload"]["fields"],
                   files={"file": (image.name, image.read_bytes(), ctype)}, timeout=120)
print("S3 upload", up.status_code)
up.raise_for_status()

started = time.time()
while True:
    time.sleep(15)
    out = requests.get(f"{api}/jobs/{job['job_id']}", timeout=30).json()
    print(f"  {time.time() - started:5.0f}s  {out['status']}", flush=True)
    if out["status"] != "processing":
        break
    if time.time() - started > 20 * 60:
        sys.exit("gave up after 20 minutes")
if out["status"] != "done":
    sys.exit(f"OCR error: {out.get('error')}")


def show(v):
    if hasattr(v, "day"):
        return f"{v.day}-{v:%b-%Y}"
    return int(v) if isinstance(v, float) and v.is_integer() else v


exp = [(s, f, show(v)) for s, f, v in load_workbook(ROOT / "samples" / "Sample_Output.xlsx")["Sheet1"]
       .iter_rows(min_row=2, values_only=True)]
got = [(f["section"], f["field"], f["value"]) for f in out["result"]["fields"]]
diffs = [(g[1], g[2], e[2]) for g, e in zip(got, exp) if g != e]
s = out["result"]["summary"]
checks = f"checks {s['checks_passed']}/{s['checks_total']}, " if "checks_total" in s else ""
print(f"{out.get('seconds')}s in Lambda, {checks}{len(diffs)} fields differ from Sample_Output.xlsx")
if "llm" in out["result"]:      # pipeline 2
    print("LLM:", out["result"]["llm"], "| OCR seconds:", out["result"].get("ocr_seconds"))
for d in diffs:
    print("  DIFF", d)
sys.exit(1 if diffs or len(got) != len(exp) else 0)

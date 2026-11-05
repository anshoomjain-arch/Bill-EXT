"""
Send a bill image to the OCR container running locally under the Lambda emulator and
compare the answer with samples/Sample_Output.xlsx.

    docker run --rm -p 9000:8080 --read-only --tmpfs /tmp:size=512m \
        -e AWS_LAMBDA_FUNCTION_TIMEOUT=900 \
        -v "$PWD/.tools:/aws-lambda" --entrypoint /aws-lambda/aws-lambda-rie \
        ocrfunction:v1 python -m awslambdaric app.handler

AWS_LAMBDA_FUNCTION_TIMEOUT matches the real Lambda (template.yaml). The emulator's default is
300 s, and a slow OCR run on a busy laptop can exceed that (the emulator then returns nothing).
    python scripts/invoke_local.py [image]
"""
import base64
import json
import sys
import time
import urllib.request
from pathlib import Path

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
URL = "http://localhost:9000/2015-03-31/functions/function/invocations"
image = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "samples" / "electricity_bill_sample.png"

payload = json.dumps({"image_b64": base64.b64encode(image.read_bytes()).decode()}).encode()
started = time.time()
with urllib.request.urlopen(urllib.request.Request(URL, data=payload), timeout=900) as r:
    raw = r.read()
print(f"invoke took {time.time() - started:.1f}s")
if not raw.strip():
    sys.exit("Empty response: the emulator timed out or the function crashed - check `docker logs` "
             "and that the container was started with -e AWS_LAMBDA_FUNCTION_TIMEOUT=900")
out = json.loads(raw)
if "fields" not in out:
    sys.exit(f"Lambda error: {json.dumps(out)[:2000]}")


def show(v):   # Excel value -> the JSON display form
    if hasattr(v, "day"):
        return f"{v.day}-{v:%b-%Y}"
    return int(v) if isinstance(v, float) and v.is_integer() else v


exp = [(s, f, show(v)) for s, f, v in load_workbook(ROOT / "samples" / "Sample_Output.xlsx")["Sheet1"]
       .iter_rows(min_row=2, values_only=True)]
got = [(f["section"], f["field"], f["value"]) for f in out["fields"]]
diffs = [(g[1], g[2], e[2]) for g, e in zip(got, exp) if g != e]
print(f"checks {out['summary']['checks_passed']}/{out['summary']['checks_total']}, "
      f"{len(got)} fields, {len(diffs)} differ from Sample_Output.xlsx")
for d in diffs:
    print("  DIFF", d)
sys.exit(1 if diffs or len(got) != len(exp) else 0)

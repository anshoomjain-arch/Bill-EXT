"""
Run pipeline 2's field selection (Qwen3 32B on Bedrock) on a saved OCR result and compare the
69 fields with samples/Sample_Output.xlsx. QWEN_VOTES Bedrock calls per run; no OCR, nothing deployed.
AWS account / profile come from the environment (see scripts/aws_env.py).

    python scripts/eval_qwen.py [ocr_result.csv] [--runs N] [--model <model or inference profile id>]
                                [--region ap-southeast-2]
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
args = sys.argv[1:]
runs = 1
if "--runs" in args:
    i = args.index("--runs")
    runs = int(args[i + 1])
    del args[i:i + 2]
for flag, env in (("--model", "QWEN_MODEL_ID"), ("--region", "BEDROCK_REGION")):
    if flag in args:
        i = args.index(flag)
        os.environ[env] = args[i + 1]
        del args[i:i + 2]

from openpyxl import load_workbook  # noqa: E402

from aws_env import session as aws_session  # noqa: E402
from billocr.ocr import read_csv  # noqa: E402
from pipeline2 import qwen_extract as q  # noqa: E402

csv_path = Path(args[0]) if args else ROOT / "samples" / "ocr_result.csv"
rows = read_csv(csv_path)
client = aws_session().client("bedrock-runtime", region_name=q.REGION)


def show(v):
    if hasattr(v, "day"):
        return f"{v.day}-{v:%b-%Y}"
    return int(v) if isinstance(v, float) and v.is_integer() else v


exp = [(f, show(v)) for _, f, v in load_workbook(ROOT / "samples" / "Sample_Output.xlsx")["Sheet1"]
       .iter_rows(min_row=2, values_only=True)]
same = lambda a, b: (a is None and b is None) or (a is not None and b is not None and
                                                    str(a).strip().lower() == str(b).strip().lower())
scores = []
for run in range(1, runs + 1):
    out = q.extract_with_qwen(rows, client=client)
    diffs = [(f, g["value"], e) for (f, e), g in zip(exp, out["fields"]) if not same(g["value"], e)]
    u = out["llm"]
    scores.append(len(exp) - len(diffs))
    print(f"run {run}: {scores[-1]}/{len(exp)} fields equal to Sample_Output.xlsx | tokens in/out "
          f"{u['input_tokens']}/{u['output_tokens']} | {u['latency_ms']} ms")
    for f, got, want in diffs:
        print(f"  DIFF {f}: qwen={got!r}  expected={want!r}")
if runs > 1:
    print(f"min {min(scores)}, max {max(scores)}, mean {sum(scores) / runs:.1f} of {len(exp)}")

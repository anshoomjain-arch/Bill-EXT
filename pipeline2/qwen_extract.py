"""
OCR rows -> the same 69 fields as pipeline 1 (billocr.extract.ORDER), chosen by Qwen3 32B on Bedrock.

The model gets the OCR text grouped into visual lines with x/y positions, the list of fields and
the value formats, and must answer with one JSON object. Token usage and latency come from the
Bedrock Converse response.
"""
import json
import os
import re
import time
from datetime import datetime

from billocr.extract import ORDER, tidy_person   # reused read-only; pipeline 1 code is not changed

MODEL_ID = os.environ.get("QWEN_MODEL_ID", "qwen.qwen3-32b-v1:0")
REGION = os.environ.get("BEDROCK_REGION", "ap-southeast-2")
MAX_OUTPUT_TOKENS = 4096
# Self-consistency: VOTES calls run in parallel and each field takes the value most of them agree on.
# Single answers vary between calls even at temperature 0 (blank vs 0 in table cells); voting
# removes most of that at VOTES x the token cost, with about the same wall-clock time.
VOTES = int(os.environ.get("QWEN_VOTES", "3"))

FIELDS = [(sec, fld) for sec, fields in ORDER for fld in fields]

DATE_FIELDS = {"Connection Date", "Bill Date", "Due Date", "Disconnection Date",
               "Billing Period Start", "Billing Period End"}
TEXT_FIELDS = {"Account Number", "Consumer Name", "Husband/Father Name", "Consumer Address", "Mobile Number",
               "Email Address", "Connection Type / Tariff", "Sanctioned Load", "Meter Type",
               "Amount in Words", "Payment QR Code", "QR Code Payload"}

FIELD_NOTES = {
    "Account Number": "digits only, as a string",
    "Consumer Name": "with title, e.g. \"Mrs. FIRSTNAME SURNAME\"",
    "Husband/Father Name": "with relation prefix, e.g. \"W/O. NAME\" or \"S/O. NAME\"",
    "Consumer Address": "all address lines joined with single spaces",
    "Mobile Number": "exactly as printed (may be masked with x)",
    "Email Address": "\"Not provided\" if the bill shows none",
    "Connection Type / Tariff": "as printed, without trailing dots",
    "Sanctioned Load": "number and unit, e.g. \"7 KW\"",
    "Billing Period Start": "previous meter reading date (meter reading table)",
    "Billing Period End": "current meter reading date (meter reading table)",
    "Billing Period (Months)": "from the meter reading table",
    "Current Bill Amount": "same as Net Current Bill",
    "Payment QR Code": "\"Present\" if a Scan & Pay QR code area exists, else \"Not found\"",
    "QR Code Payload": "always \"Not decoded\" (the QR image is not part of the OCR text)",
}

SYSTEM = ("You extract fields from the OCR text of an Indian electricity bill (KESCO / UPPCL layout). "
          "Use only values that appear in the OCR text. Answer with a single JSON object and nothing else.")


def ocr_lines(rows, ytol=6):
    """
    Group OCR boxes into visual lines: '[y=458] (x=6-128) Green Energy Charges | (x=232-252) 0.00 | ...'.
    Both edges are given because amounts are right-aligned: columns line up by the right edge.
    """
    boxes = sorted((r for r in rows if r["text"].strip()), key=lambda r: ((r["y1"] + r["y2"]) / 2, r["x1"]))
    lines, cur, cur_y = [], [], None
    for b in boxes:
        cy = (b["y1"] + b["y2"]) / 2
        if cur and abs(cy - cur_y) > ytol:
            lines.append((cur_y, cur))
            cur = []
        if not cur:
            cur_y = cy
        cur.append(b)
    if cur:
        lines.append((cur_y, cur))
    return "\n".join(f"[y={round(y)}] " + " | ".join(f"(x={b['x1']}-{b['x2']}) {b['text'].strip()}"
                                                      for b in sorted(items, key=lambda b: b["x1"]))
                     for y, items in lines)


NUMBER = re.compile(r"-?[\d,]+(\.\d+)?")


def ocr_layout(rows, px_per_char=4, ytol=6):
    """
    Render the OCR boxes as layout-preserving plain text (like `pdftotext -layout`): each visual line
    becomes one text line, and each piece is placed at the character column of its pixel position.
    Numbers are right-aligned at their right edge, text left-aligned at its left edge, so table
    columns line up vertically and an empty cell shows up as blank space.
    """
    boxes = sorted((r for r in rows if r["text"].strip()), key=lambda r: ((r["y1"] + r["y2"]) / 2, r["x1"]))
    lines, cur, cur_y = [], [], None
    for b in boxes:
        cy = (b["y1"] + b["y2"]) / 2
        if cur and abs(cy - cur_y) > ytol:
            lines.append(cur)
            cur = []
        if not cur:
            cur_y = cy
        cur.append(b)
    if cur:
        lines.append(cur)

    out = []
    for items in lines:
        line = ""
        for b in sorted(items, key=lambda b: b["x1"]):
            t = b["text"].strip()
            col = round(b["x2"] / px_per_char) - len(t) if NUMBER.fullmatch(t) else round(b["x1"] / px_per_char)
            col = max(col, len(line) + 1 if line else 0)      # never overwrite the previous piece
            line = line.ljust(col) + t
        out.append(line.rstrip())
    return "\n".join(out)


def field_spec():
    out = []
    for sec, fld in FIELDS:
        kind = "date d-Mon-yyyy" if fld in DATE_FIELDS else "text" if fld in TEXT_FIELDS else "number"
        note = FIELD_NOTES.get(fld, "")
        out.append(f'- "{fld}" ({sec}; {kind}{"; " + note if note else ""})')
    return "\n".join(out)


def build_prompt(rows):
    return f"""/no_think
OCR text of the bill, laid out as on the page: each line of text is one visual line of the bill
and horizontal positions are kept, so table columns line up vertically and an empty table cell
is blank space. Bilingual labels may contain garbled Hindi; use the English part.

{ocr_layout(rows)}

Return a JSON object whose keys are exactly these {len(FIELDS)} field names, in this order:
{field_spec()}

Rules:
1. Copy values exactly as the OCR text shows them. Do not correct spellings or fill in characters
   that are not in the OCR text.
2. One visual line can hold two different tables side by side: the electricity charges table on
   the left (Details | Gross Amount | Subsidy Amount | Net Amount) and the taxes table on the right
   (CGST, SGST, LPSC, Net Current Bill, Subsidy by Govt., Arrear Amount, Payable Amount, ...).
   Never use a value from the right-hand table for a charges field, and vice versa.
3. Amounts are right-aligned under their column header. Read each charges row across: the amount
   under "Gross Amount" is Gross, under "Subsidy Amount" is Subsidy, under "Net Amount" is Net.
4. A blank cell is not zero. Output 0 only when 0 or 0.00 is printed in that column of that row;
   if the space under a header is empty in that row, output null. Example: a charges row with
   only two amounts, under the Gross and Net headers, has Subsidy = null. (A row with three
   amounts has all three cells filled, even when one of them is 0.00.)
5. Numbers: plain JSON numbers without commas or currency signs (e.g. 16599, 5540.35, 0).
6. Dates: "d-Mon-yyyy", e.g. "1-Sep-2025".
7. Use null if a value is not in the OCR text.
"""


def call_bedrock(prompt, client=None):
    """Returns (text, usage dict). Uses the Bedrock Converse API so usage is reported uniformly."""
    if client is None:
        import boto3
        client = boto3.client("bedrock-runtime", region_name=REGION)
    started = time.time()
    resp = client.converse(
        modelId=MODEL_ID,
        system=[{"text": SYSTEM}],
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"maxTokens": MAX_OUTPUT_TOKENS, "temperature": 0},
    )
    text = "".join(c.get("text", "") for c in resp["output"]["message"]["content"])
    u = resp.get("usage", {})
    usage = {
        "model_id": MODEL_ID,
        "input_tokens": u.get("inputTokens"),
        "output_tokens": u.get("outputTokens"),
        "total_tokens": u.get("totalTokens"),
        "latency_ms": resp.get("metrics", {}).get("latencyMs"),
        "seconds": round(time.time() - started, 1),
        "stop_reason": resp.get("stopReason"),
    }
    return text, usage


def parse_json(text):
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("The model did not return a JSON object")
    return json.loads(text[start:end + 1])


def normalise(field, v):
    """Bring model answers into pipeline 1's display format, without correcting their content."""
    if v is None or (isinstance(v, str) and v.strip().lower() in ("", "null", "none", "n/a")):
        return None
    if field in DATE_FIELDS and isinstance(v, str):
        s = re.sub(r"\s+\d{1,2}:\d{2}(:\d{2})?$", "", v.strip())     # "01-AUG-2025 00:00"
        for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d.%m.%Y"):
            try:
                d = datetime.strptime(s, fmt)
                return f"{d.day}-{d:%b-%Y}"
            except ValueError:
                pass
        return v
    if field in TEXT_FIELDS:
        v = str(int(v)) if isinstance(v, float) and v.is_integer() else str(v).strip()
        # same display formatting pipeline 1 applies to these fields
        if field in ("Consumer Name", "Husband/Father Name"):
            v = tidy_person(v)
        elif field == "Connection Type / Tariff":
            v = re.sub(r"[.…]+$", "", v).strip()      # the bill prints it cut short with "..."
        elif field == "Sanctioned Load":
            m = re.fullmatch(r"([\d.]+)\s*kw", v, re.I)
            v = f"{m.group(1)} KW" if m else v
        return v
    if isinstance(v, str):
        s = re.sub(r"[,\s₹]|Rs\.?", "", v)
        try:
            v = float(s)
        except ValueError:
            return v.strip()
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def vote(answers):
    """answers: list of {field: value} -> {field: majority value}; a tie keeps the first answer's value."""
    out = {}
    for _, fld in FIELDS:
        values = [a.get(fld) for a in answers]
        keys = [json.dumps(v, sort_keys=True) for v in values]
        best = max(range(len(values)), key=lambda i: (keys.count(keys[i]), -i))
        out[fld] = values[best]
    return out


def extract_with_qwen(rows, client=None, votes=None):
    """
    OCR rows -> {"fields": [...], "summary": {...}, "llm": {usage summed over all calls}}
    (same field shape as pipeline 1).
    """
    from concurrent.futures import ThreadPoolExecutor
    votes = votes or VOTES
    if client is None:
        import boto3
        client = boto3.client("bedrock-runtime", region_name=REGION)
    prompt = build_prompt(rows)
    started = time.time()
    with ThreadPoolExecutor(max_workers=votes) as pool:
        replies = list(pool.map(lambda _: call_bedrock(prompt, client), range(votes)))
    answers = []
    for text, _ in replies:
        try:
            answers.append({fld: normalise(fld, v) for fld, v in parse_json(text).items()})
        except (ValueError, json.JSONDecodeError):
            pass                                   # one unusable reply is outvoted by the others
    if not answers:
        raise ValueError("The model did not return a JSON object")
    final = vote(answers)
    fields = [{"section": sec, "field": fld, "value": final.get(fld)} for sec, fld in FIELDS]
    usages = [u for _, u in replies]
    total = lambda k: sum(u[k] or 0 for u in usages)
    llm = {
        "model_id": MODEL_ID,
        "calls": votes,
        "usable_answers": len(answers),
        "input_tokens": total("input_tokens"),
        "output_tokens": total("output_tokens"),
        "total_tokens": total("total_tokens"),
        "latency_ms": max(u["latency_ms"] or 0 for u in usages),   # calls run in parallel
        "seconds": round(time.time() - started, 1),
        "stop_reason": usages[0]["stop_reason"],
    }
    return {
        "fields": fields,
        "summary": {"values_filled": sum(1 for f in fields if f["value"] is not None)},
        "llm": llm,
    }

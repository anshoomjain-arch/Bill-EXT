"""Pipeline 2 (Qwen on Bedrock) with a fake Bedrock client - no AWS calls."""
import importlib.util
import io
import json

import pytest

from billocr.ocr import read_csv
from conftest import ROOT, SAMPLES
from pipeline2 import qwen_extract as q

# both Lambda handlers are called app.py - load this one under its own name
_spec = importlib.util.spec_from_file_location("qwen_app", ROOT / "container" / "qwen" / "app.py")
qapp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(qapp)


def expected_display(expected):
    out = {}
    for s, f, v in expected["fields"]:
        if hasattr(v, "day"):
            v = f"{v.day}-{v:%b-%Y}"
        elif isinstance(v, float) and v.is_integer():
            v = int(v)
        out[f] = v
    return out


class FakeBedrock:
    """Answers in turn from `answers` (one string = the same answer every call)."""
    def __init__(self, answers):
        self.answers = [answers] if isinstance(answers, str) else list(answers)
        self.calls = []
        import threading
        self.lock = threading.Lock()

    def converse(self, **kw):
        with self.lock:
            text = self.answers[len(self.calls) % len(self.answers)]
            self.calls.append(kw)
        return {"output": {"message": {"content": [{"text": text}]}},
                "usage": {"inputTokens": 6123, "outputTokens": 1456, "totalTokens": 7579},
                "metrics": {"latencyMs": 41234}, "stopReason": "end_turn"}


@pytest.fixture(scope="module")
def rows():
    return read_csv(SAMPLES / "ocr_result.csv")


@pytest.fixture
def messy_answer(expected):
    """What a model might plausibly return: right values, other formats, thinking + code fence."""
    ans = {}
    for f, v in expected_display(expected).items():
        if f == "Bill Date":
            v = "01-Sep-2025"
        elif f == "Total Payable Amount":
            v = "16,599"
        elif f == "Energy Charges - Gross":
            v = "5540.35"
        elif f == "Account Number":
            v = 1257530000
        ans[f] = v
    return "<think>\n</think>\n```json\n" + json.dumps(ans) + "\n```"


def test_prompt_lists_all_fields_and_ocr_lines(rows):
    p = q.build_prompt(rows)
    assert len(q.FIELDS) == 69
    for _, f in q.FIELDS:
        assert f'"{f}"' in p
    assert "A blank cell is not zero" in p
    # layout text: a charges row with a blank Subsidy cell keeps the gap between Gross and Net
    green = next(l for l in q.ocr_layout(rows).splitlines() if "Green Energy Charges" in l)
    minim = next(l for l in q.ocr_layout(rows).splitlines() if "Minim Charges" in l)
    gross_end = minim.index("0.00") + 4
    assert green[gross_end - 4:gross_end] == "0.00"                     # Gross column lines up
    assert green[gross_end:minim.rindex("0.00")].strip() == ""          # Subsidy cell is blank
    assert p.startswith("/no_think")


def test_answer_parsed_normalised_and_matches_sample(rows, messy_answer, expected):
    fake = FakeBedrock(messy_answer)
    out = q.extract_with_qwen(rows, client=fake)
    got = {f["field"]: f["value"] for f in out["fields"]}
    assert got == expected_display(expected)
    assert [f["field"] for f in out["fields"]] == [f for _, f, _ in expected["fields"]]
    call = fake.calls[0]
    assert call["modelId"] == q.MODEL_ID and call["inferenceConfig"]["temperature"] == 0


def test_token_usage_reported(rows, messy_answer):
    out = q.extract_with_qwen(rows, client=FakeBedrock(messy_answer), votes=1)
    llm = out["llm"]
    assert (llm["input_tokens"], llm["output_tokens"], llm["total_tokens"]) == (6123, 1456, 7579)
    assert llm["latency_ms"] == 41234 and llm["model_id"] == q.MODEL_ID and llm["calls"] == 1


def test_votes_sum_tokens_and_take_majority(rows):
    a = json.dumps({"Bill Date": "1-Sep-2025", "Demand Charges - Subsidy": 0})
    b = json.dumps({"Bill Date": "1-Sep-2025", "Demand Charges - Subsidy": None})   # the odd one out
    fake = FakeBedrock([a, b, a])
    out = q.extract_with_qwen(rows, client=fake, votes=3)
    got = {f["field"]: f["value"] for f in out["fields"]}
    assert got["Demand Charges - Subsidy"] == 0 and got["Bill Date"] == "1-Sep-2025"
    assert len(fake.calls) == 3
    llm = out["llm"]
    assert (llm["calls"], llm["usable_answers"]) == (3, 3)
    assert (llm["input_tokens"], llm["output_tokens"]) == (3 * 6123, 3 * 1456)
    assert llm["latency_ms"] == 41234                       # parallel calls: the slowest one


def test_unusable_reply_is_outvoted(rows):
    good = json.dumps({"Bill Date": "1-Sep-2025"})
    out = q.extract_with_qwen(rows, client=FakeBedrock([good, "Sorry.", good]), votes=3)
    assert out["llm"]["usable_answers"] == 2
    assert {f["field"]: f["value"] for f in out["fields"]}["Bill Date"] == "1-Sep-2025"


@pytest.mark.parametrize("field,raw,want", [
    ("Consumer Name", "Mrs.RANJANA GUPTA", "Mrs. RANJANA GUPTA"),
    ("Husband/Father Name", "W/O.SUNIL KUMAR GUPTA", "W/O. SUNIL KUMAR GUPTA"),
    ("Sanctioned Load", "7kW", "7 KW"),
    ("Connection Type / Tariff", "LMV1 KE Residential / Domestic...", "LMV1 KE Residential / Domestic"),
    ("Billing Period Start", "01-AUG-2025 00:00", "1-Aug-2025"),
    ("Bill Date", "2025-09-01", "1-Sep-2025"),
    ("Total Payable Amount", "₹ 16,599.00", 16599),
    ("Green Energy Charges - Subsidy", None, None),
    ("Consumer Address", "KALYANPUR", "KALYANPUR"),        # content is never corrected
])
def test_normalise_formats_only(field, raw, want):
    assert q.normalise(field, raw) == want


def test_missing_keys_become_null(rows):
    out = q.extract_with_qwen(rows, client=FakeBedrock('{"Bill Date": "1-Sep-2025"}'))
    assert out["summary"]["values_filled"] == 1


def test_non_json_answer_raises(rows):
    with pytest.raises(ValueError):
        q.extract_with_qwen(rows, client=FakeBedrock("Sorry, I cannot help with that."))


# ---- Lambda handler (fake S3, stubbed OCR + Qwen) -------------------------------------
class FakeS3:
    def __init__(self, objects):
        self.objects = dict(objects)

    def get_object(self, Bucket, Key):
        data = self.objects[Bucket, Key]
        return {"Body": io.BytesIO(data), "ContentLength": len(data)}

    def put_object(self, Bucket, Key, Body, ContentType):
        self.objects[Bucket, Key] = Body


def test_handler_writes_result_with_tokens(monkeypatch):
    s3 = FakeS3({("b", "uploads/j1.png"): b"img", ("b", "uploads/j2.png"): b"bad"})
    monkeypatch.setattr(qapp, "_s3", s3)

    def fake_process(data):
        if data == b"bad":
            raise ValueError("The model did not return a JSON object")
        return {"fields": [], "summary": {"values_filled": 0}, "llm": {"input_tokens": 1, "output_tokens": 2},
                "ocr_seconds": 3.0}
    monkeypatch.setattr(qapp, "process", fake_process)
    ev = lambda k: {"Records": [{"s3": {"bucket": {"name": "b"}, "object": {"key": k}}}]}
    qapp.handler(ev("uploads/j1.png"))
    qapp.handler(ev("uploads/j2.png"))
    ok = json.loads(s3.objects["b", "results/j1.json"])
    bad = json.loads(s3.objects["b", "results/j2.json"])
    assert ok["status"] == "done" and ok["result"]["llm"]["output_tokens"] == 2
    assert bad["status"] == "error" and "JSON" in bad["error"]

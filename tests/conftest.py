import sys
from pathlib import Path

import pytest
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"
sys.path.insert(0, str(ROOT))
# slow tests download the OCR models to ~/.paddlex (or $PADDLE_PDX_CACHE_HOME) on first use


@pytest.fixture(scope="session")
def expected():
    """Sample_Output.xlsx -> {"fields": [(section, field, value)], "checks": [(name, result, detail)]}."""
    wb = load_workbook(SAMPLES / "Sample_Output.xlsx")
    fields = [tuple(r) for r in wb["Sheet1"].iter_rows(min_row=2, values_only=True)]
    checks = []
    for r in wb["Checks"].iter_rows(min_row=2, values_only=True):
        if r[0] is None:
            break
        checks.append(tuple(r))
    return {"fields": fields, "checks": checks}


def normalise(fields):
    """Make extract() values comparable with what openpyxl reads back from the sheet."""
    out = []
    for sec, fld, v in fields:
        if fld == "Account Number" and v is not None:
            v = str(int(v))
        elif isinstance(v, float) and v.is_integer():
            v = int(v)
        out.append((sec, fld, v))
    return out

"""Writes an extract() result to an Excel file in the Sample_Output.xlsx layout (+ a Checks sheet)."""
from datetime import datetime

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from .extract import LOW_CONF


def write_xlsx(result, path):
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ARIAL, ARIAL_B = Font(name="Arial", size=10), Font(name="Arial", size=10, bold=True)
    ws.append(["Section", "Field Name", "Extracted Value"])
    for c in ws[1]:
        c.font = ARIAL_B
    for sec, fld, v in result["fields"]:
        ws.append([sec, fld, v])
        cell = ws.cell(ws.max_row, 3)
        for c in ws[ws.max_row]:
            c.font = ARIAL
        if isinstance(v, datetime):
            cell.number_format = "d-mmm-yyyy"
        elif fld == "Account Number" and v is not None:   # keep long IDs exact (no scientific notation)
            cell.value = str(int(v))
            cell.number_format = "@"
        cell.alignment = Alignment(horizontal="left")
    ws.column_dimensions["A"].width = 24
    ws.column_dimensions["B"].width = 34
    ws.column_dimensions["C"].width = 74
    ws.freeze_panes = "A2"

    wc = wb.create_sheet("Checks")
    wc.append(["Check", "Result", "Details"])
    for c in wc[1]:
        c.font = ARIAL_B
    for row in result["checks"]:
        wc.append(list(row))
    red, green = PatternFill("solid", fgColor="F8CBAD"), PatternFill("solid", fgColor="C6E0B4")
    for r in wc.iter_rows(min_row=2):
        for c in r:
            c.font = ARIAL
        r[1].fill = green if r[1].value == "PASS" else red
    wc.append([])
    wc.append(["Fields to double-check (OCR confidence below %.0f%% or special remark)" % (LOW_CONF * 100)])
    wc.cell(wc.max_row, 1).font = ARIAL_B
    for fld, conf, remark in result["flagged"]:
        wc.append([fld, "" if conf is None else conf, remark])
        for c in wc[wc.max_row]:
            c.font = ARIAL
    if not result["flagged"]:
        wc.append(["(none)"])
    wc.column_dimensions["A"].width = 52
    wc.column_dimensions["B"].width = 10
    wc.column_dimensions["C"].width = 70

    wb.save(path)

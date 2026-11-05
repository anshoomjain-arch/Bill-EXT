"""
Turns the raw PaddleOCR result (text + position) into the structured layout
(Section | Field Name | Value) of samples/Sample_Output.xlsx, plus validation checks.

Works for the KESCO / UPPCL (Kanpur) bill layout. Another bill layout needs its
own set of rules (positions and labels), not a new OCR engine.
"""
import re
from datetime import datetime

LOW_CONF = 0.90  # values read with lower confidence get flagged for review
PAYABLE = r"Pa[vy]+a?b[l1I]e"  # OCR sometimes reads "Payable" as "Pavable"

S2, S3, S4, S5 = ("2. Consumer & Connection", "3. Billing & Payment",
                  "4. Electricity Charges", "5. Taxes & Adjustments")

CHARGE_ROWS = [
    ("Energy Charges", lambda t: "Energy Charges" in t and "Green" not in t),
    ("Demand Charges", lambda t: "Demand Charges" in t),
    ("Minimum Charges", lambda t: "Minim" in t),
    ("Green Energy Charges", lambda t: re.search(r"Green\s*En\w*\s*Charges", t) is not None),
    ("Electricity Duty", lambda t: "Electricity Duty" in t),
    ("Excess Demand Penalty", lambda t: "Excess Demand" in t),
    ("Low PF Surcharge", lambda t: "Low PF" in t),
    ("LT Metering Energy Surcharge", lambda t: "LT Metering" in t),
    ("Miscellaneous Charges", lambda t: re.search(r"Mis[ce]\.?\s*Charges", t) is not None),
]

# output order = Sample_Output.xlsx
ORDER = [
    (S2, ["Account Number", "Consumer Name", "Husband/Father Name", "Consumer Address", "Mobile Number",
          "Email Address", "Connection Date", "Connection Type / Tariff", "Supply Type", "Sanctioned Load",
          "Billed Demand", "Security Deposit", "Meter Type", "Meter Number", "Inoperative Amount",
          "Progressive Subsidy", "Billing Latitude", "Billing Longitude"]),
    (S3, ["Bill Date", "Due Date", "Disconnection Date", "Billing Period Start", "Billing Period End",
          "Billing Period (Months)", "Net Billed Units", "Current Bill Amount", "Arrear Amount",
          "Due Date Rebate", "Total Payable Amount", "Payable by Due Date", "Amount in Words",
          "Payment QR Code", "QR Code Payload"]),
    (S4, [f"{n} - {c}" for n, _ in CHARGE_ROWS for c in ("Gross", "Subsidy", "Net")]),
    (S5, ["CGST", "SGST", "Current LPSC", "Previous LPSC", "Net Current Bill", "Subsidy by Government",
          "Subsidized Bill", "Arrear Amount", "Final Payable Amount"]),
]


# ---------------------------------------------------------------- pure helpers
def is_money(t):
    return re.fullmatch(r"-?[\d,]+(\.\d+)?", t) is not None


def is_date(t):
    return re.fullmatch(r"\d{2}-[A-Za-z]{3}-\d{4}", t) is not None


def to_num(t):
    return float(t.replace(",", ""))


def parse_date(s):
    try:
        return datetime.strptime(s.upper().replace(".", "-").replace("--", "-"), "%d-%b-%Y")
    except Exception:
        return None


def tidy_person(s):
    s = re.sub(r"^(Mrs|Mr|Ms|Smt|Shri)\.?\s*", lambda m: m.group(1) + ". ", s, flags=re.I)
    s = re.sub(r"^(W/O|S/O|D/O|C/O)\.?\s*", lambda m: m.group(1).upper() + ". ", s, flags=re.I)
    return s.strip()


def decode_qr(img):
    """Try to read the payment QR code; img is a BGR array scaled to the reference width."""
    try:
        import cv2
        h, w = img.shape[:2]
        crop = img[int(h * 0.29):int(h * 0.40), int(w * 0.72):w]       # QR area on this layout
        for scale in (4, 6, 3):
            big = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
            text, pts, _ = cv2.QRCodeDetector().detectAndDecode(big)
            if text:
                return text
    except Exception:
        pass
    return "Not decoded"


# ---------------------------------------------------------------- main
def extract(rows, image=None):
    """
    rows:  OCR rows (dicts with text, confidence, x1, y1, x2, y2)
    image: BGR array of the bill (scaled to the reference width), only used for the QR code

    Returns {"fields": [(section, field, value)], "checks": [(name, PASS|REVIEW, detail)],
             "flagged": [(field, confidence or None, remark)]}
    """
    items = []
    for r in rows:
        t = r["text"].strip()
        if not t:
            continue
        items.append(dict(t=t, c=float(r["confidence"]), x1=r["x1"], y1=r["y1"], x2=r["x2"], y2=r["y2"],
                          cy=(r["y1"] + r["y2"]) / 2))

    confidence = {}   # field name -> lowest OCR confidence of the pieces used
    notes = {}        # field name -> remark shown on the Checks sheet

    def in_region(it, xr=None, yr=None):
        if xr and not (xr[0] <= it["x1"] <= xr[1]):
            return False
        if yr and not (yr[0] <= it["y1"] <= yr[1]):
            return False
        return True

    def find_label(pattern, xr=None, yr=None, pred=None):
        """First OCR box whose text matches the (English) label."""
        hits = [i for i in items if re.search(pattern, i["t"], re.I) and in_region(i, xr, yr)
                and (pred is None or pred(i["t"]))]
        return sorted(hits, key=lambda i: (i["y1"], i["x1"]))[0] if hits else None

    def value_right(label, xr, check=lambda t: True, ytol=9):
        """Box on the same line as `label`, inside the x-window `xr`, passing `check`."""
        if label is None:
            return None
        cands = [i for i in items if i is not label and xr[0] <= i["x1"] <= xr[1]
                 and abs(i["cy"] - label["cy"]) <= ytol and check(i["t"])]
        return sorted(cands, key=lambda i: (abs(i["cy"] - label["cy"]), i["x1"]))[0] if cands else None

    def grab(field, label_pat, label_xr, label_yr, val_xr, check=lambda t: True, pred=None, ytol=9):
        lab = find_label(label_pat, label_xr, label_yr, pred)
        val = value_right(lab, val_xr, check, ytol)
        if val:
            confidence[field] = val["c"]
        return val["t"] if val else None

    def money(field, *a, **k):
        t = grab(field, *a, check=is_money, **k)
        return to_num(t) if t is not None else None

    F = {}   # (section, field) -> value
    CONS = (60, 240)          # y-range of the consumer block
    LEFT_V, RIGHT_V = (135, 290), (415, 600)

    # ---- 2. Consumer & connection --------------------------------------
    m = next((re.search(r"Account No\.?:?\s*(\d+)", i["t"]) for i in items
              if re.search(r"Account No", i["t"], re.I)), None)
    acc = next((i for i in items if re.search(r"Account No", i["t"], re.I)), None)
    if acc:
        confidence["Account Number"] = acc["c"]
    F[S2, "Account Number"] = float(m.group(1)) if m else None

    F[S2, "Consumer Name"] = (lambda v: tidy_person(v) if v else None)(
        grab("Consumer Name", r"Name\s*:", (0, 130), (80, 100), LEFT_V))
    F[S2, "Husband/Father Name"] = (lambda v: tidy_person(v) if v else None)(
        grab("Husband/Father Name", r"Husband", (0, 130), (92, 110), LEFT_V))

    addr_lines = sorted([i for i in items if LEFT_V[0] <= i["x1"] <= 295 and 108 <= i["y1"] <= 136],
                        key=lambda i: i["y1"])
    F[S2, "Consumer Address"] = " ".join(i["t"] for i in addr_lines) if addr_lines else None
    if addr_lines:
        confidence["Consumer Address"] = min(i["c"] for i in addr_lines)
        notes["Consumer Address"] = f"{len(addr_lines)} address line(s) read - check for a missing last line"

    F[S2, "Mobile Number"] = grab("Mobile Number", r"Mobile No", (0, 130), CONS, LEFT_V)
    em = grab("Email Address", r"Email\s*:", (0, 130), CONS, LEFT_V)
    F[S2, "Email Address"] = em if em else "Not provided"
    F[S2, "Connection Date"] = (lambda v: parse_date(v) if v else None)(
        grab("Connection Date", r"Connection Date", (0, 130), CONS, LEFT_V, check=is_date))

    tariff = grab("Connection Type / Tariff", r"Tariff", (280, 360), CONS, RIGHT_V)
    F[S2, "Connection Type / Tariff"] = re.sub(r"[.…]+$", "", tariff) if tariff else None
    if tariff and tariff.endswith("."):
        notes["Connection Type / Tariff"] = "The bill itself prints this text cut short with '...'"

    F[S2, "Supply Type"] = (lambda v: float(v) if v else None)(
        grab("Supply Type", r"Supply Type", (280, 360), CONS, RIGHT_V, check=lambda t: t.isdigit()))
    sl = grab("Sanction Load", r"Sanction Load", (280, 360), CONS, RIGHT_V)
    mm = re.search(r"([\d.]+)\s*kw", sl or "", re.I)
    F[S2, "Sanctioned Load"] = f"{mm.group(1)} KW" if mm else sl
    F[S2, "Billed Demand"] = money("Billed Demand", r"Billed Demand", (280, 360), CONS, RIGHT_V)
    F[S2, "Security Deposit"] = money("Security Deposit", r"Security Deposit", (280, 360), CONS, RIGHT_V)
    F[S2, "Meter Type"] = grab("Meter Type", r"Meter Type", (0, 130), CONS, LEFT_V)
    F[S2, "Meter Number"] = (lambda v: float(v) if v else None)(
        grab("Meter Number", r"Meter Number", (280, 400), CONS, RIGHT_V, check=lambda t: t.isdigit()))
    F[S2, "Inoperative Amount"] = money("Inoperative Amount", r"Inoperative", (0, 130), CONS, LEFT_V)
    F[S2, "Progressive Subsidy"] = money("Progressive Subsidy", r"Progressive Subsidy", (0, 130), CONS, LEFT_V)
    F[S2, "Billing Latitude"] = money("Billing Latitude", r"Billing Latitude", (280, 410), CONS, RIGHT_V)
    F[S2, "Billing Longitude"] = money("Billing Longitude", r"Billing Longitude", (280, 410), CONS, RIGHT_V)

    # ---- 3. Billing & payment ------------------------------------------
    BILL_Y = (240, 360)
    BILL_LEFT_V, BILL_MID_V = (125, 205), (385, 440)
    for fld, pat in (("Bill Date", r"Bill Date"), ("Due Date", r"Due Date$"),
                     ("Disconnection Date", r"Disc\.?\s*Date")):
        v = grab(fld, pat, (0, 120), BILL_Y, BILL_LEFT_V, check=is_date)
        F[S3, fld] = parse_date(v) if v else None

    # billing period comes from the meter reading table (dates wrap over two lines)
    starts = [i for i in items if re.fullmatch(r"\d{2}-[A-Z]{3}[-.]", i["t"]) and i["x1"] < 300]
    ends = [i for i in items if re.fullmatch(r"\d{2}-[A-Z]{3}[-.]", i["t"]) and i["x1"] >= 300]

    def period_date(cands):
        if not cands:
            return None, None
        d = sorted(cands, key=lambda i: i["y1"])[-1]
        years = [i for i in items if re.match(r"^(\d{4})\s*\d{2}:\d{2}$", i["t"])
                 and abs(i["x1"] - d["x1"]) <= 10 and 0 < i["y1"] - d["y1"] <= 16]
        if not years:
            return None, d["c"]
        y = sorted(years, key=lambda i: i["y1"] - d["y1"])[0]
        return parse_date(d["t"][:-1] + "-" + y["t"][:4]), min(d["c"], y["c"])

    F[S3, "Billing Period Start"], c1 = period_date(starts)
    F[S3, "Billing Period End"], c2 = period_date(ends)
    if c1 is not None:
        confidence["Billing Period Start"] = c1
    if c2 is not None:
        confidence["Billing Period End"] = c2
    pm = [i for i in items if i["t"].isdigit() and 515 <= i["x1"] <= 540 and 595 <= i["y1"] <= 670]
    F[S3, "Billing Period (Months)"] = float(sorted(pm, key=lambda i: i["y1"])[0]["t"]) if pm else None

    F[S3, "Net Billed Units"] = money("Net Billed Units", r"Net Billed Unit", (0, 120), (330, 360), BILL_LEFT_V)
    F[S3, "Due Date Rebate"] = money("Due Date Rebate", r"Due Date Rebate", (200, 260), BILL_Y, BILL_MID_V)
    F[S3, "Total Payable Amount"] = money("Total Payable Amount", PAYABLE + r"\s*Amount", (200, 260), (250, 285),
                                          BILL_MID_V)
    F[S3, "Payable by Due Date"] = money("Payable by Due Date", PAYABLE + r"\s*by\s*Due", (200, 260), (330, 360),
                                         BILL_MID_V)

    # ---- 4. Electricity charges (Gross / Subsidy / Net) ----------------
    COLS = {"Gross": 252, "Subsidy": 317, "Net": 366}     # right edge of each column
    charges = {}
    for name, pred in CHARGE_ROWS:
        lab = find_label(".", (0, 20), (400, 545), pred)
        row = {"Gross": None, "Subsidy": None, "Net": None}
        if lab:
            for i in items:
                if is_money(i["t"]) and 200 <= i["x2"] <= 372 and abs(i["cy"] - lab["cy"]) <= 7:
                    col = min(COLS, key=lambda k: abs(COLS[k] - i["x2"]))
                    row[col] = to_num(i["t"])
                    confidence[f"{name} - {col}"] = min(i["c"], confidence.get(f"{name} - {col}", 1))
        charges[name] = row
        for col in ("Gross", "Subsidy", "Net"):
            F[S4, f"{name} - {col}"] = row[col]

    # ---- 5. Taxes & adjustments (right-hand table) ---------------------
    TAX_ROWS = [("CGST", "CGST"), ("SGST", "SGST"), ("Current LPSC", "Current LPSC"),
                ("Previous LPSC", "Previous LPSC"), ("Net Current Bill", "Net Current Bill"),
                ("Subsidy by Government", "Subsidy by Govt"), ("Subsidized Bill", "Subsidized Bill"),
                ("Arrear Amount", "Arrear Amount"), ("Final Payable Amount", PAYABLE + r"\s*Amount")]
    for fld, pat in TAX_ROWS:
        F[S5, fld] = money(fld, pat, (360, 400), (380, 510), (535, 600), ytol=6)

    # fields in section 3 that repeat values from the right-hand table
    F[S3, "Current Bill Amount"] = F[S5, "Net Current Bill"]
    F[S3, "Arrear Amount"] = F[S5, "Arrear Amount"]

    # amount in words (wraps over two lines)
    w1 = find_label(r"In Words", (360, 400), (505, 535))
    words = None
    if w1:
        words = re.sub(r"^.*In Words:\s*", "", w1["t"]).strip()
        nxt = [i for i in items if 360 <= i["x1"] <= 400 and 0 < i["y1"] - w1["y1"] <= 16
               and re.search(r"Rupees", i["t"], re.I)]
        if nxt:
            words += " " + nxt[0]["t"]
        confidence["Amount in Words"] = w1["c"]
    F[S3, "Amount in Words"] = words

    # QR code: present? and can it be decoded?
    qr_label = find_label(r"S[ce]an\s*&?\s*Pay")
    F[S3, "Payment QR Code"] = "Present" if qr_label else "Not found"
    F[S3, "QR Code Payload"] = decode_qr(image) if image is not None else "Not decoded"

    # ---- validation ----------------------------------------------------
    checks = []

    def add_check(name, ok, detail):
        checks.append((name, "PASS" if ok else "REVIEW", detail))

    for name, _ in CHARGE_ROWS:
        r = charges[name]
        if r["Gross"] is not None and r["Net"] is not None:
            calc = round(r["Gross"] - (r["Subsidy"] or 0), 2)
            add_check(f"{name}: Gross - Subsidy = Net", abs(calc - r["Net"]) < 0.02,
                      f"{r['Gross']} - {r['Subsidy'] or 0} = {calc}, bill shows {r['Net']}")
    nets = round(sum(r["Net"] or 0 for r in charges.values()), 2)
    ncb = F[S5, "Net Current Bill"]
    if ncb is not None:
        add_check("Sum of net charges = Net Current Bill", abs(nets - ncb) < 0.05, f"{nets} vs {ncb}")
        arr, fin = F[S5, "Arrear Amount"] or 0, F[S5, "Final Payable Amount"]
        if fin is not None:
            add_check("Net Current Bill + Arrear = Final Payable (rounded)", abs(ncb + arr - fin) <= 1.0,
                      f"{ncb} + {arr} = {round(ncb + arr, 2)}, bill shows {fin}")
    tp, pd_, fp = F[S3, "Total Payable Amount"], F[S3, "Payable by Due Date"], F[S5, "Final Payable Amount"]
    add_check("Payable amounts agree (top box, due-date box, table)", tp == pd_ == fp, f"{tp} / {pd_} / {fp}")
    bp = (F[S3, "Billing Period Start"], F[S3, "Billing Period End"])
    add_check("Billing period dates found", all(bp),
              f"{bp[0]:%d-%b-%Y} to {bp[1]:%d-%b-%Y}" if all(bp) else "missing")
    missing = [f for sec, fl in ORDER for f in fl if F.get((sec, f)) is None
               and not (sec == S4 and f.endswith("Subsidy"))]
    add_check("No required field is empty", not missing, ", ".join(missing) or "all present")

    # ---- fields to double-check ----------------------------------------
    flagged = []
    for sec, fields in ORDER:
        for fld in fields:
            conf = confidence.get(fld)
            if fld in notes or (conf is not None and conf < LOW_CONF):
                flagged.append((fld, None if conf is None else round(conf, 3),
                                notes.get(fld, "Low OCR confidence")))

    return {"fields": [(sec, fld, F.get((sec, fld))) for sec, fields in ORDER for fld in fields],
            "checks": checks, "flagged": flagged}


# ---------------------------------------------------------------- JSON for the web UI
def display_value(field, v):
    """Value as the Excel sheet shows it (dates d-Mmm-yyyy, whole numbers without .0)."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return f"{v.day}-{v:%b-%Y}"
    if field == "Account Number":
        return str(int(v))
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def to_json(result):
    return {
        "fields": [{"section": s, "field": f, "value": display_value(f, v)} for s, f, v in result["fields"]],
        "checks": [{"check": n, "result": r, "details": d} for n, r, d in result["checks"]],
        "flagged": [{"field": f, "confidence": c, "remark": m} for f, c, m in result["flagged"]],
        "summary": {
            "values_filled": sum(1 for _, _, v in result["fields"] if v is not None),
            "checks_passed": sum(1 for c in result["checks"] if c[1] == "PASS"),
            "checks_total": len(result["checks"]),
        },
    }

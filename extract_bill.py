"""
Step 2: turn output/ocr_result.csv into the structured Excel layout of samples/Sample_Output.xlsx.
The rules live in billocr/extract.py.

Usage (in the same environment you used for run_ocr.py):
    python extract_bill.py
    python extract_bill.py <ocr_result.csv> <bill_image.png> <output.xlsx>
"""
import os
import sys

from billocr.excel import write_xlsx
from billocr.extract import extract
from billocr.ocr import load_image, read_csv

HERE = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(HERE, "output", "ocr_result.csv")
IMAGE_PATH = os.path.join(HERE, "samples", "electricity_bill_sample.png")
OUT_XLSX = os.path.join(HERE, "output", "bill_output.xlsx")
if len(sys.argv) >= 4:
    CSV_PATH, IMAGE_PATH, OUT_XLSX = sys.argv[1:4]

try:
    image = load_image(IMAGE_PATH)
except ValueError:
    image = None
result = extract(read_csv(CSV_PATH), image)
write_xlsx(result, OUT_XLSX)

checks = result["checks"]
print("Saved:", OUT_XLSX)
print(f"{sum(1 for _, _, v in result['fields'] if v is not None)} values filled, "
      f"{sum(1 for c in checks if c[1] == 'PASS')}/{len(checks)} checks passed")
for c in checks:
    if c[1] != "PASS":
        print("REVIEW:", c[0], "-", c[2])

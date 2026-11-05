"""
Step 1: run PaddleOCR on a bill image and save output/ocr_result.csv (+ annotated image and raw JSON).

Usage:
    python run_ocr.py
    python run_ocr.py <bill_image.png> <output_dir>

The OCR models (~135 MB) are downloaded on the first run into PaddleX's cache: ~/.paddlex,
or the folder in the PADDLE_PDX_CACHE_HOME environment variable if it is set.
"""
import os
import sys

from billocr.ocr import load_image, run_ocr, write_csv

HERE = os.path.dirname(os.path.abspath(__file__))
IMAGE = os.path.join(HERE, "samples", "electricity_bill_sample.png")
OUT_DIR = os.path.join(HERE, "output")
if len(sys.argv) >= 3:
    IMAGE, OUT_DIR = sys.argv[1:3]
os.makedirs(OUT_DIR, exist_ok=True)

rows = run_ocr(load_image(IMAGE), save_dir=OUT_DIR)
write_csv(rows, os.path.join(OUT_DIR, "ocr_result.csv"))

print(f"Done. {len(rows)} text items found.")
print("Results saved in:", OUT_DIR)

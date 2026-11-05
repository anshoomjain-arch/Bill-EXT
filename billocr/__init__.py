"""Electricity bill OCR (PaddleOCR) + field extraction for the KESCO / UPPCL layout."""
from .extract import extract, to_json


def process_image(src):
    """Image (path, bytes or BGR array) -> JSON-ready dict with fields, checks and flagged values."""
    from .ocr import load_image, run_ocr
    img = load_image(src)
    return to_json(extract(run_ocr(img), img))

"""
PaddleOCR step: image -> list of text boxes (text, confidence, x1, y1, x2, y2).

The extraction rules in extract.py use pixel positions measured on a bill image
596 px wide, so every image is scaled to that width before OCR.
"""
import csv

import cv2
import numpy as np

REF_WIDTH = 596   # width of samples/electricity_bill_sample.png, which the rules were written for

_engine = None


DET_MODEL, REC_MODEL = "PP-OCRv6_medium_det", "PP-OCRv6_medium_rec"


def get_engine():
    """
    Create PaddleOCR once per process (model loading is the slow part).
    If BILLOCR_MODEL_DIR is set (e.g. models baked into the Lambda image), models load from
    <dir>/<model name> instead of being downloaded.
    """
    global _engine
    if _engine is None:
        import os
        from paddleocr import PaddleOCR   # imported late so callers can set PADDLE_PDX_CACHE_HOME first
        model_dir = os.environ.get("BILLOCR_MODEL_DIR")
        dirs = dict(text_detection_model_dir=os.path.join(model_dir, DET_MODEL),
                    text_recognition_model_dir=os.path.join(model_dir, REC_MODEL)) if model_dir else {}
        _engine = PaddleOCR(
            lang="en",
            text_detection_model_name=DET_MODEL,
            text_recognition_model_name=REC_MODEL,
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            enable_mkldnn=False,
            **dirs,
        )
    return _engine


def load_image(src):
    """Path, raw bytes or BGR array -> BGR array scaled to REF_WIDTH."""
    if isinstance(src, np.ndarray):
        img = src
    elif isinstance(src, (bytes, bytearray)):
        img = cv2.imdecode(np.frombuffer(src, np.uint8), cv2.IMREAD_COLOR)
    else:
        img = cv2.imread(str(src))
    if img is None:
        raise ValueError("Could not read the image (unsupported or corrupt file)")
    h, w = img.shape[:2]
    if w != REF_WIDTH:
        interp = cv2.INTER_AREA if w > REF_WIDTH else cv2.INTER_CUBIC
        img = cv2.resize(img, (REF_WIDTH, round(h * REF_WIDTH / w)), interpolation=interp)
    return img


def run_ocr(img, save_dir=None):
    """BGR array -> OCR rows sorted in rough reading order (top to bottom, left to right)."""
    rows = []
    for res in get_engine().predict(img):
        for text, score, box in zip(res["rec_texts"], res["rec_scores"], res["rec_boxes"]):
            x1, y1, x2, y2 = [int(v) for v in box]
            rows.append(dict(text=text, confidence=round(float(score), 3), x1=x1, y1=y1, x2=x2, y2=y2))
        if save_dir:
            try:
                res.save_to_img(save_dir)    # picture with boxes drawn on the bill
                res.save_to_json(save_dir)   # full raw result
            except Exception as e:
                print("Could not save image/json:", e)
    rows.sort(key=lambda r: (r["y1"] // 15, r["x1"]))
    return rows


FIELDS = ["text", "confidence", "x1", "y1", "x2", "y2"]


def write_csv(rows, path):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


def read_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return [dict(text=r["text"], confidence=float(r["confidence"]),
                     **{k: int(r[k]) for k in ("x1", "y1", "x2", "y2")})
                for r in csv.DictReader(f)]

"""
Copy the OCR model files used locally into ./models so the Docker build can bake them into the image.

Usage:  python scripts/copy_models.py [paddle cache dir]
The cache dir defaults to $PADDLE_PDX_CACHE_HOME, else ~/.paddlex (where run_ocr.py downloads them).
"""
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from billocr.ocr import DET_MODEL, REC_MODEL  # noqa: E402

default_cache = os.environ.get("PADDLE_PDX_CACHE_HOME") or Path.home() / ".paddlex"
src = Path(sys.argv[1] if len(sys.argv) > 1 else default_cache) / "official_models"
dst = Path(__file__).resolve().parents[1] / "models"
for name in (DET_MODEL, REC_MODEL):
    if not (src / name / "inference.pdiparams").exists():
        sys.exit(f"Missing {src / name} - run run_ocr.py once to download the models")
    shutil.copytree(src / name, dst / name, dirs_exist_ok=True)
    print("copied", name)

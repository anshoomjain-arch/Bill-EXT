"""
Deploy web/ to Amplify Hosting (manual zip deployment, no git connection).

    python scripts/deploy_web.py <api url> [amplify app id] [--qwen-api <pipeline 2 api url>]

Without an app id, an Amplify app "bill-ocr-demo" with branch "main" is created (or reused).
Without --qwen-api the page shows pipeline 1 only.
AWS account / profile / region come from the environment (see scripts/aws_env.py).
"""
import io
import sys
import time
import zipfile
from pathlib import Path

import requests

from aws_env import session as aws_session

APP_NAME, BRANCH = "bill-ocr-demo", "main"
WEB = Path(__file__).resolve().parents[1] / "web"

amp = aws_session().client("amplify")
args = sys.argv[1:]
qwen_url = ""
if "--qwen-api" in args:
    i = args.index("--qwen-api")
    qwen_url = args[i + 1].rstrip("/")
    del args[i:i + 2]
api_url = args[0].rstrip("/")

if len(args) > 1:
    app_id = args[1]
else:
    apps = [a for a in amp.list_apps(maxResults=100)["apps"] if a["name"] == APP_NAME]
    app_id = apps[0]["appId"] if apps else amp.create_app(name=APP_NAME, platform="WEB")["app"]["appId"]
try:
    amp.get_branch(appId=app_id, branchName=BRANCH)
except amp.exceptions.NotFoundException:
    amp.create_branch(appId=app_id, branchName=BRANCH, stage="PRODUCTION")

buf = io.BytesIO()
with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
    for f in sorted(WEB.iterdir()):
        if f.name == "config.js":
            z.writestr("config.js", f'window.BILL_API_URL = "{api_url}";\n'
                                    f'window.BILL_QWEN_API_URL = "{qwen_url}";\n')
        elif f.is_file():
            z.write(f, f.name)

dep = amp.create_deployment(appId=app_id, branchName=BRANCH)
requests.put(dep["zipUploadUrl"], data=buf.getvalue(), timeout=120).raise_for_status()
job_id = amp.start_deployment(appId=app_id, branchName=BRANCH, jobId=dep["jobId"])["jobSummary"]["jobId"]
while True:
    status = amp.get_job(appId=app_id, branchName=BRANCH, jobId=job_id)["job"]["summary"]["status"]
    if status not in ("PENDING", "PROVISIONING", "RUNNING"):
        break
    time.sleep(5)
print(f"Amplify app {app_id}, job {job_id}: {status}")
print(f"URL: https://{BRANCH}.{amp.get_app(appId=app_id)['app']['defaultDomain']}")
sys.exit(0 if status == "SUCCEED" else 1)

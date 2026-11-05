"""
AWS session for the helper scripts, from environment variables (nothing account-specific in code):

    AWS_PROFILE        named profile from ~/.aws/credentials (optional; default credential chain otherwise)
    AWS_REGION         default ap-southeast-2
    BILL_AWS_ACCOUNT   required: the 12-digit account you intend to use. Scripts stop if the
                       credentials belong to any other account (protects shared accounts).
"""
import os
import sys

import boto3

REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "ap-southeast-2"


def session():
    expected = os.environ.get("BILL_AWS_ACCOUNT")
    if not expected:
        sys.exit("Set BILL_AWS_ACCOUNT to the AWS account id you intend to use (see README.md).")
    s = boto3.Session(profile_name=os.environ.get("AWS_PROFILE") or None, region_name=REGION)
    actual = s.client("sts").get_caller_identity()["Account"]
    if actual != expected:
        sys.exit(f"Credentials are for account {actual}, but BILL_AWS_ACCOUNT={expected} - aborting.")
    return s

# deploy/

The step-by-step deployment guide is in the main [README.md](../README.md#4-deploy-it-to-aws).
This folder holds the IAM permissions the **deploying** IAM user needs.

| File | Grants |
|---|---|
| [iam-deployer-policy.json](iam-deployer-policy.json) | Pipeline 1 + shared pieces: CloudFormation (`bill-ocr-demo`, SAM's `aws-sam-cli-managed-default`), S3 buckets, ECR repos `billocrdemo*`, Lambda functions and IAM roles `bill-ocr-demo-*`, API Gateway, CloudWatch log groups |
| [iam-deployer-policy-qwen.json](iam-deployer-policy-qwen.json) | Pipeline 2: stack `bill-ocr-demo-qwen`, its bucket, Bedrock model listing, invoking **Qwen models only** (for `scripts/eval_qwen.py`) |

Both are scoped to resources named `bill-ocr-demo*` / `billocrdemo*`, so they can be used in a
shared account without touching anything else. Replace `ACCOUNT_ID` before attaching:

```bash
sed 's/ACCOUNT_ID/123456789012/g' deploy/iam-deployer-policy.json
```

Not included: Amplify Hosting (`amplify:*` on the app) and `sts:GetCallerIdentity` (always allowed).

## Reference deployment (owner's account)

The working deployment this repo was built against. **Do not deploy into this account unless you
were asked to**: stack names are fixed, so a second deploy there updates the live system.

| | |
|---|---|
| Account / region | `982099554167` / `us-east-1` |
| Web UI | https://main.dv282fu0ptyb1.amplifyapp.com (Amplify app `dv282fu0ptyb1`, manual zip deploys) |
| Pipeline 1 | stack `bill-ocr-demo`, API https://8n64elzbl4.execute-api.us-east-1.amazonaws.com, ECR `billocrdemo-ocr` |
| Pipeline 2 | stack `bill-ocr-demo-qwen`, API https://g5hdwh479l.execute-api.us-east-1.amazonaws.com, ECR `billocrdemo-qwen` |
| Settings | `OcrMemoryMB=3008` (account limit), `AllowedOrigin=https://main.dv282fu0ptyb1.amplifyapp.com` |

#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
PROJECT=sky-task-c4048; REGION=us-central1
gcloud run deploy skytask-api --source . --project "$PROJECT" --region "$REGION" \
  --service-account "skytask-api@$PROJECT.iam.gserviceaccount.com" \
  --allow-unauthenticated --max-instances 1 --min-instances 1 --memory 512Mi --cpu 1 --timeout 60 --quiet

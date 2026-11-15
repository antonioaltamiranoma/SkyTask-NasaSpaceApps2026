#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
PROJECT=sky-task-c4048; REGION=us-central1
python3 deploy/make_snapshot.py "$@"
gcloud run deploy skytask-api --source . --project "$PROJECT" --region "$REGION" \
  --service-account "skytask-api@$PROJECT.iam.gserviceaccount.com" \
  --allow-unauthenticated --max-instances 1 --min-instances 0 --memory 512Mi --cpu 1 --timeout 60 --quiet

# Cloud setup (card #3)

Project `sky-task-c4048`, region `us-central1` for everything. No key files exist anywhere: Cloud Run uses the
`skytask-api` service account directly, and the deploy commands use your own `gcloud` / `firebase` login.

## What exists (done in the console)
| Piece | Name / setting |
|---|---|
| Firebase / GCP project | `sky-task-c4048`, Blaze plan, $5 budget alert |
| Firestore | `(default)`, `us-central1`, production mode (browsers can't read it) |
| Cloud Storage | `sky-task-c4048-masters`, `us-central1`, public access prevention on, uniform access |
| Service account | `skytask-api@sky-task-c4048.iam.gserviceaccount.com`: Cloud Datastore User; Storage Object Viewer on the bucket |
| APIs | Cloud Run Admin, Cloud Build, Artifact Registry, Secret Manager |
| Hosting | https://sky-task-c4048.web.app |

## Tools on the deploying PC (once)
```
gcloud auth login
gcloud config set project sky-task-c4048
gcloud config set run/region us-central1
gcloud auth application-default login
npm install -g firebase-tools
firebase login
```

## How data flows
1. The **generator** (`generator/`, on the home PC) keeps adding tasks to its local SQLite database.
2. The **sync** (`deploy\sync_tasks.bat`, next to it) pushes active tasks every 10 minutes: strip →
   `gs://sky-task-c4048-masters/masters/<task_id>.png`, record → Firestore `tasks`; tasks the generator dropped are
   marked `retired`. It stops syncing `sample_data` once the generator holds 50+50+12+12 active controls.
3. The **API** (Cloud Run) re-reads active tasks from Firestore every minute and serves request-time variants.
4. Every graded answer is written to Firestore `answers` (queued and retried; session id hashed).
   `GET /v1/stats` gives the public totals; volunteer accuracy on holdout controls appears after 50 check answers.

## Deploy
Run from the repo root (cloned inside `Nasa2026`, next to `generator/`):

1. **API → Cloud Run:** `deploy\deploy_api.bat`. `gcloud run deploy skytask-api --source .` builds the image with
   Cloud Build (no task data inside: tasks come from Firestore). The first run creates an Artifact Registry repository.
2. **Website → Firebase Hosting:** `python deploy\hosting_rest.py` (no Firebase CLI needed) or `deploy\deploy_site.bat`.
   Serves `explorer/` + `widget/`; the pages call the Cloud Run API directly (CORS open to any site).

Check: `https://sky-task-c4048.web.app/classify.html`, `/`, `/game.html`, `/inspector.html` (no DEV table in production).

## Limits today
- Sessions, challenges and per-session reliability live in the instance (`--max-instances 1 --min-instances 1`), so a
  redeploy starts everyone's session fresh. Answers and tasks are safe in Firestore and the bucket.
- One idle instance costs a few dollars a month; after judging, set `--min-instances 0` or delete the service.
- `gcloud run deploy --source` keeps a copy of the uploaded source in the private `run-sources-*` bucket and the image
  in Artifact Registry; delete old ones after the event.
- All `/v1` responses are `Cache-Control: no-store`. `SKYTASK_DEV` is refused on Cloud Run, so `/v1/dev/*` never
  exists in production. Firestore stays in production mode: browsers can't read it.

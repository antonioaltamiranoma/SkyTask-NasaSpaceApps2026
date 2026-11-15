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

## Deploy
Run from the repo root (cloned inside `Nasa2026`, next to `generator/`):

1. **API → Cloud Run:** `deploy\deploy_api.bat` (optionally pass the generator data folder).
   It copies the generator database and the active tasks' master strips into `deploy/_snapshot/`
   (git-ignored), then `gcloud run deploy skytask-api --source .` builds the image with Cloud Build.
   The first run asks to create an Artifact Registry repository: answer yes.
2. **Website → Firebase Hosting:** `deploy\deploy_site.bat`. Serves `explorer/` + `widget/`;
   `/v1/**` is forwarded to the Cloud Run service (`firebase.json` rewrite), so the pages and the API share one origin.

Check: `https://sky-task-c4048.web.app/` (CAPTCHA box), `/game.html`, `/inspector.html` (no DEV table in production).

## Phase A limits (what to say in the pitch)
- The task snapshot is baked into the **private** container image. It contains answer keys, so it must never go in git
  or a public bucket. The API only ever serves request-time variants.
- `--max-instances 1` and answers in a SQLite file inside the instance: answers and reliability are **lost when the
  instance restarts**. Fine for the demo; phase B moves sessions/answers to Firestore and tasks to the bucket via the
  uploader (#4).
- `SKYTASK_DEV` is refused on Cloud Run, so `/v1/dev/*` never exists in production.

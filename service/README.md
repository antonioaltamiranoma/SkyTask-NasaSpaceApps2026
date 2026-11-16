# service

Sky Task API (FastAPI). Contract: [`docs/api-contract.yaml`](../docs/api-contract.yaml). Runs **locally first**
against the generator's task database, so we can see exactly what a website receives before anything goes to the
cloud (Cloud Run + Firestore + private Cloud Storage come with cards #3/#4).

## Run it locally (Windows)

1. Clone the repo **inside your `Nasa2026` folder**, next to `generator`:
   `Nasa2026\generator\` and `Nasa2026\SkyTask-NasaSpaceApps2026\`.
2. Double-click `service\start_local.bat`. The first run creates `service\.venv`, installs the requirements and
   copies `settings.local.example.toml` to `settings.local.toml` (yours to edit; gitignored).
3. Open <http://localhost:8080/docs> (interactive API) or <http://localhost:8080/> (the website in `explorer/`,
   built by card #7; the widget script is served from `widget/` at `/widget/sky-task.js`).

Linux/macOS/WSL: `service/start_local.sh`.

**Which tasks it serves:** `generator_data_dir` in `settings.local.toml`. The default is
`..\..\generator\sample_data` — a copy of the generator's test database (26 controls in 16 fields, no science yet).
When your own generator has made tasks, change it to `"../../generator/data"`. The service re-reads the active
tasks every 30 s, so tasks the generator adds or retires show up without a restart. It **only reads** the
generator's database (SQLite `query_only`); its own sessions and answers live in `service\local_service.sqlite3`
(delete that file to start fresh).

## Endpoints (all under `/v1`)

| Method | Path | Notes |
| --- | --- | --- |
| POST | `/sessions` | `{site_key}` → `{session_id}`. Local site key: `local-demo`. |
| POST | `/challenge` | `{session_id, count 2–10, result none/score/token}` → public tasks only. `token` → 501 until card #9. 503 `not_enough_tasks` when this participant has seen every field (start a new session). |
| GET | `/challenge/{id}/image/{task_ref}` | PNG strip, frames side by side. Rendered once per challenge and cached until it expires (same image on reload: it has to match the ring already sent); a new challenge gets a new random crop/rotation/flip. |
| POST | `/challenge/{id}/submit` | All answers at once, once. `none` → `{recorded}`, `score` → `{score: 0/25/50/75/100}`. |
| GET | `/dev/challenge/{id}`, `/dev/pool` | **Only with `SKYTASK_DEV=1`** (start_local sets it). What was served and why: kinds, fields, answers, mix, variant. Refuses to start on Cloud Run. |

## How serving and scoring work (CLAUDE.md rules)

- `app/serving.py`: up to 50% unsettled science (most uncertain first: fewest weighted votes, then score nearest
  0.5); settled science only as re-checks (each free slot has a ~5% chance); the rest controls, Moving/Not moving by coin flip; one task per field
  per participant; order shuffled. Settings in `[serving]`.
- Scoring: controls by correctness; settled science by agreement with consensus; unsettled science by random draw;
  rounded to 25% steps. Only non-holdout controls update the participant's reliability (prior α=3, β=1).
  The consensus table is filled by the batch job of card #6 (until then every science task is unsettled).
- `task_ref` is random per challenge; the browser never sees task ids, field ids, kinds, answers, coordinates or
  absolute times. Variants come from `generator/skytask_gen/variants.py` (shared with the generator).

## Code

```
app/config.py     settings (settings.local.toml, env SKYTASK_SETTINGS, SKYTASK_DEV)
app/taskstore.py  TaskStore interface + LocalTaskStore (generator SQLite + masters, read-only); Firestore/GCS: #4
app/state.py      service SQLite: sessions, challenges, answers, reliability, consensus
app/serving.py    pick_tasks + grade (pure, unit-tested)
app/main.py       FastAPI app factory (uvicorn --factory app.main:create_app)
```

## Tests

```
cd service
python -m pytest
```
Synthetic fixtures only (fake fields and master strips); no real answer keys in the repo.

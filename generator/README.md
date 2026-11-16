# skytask_gen — continuous SPHEREx micro-task generator

NASA Space Apps 2026, "Planet X and SPHEREx". A program you start on your own computer and leave running. It keeps
growing a local database of blink tasks ("is the object in the ring moving?") from NASA SPHEREx images:

* **Science tasks (main goal):** random sky fields near the ecliptic → blind detection of moving objects → anything
  that moves and that JPL does **not** know becomes a science candidate.
* **Controls (answer keys):** known asteroids become *Moving* controls and flux-matched static stars become
  *Not moving* controls, until the pools reach their targets (50 + 50, holdout 12 + 12 by default).

> **Pre-event disclosure.** This generator reuses code and verified science rules from `pipeline-prep/`, the
> pre-event data exploration of Nov 11–13, 2026 (kept outside this repository; disclosed in the submission). Every adapted function says
> where it came from. The generator itself (survey mode, pools, database, master strips, request-time variants,
> sync queue) was written during the event.

> **Getting tasks to the cloud.** The generator only writes its local database. `deploy/sync_tasks.py` (repo root)
> pushes active tasks to Firestore + the private bucket and retires dropped ones; run `deploy\sync_tasks.bat` next
> to the generator. (`store_backend = "firebase"` below is an unused stub.)
>
> **Known caveats.** Frame-count matching falls back to all covering frames on an empty database; Not-moving
> controls from mover-less fields were never triggered in testing.

## Start it

**Windows:** double-click `start.bat` (or in a terminal: `start.bat`, `start.bat status`, `start.bat export`).
**Linux / macOS:** `./start.sh` (or `./start.sh status`, `./start.sh export`).

The first start creates `.venv/` and installs `requirements.txt` (a few minutes). Delete `.venv\.installed` to force a
reinstall after changing requirements. Needs Python 3.10+ and network access to `irsa.ipac.caltech.edu` and
`ssd-api.jpl.nasa.gov` (plus `pypi.org` for the first install).

* **Stop:** Ctrl+C. The field in progress goes back to the queue. (Windows then asks "Terminate batch job (Y/N)?" —
  either answer is fine. Ctrl+C can take up to a minute while a JPL request is in flight.)
* **Resume:** start it again. Everything lives in `data/skytask.sqlite3`; cached cutouts and JPL answers make the
  interrupted field cheap to redo.

Commands (from this folder, inside the venv: `python -m skytask_gen <command> [--settings FILE]`):

| Command | What it does |
| --- | --- |
| `run [--max-fields N] [--minutes M]` | the generator loop (limits override settings) |
| `status` | fields processed, controls by kind/holdout, science tasks, candidates, queues, disk use |
| `export [--out DIR] [--seed N]` | `public/` (`tasks_public.jsonl` + `strips/`, one rendered variant per active task) and, in a separate folder, `server/` (`tasks_server.jsonl`, `review_keys.jsonl`). Same contract as pipeline-prep `05_export.py`: 50/50 controls per split, shuffled |
| `preview [--n 12] [--variants 3]` | review sheet **with answers**: master strips (red ring, green real track) + request-time variants. Never serve it. |
| `retire TASK_ID ...` | retires tasks; a reserve control of the same kind and split is promoted |
| `import-legacy` | imports the 20 controls of `pipeline-prep/tasks_v8` (also done once automatically) |

Tests: `python -m unittest discover -s tests` (checks that after every random crop + rotation + flip the ring lands
on the object, in 16-bit and 8-bit masters). `tests/fixture_field.py` processes one survey-mode field at a fixed
position/time (e.g. asteroid 420 on 2025-12-17: `--ra 169.47 --dec -3.28 --mjd 61026.97 61027.55`) so the blind
detection → JPL labelling path can be checked; `--unlabeled` (throw-away data dir!) forces the science path.

## How it works

```
loop:
  next queued field, or create one:
    every Nth field while the Moving pool is below target  -> MOST top-up field (known bright asteroid, V <= 16.7)
    otherwise                                              -> survey field (random position, IRSA SIA)
  field: cutouts (one exposure per visit, best detector) -> reproject to a common 6.15"/px grid
         -> blind detection (sep, 5 sigma) -> static vs transient -> linking (>= 3 frames) + 2-frame pairs
         -> labels: MOST prediction for the target (targeted fields); JPL per frame ONLY if something else moved
         -> tasks: Moving / Not-moving controls (if pools need them), science candidates
         -> master strips + database rows + sync-queue entries; delete the field's cutouts
```

* **Survey fields.** A random position, uniform on the sky within |ecliptic β| ≤ 20° and |galactic b| ≥ 25°.
  IRSA SIA (`astroquery Irsa.query_sia`, collections `spherex_dr1` + `spherex_qr3`; QR2 is not in SIA) lists every
  SPHEREx image covering it (~2 s). Exposures < 30 min apart form a visit; we pick 3–4 visits ≥ 90 min apart within
  14 h (randomly among the longest such sequences), one exposure per visit (detector preference D2, D1, D3, D6, D5,
  D4; the first one covering ≥ 90 % of the field). Field = 12′ centred on the random position.
  If SIA fails 3 times in a row the generator uses **MOST-anchored fields** for 30 min (a random numbered asteroid's
  track gives the field; blind detection on the whole field; the target is labelled from MOST, anything else via JPL).
* **Top-up fields** (fallback for controls): IRSA MOST for a random numbered asteroid in 1–3000, sequences selected
  as in pipeline-prep `02a_sequences.py` (V ≤ 16.7, every step ≥ 4 px, total ≤ 50 px, |b| ≥ 25°), at most 2 fields
  per asteroid, each asteroid used once. The target needs no JPL call (MOST gives its position).
* **Science rules** (unchanged from pipeline-prep, `[science]`): 5σ, static if another frame has a source within 1 px
  that is not > 3× fainter, transients must be covered by another frame, links 0.05–0.5 °/day, ≥ 3 frames, rms ≤ 1.2 px,
  known if ≥ 2 points within 2 px of a per-frame JPL/MOST prediction. JPL observer = SPHEREx's geocentric state from
  each FITS header; "M" prefix for negative declinations; error responses are never cached; JPL's explicit
  "no matching records" answer is a valid empty result and is cached.
* **Classification.**
  * known ≥ 3-frame tracklet → *Moving* control if its split's pool is below target, else **reserve** (inactive, up
    to `reserve_max_per_kind`; promoted when a control is retired), else discarded;
  * unknown ≥ 3-frame tracklet → science, confidence **high**;
  * unknown 2-frame pair → science, confidence **low** (flagged `low_confidence_flag`);
  * a tracklet with only one point near a known object → discarded as ambiguous (conservative);
  * *Not moving* controls: per new Moving control one static star seen in every shown frame, flux 0.5–2× the
    asteroid, ≥ 6 px from every mover, pair and known-object prediction, shown with **exactly the same frames** as
    that Moving control (so frame count and time offsets carry no answer signal). Survey fields without movers may also supply one
    when the Not-moving pool lags the Moving pool (flux reference = a stored Moving control on the same detector).
  * Frame counts: Moving controls drop leading frames until the first step clears the ring (pipeline-prep rule).
    Science tasks use the same start rule and, like star-only Not-moving controls, draw their frame count from the
    stored Moving controls' distribution (`frame_count_matched` in meta says whether that succeeded).
    Fake placement tracks come from this field's movers, else from stored real control tracks (random direction).
  * Re-processing a field (after Ctrl+C or a crash) first clears that field's earlier tasks: unpublished ones are
    deleted, already-uploaded ones are retired (a `delete` is queued). No duplicates.
* **Holdout by asteroid.** Every asteroid (and every star-only field) is a group; its first control decides the
  group's split, which is then fixed. A new group goes to whichever split is *less full relative to its target*
  (proportional filling: with 50/12 targets about 1 in 5 asteroids goes to holdout). Not-moving controls inherit the
  group of the field's Moving control. Legacy tasks keep their v8 holdout (asteroid 340).
* **Master strips** (`data/masters/<task_id>.png`): every frame of the task as a tile of window + 2 × margin
  (64 + 2 × 16 = 96 px), north up, background-subtracted, 16-bit linear. The window is placed exactly as in
  pipeline-prep (ring and track — or a plausible fake track for stars/candidates — ≥ 8 px inside), then the margin
  is added. Ring, real track and placement points are stored in master coordinates (server-only).
* **Request-time variants** (`skytask_gen/variants.py`, numpy + Pillow only, importable by the cloud service):
  `make_variant(master, meta, rng) -> (png_bytes, highlight)` picks a random 64×64 window that keeps all placement
  points ≥ 8 px inside (same rule for every kind), applies one random rot90/flip to every frame, stretches each frame
  on its own and returns the transformed ring. `store.variant_meta(task)` builds `meta` from a task row.

## Settings (`settings.toml`)

Paths are relative to the settings file. On Windows write paths with forward slashes or in single quotes
(`'C:\data\sky'`): backslashes inside double quotes are TOML escapes. Missing keys fall back to defaults in
`skytask_gen/config.py`. Use another file with `--settings FILE` or the `SKYTASK_SETTINGS` environment variable.

| Section.key | Default | Meaning |
| --- | --- | --- |
| general.data_dir | `data` | database, masters, exports, logs |
| general.cache_dir | `cache` | FITS cutouts (`fits/`) and JPL answers (`jpl/`) |
| general.log_level | INFO | DEBUG / INFO / WARNING; log also in `data/logs/generator.log` |
| general.store_backend | local | `local`; `firebase` is a stub (see below) |
| general.seed | 0 | 0 = new random sky every run; other = reproducible sequence |
| run.max_runtime_minutes / max_fields_per_run | 0 / 0 | 0 = unlimited (stop with Ctrl+C) |
| run.pause_between_fields_s | 2 | politeness pause |
| run.max_field_attempts | 3 | failed fields (network) are retried after 30 min / at the next start, up to this many times |
| sky.min_abs_galactic_b / max_abs_ecliptic_beta | 25 / 20 | sky selection for survey fields (b also applies to top-up) |
| sky.field_size_arcmin | 12 | must exceed window + 2 × margin (9.8′) |
| survey.enabled | true | random-field survey |
| survey.collections | dr1, qr3 | IRSA SIA collections |
| survey.min_visits / max_visits | 3 / 4 | frames per field |
| survey.min_visit_gap_min / max_span_hours / visit_split_min | 90 / 14 / 30 | sequence rules |
| survey.detector_preference | D2 D1 D3 D6 D5 D4 | best detectors for asteroids first |
| survey.max_cutout_tries_per_visit / min_field_coverage | 2 / 0.9 | cutouts tried per visit, coverage needed |
| survey.positions_per_attempt | 8 | random positions tried before giving the loop back |
| survey.sia_failures_before_fallback / fallback_asteroid_max_number | 3 / 200000 | MOST-anchored fallback |
| controls.moving_target / not_moving_target | 50 / 50 | ACTIVE training controls wanted |
| controls.holdout_moving_target / holdout_not_moving_target | 12 / 12 | ACTIVE holdout controls wanted |
| controls.reserve_max_per_kind | 20 | inactive spares; 0 = discard surplus |
| controls.negatives_per_moving_control | 1 | Not-moving stars per new Moving control |
| controls.flux_ratio_min / max | 0.5 / 2.0 | star flux / asteroid flux |
| controls.negatives_from_moverless_fields | true | survey fields may supply Not-moving controls |
| topup.enabled / every_n_fields | true / 3 | while the Moving pool is short, every Nth field is a MOST field |
| topup.max_vmag, asteroid_number_min/max, max_fields_per_asteroid, min_step_px, max_total_px | 16.7, 1, 3000, 2, 4, 50 | top-up selection |
| science.* | pipeline-prep values | detection/linking/labelling thresholds (above) |
| science.max_science_per_field | 3 | cap |
| science.keep_pairs | true | 2-frame pairs become low-confidence science |
| science.pair_require_outlier / pair_max_flux_ratio | true / 4 | **new** pair screens: both detections carry SPHEREx's OUTLIER flag and have similar flux (without them one field gave 34 noise pairs) |
| science.jpl_vmag_limit | 21 | JPL query depth |
| tasks.window_px / ring_radius_px / master_margin_px / edge_px / min_frames | 64 / 4 / 16 / 8 / 3 | strip geometry |
| rate_limits.jpl_max_parallel | 1 | capped at 2 (JPL answers HTTP 429 above that) |
| rate_limits.jpl_pause_s / jpl_timeout_s / jpl_max_retries / jpl_backoff_s | 5 / 150 / 4 / 60 | backoff doubles after every failure/429 |
| rate_limits.jpl_expected_s | 75 | with a time budget, no JPL call starts that would overrun it |
| rate_limits.irsa_pause_s / irsa_timeout_s | 1 / 120 | IRSA politeness |
| cache.cleanup_after_field / max_cache_mb | true / 2000 | delete a field's cutouts when it is finished; oldest cache files are trimmed above the cap |
| seed_data.import_legacy / legacy_dir | true / `../pipeline-prep/tasks_v8` | one-time import of the 20 v8 controls |

## Files it writes

```
data/skytask.sqlite3       fields, frames, detections, candidates, tasks, holdout_groups, asteroids, sync_queue, kv
data/masters/<id>.png      master strips (16-bit; legacy ones are the 8-bit v8 strips)
data/exports/<time>/       output of `export`
data/review/               output of `preview` (contains answers)
data/logs/generator.log    rotating log
cache/fits, cache/jpl      cutouts (deleted per field) and JPL answers (small, kept)
```

### Reading `status`
* **FIELDS** — per source (`survey`, `most`) and status; `outcome` says what each field produced (`no_tasks` is
  normal: most random 12′ fields contain no object bright enough); survey positions queried; JPL network calls.
* **CONTROLS** — active / target, reserve, retired, per kind and split; "legacy" = imported v8 controls.
* **SCIENCE TASKS** — by confidence (high = ≥ 3-frame tracklet, low = 2-frame pair) and status.
* **CANDIDATES** — every tracklet/pair and its fate (`control_pos`, `science`, `known_pool_full`,
  `known_step_too_small`, `ambiguous_near_known`, `pair_known`, `science_cap`, `*_no_render` …).
* **QUEUES** — fields waiting; sync-queue entries for the future uploader.
* **DISK** — data and cache size, kB per new task.

## Public vs server-only (never break this)
Public (browser): `task_id` (random), `frame_count`, `frame_size`, `frame_offsets_h` (rounded to 0.5 h), and per request
the variant PNG + highlight. Server-only: kind, expected answer, holdout, group, confidence, master strip and master
coordinates, field id, RA/Dec, absolute times, image ids, designation, flux. The service must still serve **at most
one task per field per participant** (tasks of one field share stars).

## Export layout
```
<export>/public/tasks_public.jsonl   task_id, frame_count, frame_size, frame_offsets_h, strip_file, highlight
<export>/public/strips/<random>.png
<export>/server/tasks_server.jsonl   answer keys and everything server-side  (never upload to a public place)
<export>/server/review_keys.jsonl    track positions (review only, never served)
```

## Uploader contract (for the cloud work)
* Drain `sync_queue` rows with `state='pending'` and mark each `done` or `failed` (+ `error`).
* **Idempotent by `task_id`:** uploading the same task twice overwrites, never duplicates.
* **Deletes tolerate missing objects** (a task may never have reached the cloud). The generator only queues a
  `delete` for tasks whose `upload` is `done`; a pending upload of a retired task becomes `superseded`.
* **Shuffle the upload order** within a batch, so upload time/order can't reveal kind or field.
* Public records contain only `store.public_record(task)`; masters and answer keys go to server-only storage.
  Clients get strips **only through the service** (which calls `variants.make_variant`), never by listing storage.
* One generator per data folder (enforced by `data/run.lock`); the uploader may read the SQLite db concurrently
  (WAL mode).

## Future Firebase uploader
`store.py` defines the `Store` interface (`save_task`, `set_status`, `master_bytes`), `LocalStore` (used now) and a
`FirebaseStore` stub whose docstring gives the mapping: master → private Cloud Storage `masters/<id>.png`;
`public_record` → Firestore `tasks/<id>`; answer key + `variant_meta` → server-only `answer_keys/<id>`; field info →
`fields/<id>`. The generator stays local; a separate uploader drains `sync_queue` (`upload` for each task that
became active, `delete` for retired ones; mark rows `done`/`failed`). Credentials come from an environment
variable, never from settings.toml.

## Throughput and limits (measured in the Linux test VM, Nov 14, 2026)
* Survey field: 9–77 s (mean 37 s), mostly cutout downloads (~5 MB each, mostly the PSF cube). Top-up field: 27–139 s
  (mean 56 s). A JPL call takes 30–90 s; it only happens when something other than the target moved.
* 11 random survey fields: 0 tracklets, 0 screened pairs → no science yet. Expected: a single SPHEREx exposure
  reaches V ≈ 17, and a random 12′ field near the ecliptic rarely holds such an asteroid. Science yield will be low
  (order of one candidate per many hours); the method, not the candidate count, is the pitch.
* Top-up: 5 of 8 MOST fields gave a Moving + Not-moving pair. With the default interleaving (every 3rd field) that is
  roughly 15 control pairs per hour while the pools are short (~30/hour with `every_n_fields = 1`), so the default
  pools (62 + 62, 20 already imported) fill in about 3 hours.
* ~55 kB per task (master PNG); cutouts are deleted after every field (cache stays at a few tens of MB).

## Known issues / risks
* **Windows is untested** (developed in a Linux VM). `sep` 1.4.1, `reproject`, `astropy` publish Windows wheels for
  Python 3.10–3.13, so `pip install` should not need a compiler. If `sep` fails to install, install
  `sep==1.2.1` (older wheels) or use Python 3.11/3.12; as a last resort run the generator in WSL with `start.sh`.
* SQLite in a cloud-synced folder (OneDrive, Dropbox) or a network share can fail ("disk I/O error" happened in the
  VM's shared folder): point `data_dir` to a plain local folder in that case.
* IRSA SIA sometimes answers 502 for a while (seen on Nov 14): retries, then the MOST-anchored fallback.
* A field killed hard (power loss, kill -9) is re-queued without losing an attempt, at most 3 times; after that it
  is marked failed ("interrupted too often").
* Frame-count matching for science / star-only controls needs stored Moving controls; before the first one
  exists they show all covering frames.
* Science pairs are still a weak signal (no cosmic-ray bit check, no check for the object's absence at intermediate
  frames); they are labelled low confidence.

# CLAUDE.md — Sky Task (NASA Space Apps 2026)

Read this before working on any card. It is the standing briefing for every Claude session in this repo.

## The project in five lines
Sky Task turns NASA SPHEREx infrared images into short "blink" micro-tasks: a few frames of the same patch of sky, a circle on one dot, and one question — did the circled dot move? A Python generator builds the tasks and their answer keys; a FastAPI service on Cloud Run serves them to an embeddable `<sky-task>` widget and a public sky explorer; many quick answers are combined into a consensus weighted by each person's accuracy on control tasks. Uses: pure citizen science, game mechanics, and (future) CAPTCHA-style verification.

## Settled rules — never change these on your own (stop and say so in the PR)
- **Answer options:** `moving` or `not_moving`. No "Not sure".
- **Task kinds:** `control_pos` (known asteroid → moving), `control_neg` (star of similar brightness → not_moving), `science` (unexplained candidate, no answer key).
- **The browser never learns which tasks are controls, or how many.** Public task data contains only: task id, frame count, frame size, ring position, frame offsets in hours. Never coordinates, absolute times, field ids, kinds or answers.
- **Variants are made at request time** from one stored master strip (`generator/skytask_gen/variants.py`: random crop + the same rot90/flip on every frame, ring transformed). Never serve a master directly.
- **Serving mix:** every challenge has ≥1 control and at most 50% unsettled science tasks.
- **Scoring by mode:** `token` (CAPTCHA) passes on controls only, all must be right, consensus never used. `score` (games) is a percentage over all tasks in 25% steps: controls by correctness, settled science by agreement with consensus, unsettled science by random draw. `none` just records. Only non-holdout controls change reliability.
- **Consensus:** reliability r = α/(α+β) with prior α=3, β=1; vote weight w = max(0, 2r−1); confirmed at score ≥ 0.8 with ≥ 2 weighted votes, rejected at ≤ 0.2; recomputed in batches.
- **Serving science:** prioritise uncertain tasks (few votes, score near 0.5); stop serving settled tasks except ~5% re-checks.
- **Control pool:** 50 Moving + 50 Not moving active, plus 12 + 12 holdout (configurable). Holdout controls never affect anyone's weight; they only measure accuracy.
- **One task per field per participant** (tasks of one field share stars).
- **Disclosure:** the widget always shows "Helps analyze NASA SPHEREx data". No NASA logos.

## Source of truth
- API: `docs/api-contract.yaml`. Data model and architecture: the "SPHEREx Micro-Task — MVP Architecture" doc. If a card seems to need a contract change, stop and say so in the PR.

## How we work
- One card (GitHub issue) per branch: `card-<n>-short-name`.
- Open a pull request that links the issue (`Closes #n`) and fills in the template, especially **How to verify**.
- Run the reviewer agent (`.claude/agents/reviewer.md`) on your PR before asking for QA.
- Antonio approves every PR (QA). Nothing merges without him.
- Stay inside the card's scope. Write tests for its acceptance criteria.

## Safety
- **Never commit credentials**: service-account keys, Firebase config with secrets, `.env` files. Use Secret Manager / environment variables.
- Never serve `tasks_server.jsonl`, answer keys or review files publicly.
- Be polite to IRSA and JPL (rate limits; cache responses; never cache error responses).

## Commands
- Generator: see `generator/README.md` (`start.bat` / `start.sh`, `status`, `export`).
- Service: see `service/README.md` (filled in by its first card).
- Tests: `python -m pytest` in each Python folder.

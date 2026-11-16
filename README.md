# Sky Task

**NASA Space Apps Challenge 2026 · Challenge: Planet X and SPHEREx · Team Sky Task (Quito, Ecuador)**

Somos un equipo de Quito que trabaja en el desafío Planet X and SPHEREx. Queremos acercar los datos infrarrojos de la misión SPHEREx de la NASA a cualquier persona, convirtiendo unos segundos de atención en una contribución real a la ciencia.

*Every task solved is one more pair of eyes on the infrared sky.*

## Try it

- **Classify SPHEREx images (citizen science):** https://sky-task-c4048.web.app/classify.html, with **live results**: answers, volunteers, and how often the crowd is right on hidden check tasks (known asteroids and stars), updated every 10 minutes
- The widget inside a sign-up form (CAPTCHA-style box): https://sky-task-c4048.web.app/
- The widget inside a game: https://sky-task-c4048.web.app/game.html
- What the browser receives (public data only): https://sky-task-c4048.web.app/inspector.html
- API: https://skytask-api-659703561463.us-central1.run.app/v1 (contract: [`docs/api-contract.yaml`](docs/api-contract.yaml); live count: `/v1/stats`)

## How it works

1. **Generator** (`generator/`, runs on a home PC): finds SPHEREx images through NASA/IPAC IRSA, aligns them, detects every
   source and links dots that move in a straight line across 3+ frames. JPL's Small-Body Identification API labels known
   asteroids. Known asteroids become *Moving* controls, flux-matched stars become *Not moving* controls (capped at
   50 + 50, plus 12 + 12 holdout), and unexplained movers become science tasks.
2. **Sync** (`deploy/sync_tasks.py`): pushes new tasks to Firestore and a private Cloud Storage bucket every 10 minutes.
3. **API** (`service/`, FastAPI on Google Cloud Run): serves challenges that mix controls with at most 50% unsettled
   science, renders a fresh image variant per request, never tells the browser which task is a control, and stores
   every answer in Firestore.
4. **Widget** (`widget/sky-task.js`): one script tag and one `<sky-task>` element, for any site or game.

Measured on real SPHEREx data before the event: 14 of 14 known asteroids recovered blind in their fields, 0 false tracks.

## What's in this repository

| Folder | What it is |
| --- | --- |
| `generator/` | Python program that turns NASA SPHEREx images into blink tasks with automatic answer keys (runs on a home computer, builds a local task database) |
| `service/` | API (FastAPI on Google Cloud Run): serves challenges, records answers, computes consensus |
| `widget/` | `<sky-task>` embeddable web component |
| `explorer/` | Public sky explorer and demo pages |
| `docs/` | API contract and project documentation |

## Data and credits

- NASA SPHEREx data via the NASA/IPAC Infrared Science Archive (IRSA), including its Moving Object Search Tool and image services.
- NASA/JPL Solar System Dynamics Small-Body Identification API.
- Open-source libraries: astropy, astroquery, pyvo, reproject, sep, numpy, scipy, Pillow, FastAPI (full list in each folder's requirements).

## Disclosures

- **Pre-event data exploration:** before the event (Nov 11–13, 2026) we explored SPHEREx data and prototyped the image pipeline (finding asteroid images, alignment, detection, labeling). The generator in this repository reuses that exploration code; everything else was built during the event.
- **AI assistance:** this project was developed with the help of AI tools (Anthropic's Claude) for code generation, review and documentation. AI-generated images or video in our materials carry a visible watermark. No AI-generated content uses NASA branding.

## License

Apache License 2.0 — see [LICENSE](LICENSE).

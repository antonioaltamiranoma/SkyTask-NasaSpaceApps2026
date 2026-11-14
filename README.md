# Sky Task

**NASA Space Apps Challenge 2026 · Challenge: Planet X and SPHEREx · Team Sky Task (Quito, Ecuador)**

Somos un equipo de Quito que trabaja en el desafío Planet X and SPHEREx. Queremos acercar los datos infrarrojos de la misión SPHEREx de la NASA a cualquier persona, convirtiendo unos segundos de atención en una contribución real a la ciencia.

*Every task solved is one more pair of eyes on the infrared sky.*

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

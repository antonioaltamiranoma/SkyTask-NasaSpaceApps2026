# explorer

The public website: demo pages now, the sky explorer later (card #8). This folder is the **Firebase Hosting** root (`firebase.json` at the repo root).

| Page | What it shows |
| --- | --- |
| `index.html` | A fake sign-up form with the widget in it, CAPTCHA-box style. Switch what the site gets back (nothing / score / token) and watch the widget's events. |
| `game.html` | The widget inside a made-up game screen, themed with the game's colors; desktop, phone portrait (bottom sheet) and phone landscape (side panel). |
| `inspector.html` | Calls the API itself and shows every response as it arrives: raw JSON, each image's size, dimensions and load time, PNG metadata chunks, and a check that public task data has only the allowed fields. With a dev server it also shows the **DEV ONLY** server-side view (kinds, answers, mix). |

## Where the widget comes from

The pages load the widget from **`/widget/sky-task.js`**; `widget/` is the single source.

- **Locally**, the server serves `explorer/` at `/` and `widget/` at `/widget/` (the mock server below does this; the service from card #5 should mount the same two folders).
- **On Firebase Hosting**, the `predeploy` step in `firebase.json` copies `widget/` into `explorer/widget/` before upload (that copy is git-ignored).

## Where the pages find the API

`assets/config.js`: same origin + `/v1` by default. For one browser tab you can point the pages elsewhere with `?api=`, e.g. `index.html?api=https://SERVICE.run.app/v1`. When the site moves to Firebase Hosting, set `DEFAULT_API` there to the Cloud Run URL. The API then needs CORS for the hosting origin.

## Run it locally (no installs)

From the repo root, with Python 3.10+:

```
python explorer/dev/mock_server.py
```

Open http://localhost:8080. The mock serves the pages, the widget and a fake API with synthetic star fields (no real data, no real answers), including the dev routes. Options: `--port`, `--no-dev` (hide the dev routes), `--slow 0.5` (slow images down).

`explorer/dev/` is **dev only**: it's excluded from Firebase deploys and the mock server refuses to serve it.

## End-to-end check

With the mock running on port 8099 and Playwright installed:

```
python explorer/dev/mock_server.py --port 8099
node explorer/dev/e2e.js ./shots
```

It takes screenshots at 320, 390 and 1280 px in light and dark, runs the full flow in all three result modes (including a failed token attempt), checks keyboard and reduced motion, the three game shapes and the inspector, and prints PASS/FAIL lines.

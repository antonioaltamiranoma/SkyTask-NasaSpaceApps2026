# widget — `<sky-task>`

The embeddable Sky Task micro-task: a few infrared pictures of one patch of sky blink in a loop, a circle marks one dot, and the person answers **Moving** or **Not moving**. One file, `sky-task.js`: vanilla JavaScript, a custom element with Shadow DOM and its own styles. No framework, no build step, no external fonts or CDNs.

## Embedding: two lines

```html
<script src="https://YOUR_SKY_TASK_HOST/widget/sky-task.js" defer></script>
<sky-task site-key="YOUR_SITE_KEY"></sky-task>
```

That is enough for it to look right: the widget carries its own styles inside its Shadow DOM, so the host page's CSS can't break it and the host doesn't have to write any. The host only adds CSS if it wants to match its own colors (see below).

## Attributes

| Attribute | Values | Default | What it does |
| --- | --- | --- | --- |
| `site-key` | string | — | Identifies the host site (sent to `POST /sessions`). |
| `api` | URL | same origin + `/v1` | Base URL of the Sky Task API (local service now, Cloud Run later). |
| `count` | 2–10 | `2` | Tasks per challenge. The site key can cap it on the server. |
| `result` | `none`, `score`, `token` | `none` | What the site gets back: nothing (science), a 0–100% score (games), or a pass token (CAPTCHA, card #9). |
| `theme` | `light`, `dark`, `auto` | `auto` | `auto` follows the visitor's system setting. |
| `layout` | `box`, `screen` | `box` | `box`: a CAPTCHA-sized card, up to 380 px wide. `screen`: fills the space the host gives it and switches to two columns from 440 px wide (for game consoles, side panels and bottom sheets). |

Changing `site-key`, `count`, `result` or `api` starts a fresh challenge.

## Styling without code

CSS custom properties, set on the element or any ancestor:

| Property | Default (light) | Use |
| --- | --- | --- |
| `--sky-task-accent` | `#FF8A4C` | Ring around the dot, current picture marker, focus outline |
| `--sky-task-bg` | `#F6F8FB` | Card background |
| `--sky-task-fg` | `#121A28` | Text and button outlines |
| `--sky-task-muted` | `#5A6577` | Secondary text |
| `--sky-task-line` | `#CDD5E0` | Borders and the timeline |
| `--sky-task-sky` | `#070C16` | Behind the pictures while loading |
| `--sky-task-radius` | `10px` | Corner radius |
| `--sky-task-font` | system UI stack | Font family |
| `--sky-task-stage-max` | `100%` | Max width of the picture area (handy inside short game screens, e.g. `40cqh`) |

For anything else, these `::part()` names are stable: `header`, `progress`, `viewport`, `status`, `controls`, `tick`, `answers`, `answer`, `answer-moving`, `answer-not-moving`, `result`, `again`, `retry`, `about`.

```css
sky-task { --sky-task-accent: #FFB547; --sky-task-bg: #0C1A1E; --sky-task-fg: #D7EEE8; --sky-task-radius: 4px; }
sky-task::part(answer) { text-transform: none; }
```

The disclosure line "Helps analyze NASA SPHEREx data" is deliberately **not** a part and sits outside every exposed part, so host CSS can't target or hide it. Its colour is derived from `--sky-task-fg` and `--sky-task-bg`, so it stays readable in any theme.

## Events and methods

| Event | `detail` | When |
| --- | --- | --- |
| `sky-task-result` | `{ mode, ...submit response }`: `{recorded}`, `{score}` or `{passed, token}` | After a challenge is submitted. In `token` mode a failed attempt also fires (`passed: false`) and a fresh challenge loads at once; the widget never says which answer was wrong. |
| `sky-task-error` | `{ stage, status, message }`, stage is `session`, `challenge`, `image` or `submit` | Something failed; the widget shows a "Try again" button. |

Both events bubble and cross the Shadow DOM boundary.

- `el.reset()` starts over with a fresh challenge.
- `el.getResponse()` returns the token after a passed `token` challenge, else `null`.
- In `token` mode, inside a `<form>`, the widget also keeps a hidden `<input name="sky-task-token">` up to date, so a normal form post carries the token. The host's **server** must then check it with `POST /siteverify` and its secret key; a token checked only in the browser proves nothing.

## Behaviour

- The pictures blink every 0.6 s. Play/pause, previous and next buttons; the timeline under the pictures spaces the dots by the real hours between pictures, and each dot can be clicked.
- Keyboard: Tab through the controls; on the picture area, ← → step and Space plays or pauses. The focus ring uses the text colour, so it keeps at least 3:1 contrast in any theme.
- Answers are ignored for 0.3 s after each new task appears, and a held Enter key never answers twice, so a double tap can't answer the next task by accident.
- Screen readers hear one short announcement per task; loading and errors are announced from the picture area (errors as alerts).
- With "reduce motion" on, nothing blinks by itself: the person steps through the pictures.
- Works from 320 px wide. Answer buttons are at least 44 px tall.
- The session id is kept in `sessionStorage` (per API and site key) and silently renewed if the server forgot it.
- Images are drawn on a canvas without reading pixels back, so the image host doesn't need CORS headers for them; the JSON calls do.

## How this compares to reCAPTCHA / Turnstile

Same embedding pattern: one script tag, one element with a site key, a token that the host's server verifies with a secret (`/siteverify`). Differences: it's a custom element rather than an iframe, so it styles to the host and adds no third-party frame; it can also run without any pass/fail at all (`result="none"` for science, `score` for games); and every answer contributes to real analysis of SPHEREx data. Unlike those services, it isn't hardened against bots yet: the token mode is a demo of the flow (card #9), not a security product.

## Trying it locally

See `explorer/README.md`: one command serves the demo pages, this widget and a mock API.

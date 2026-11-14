/*!
 * <sky-task> — embeddable Sky Task micro-task widget (NASA Space Apps 2026, team Sky Task)
 * One self-contained file: no framework, no build step, no external fonts or CDNs.
 * Usage:
 *   <script src="https://YOUR_HOST/widget/sky-task.js" defer></script>
 *   <sky-task site-key="demo" count="2" result="none"></sky-task>
 * Docs: widget/README.md. API: docs/api-contract.yaml. License: Apache-2.0.
 */
(() => {
  "use strict";
  if (customElements.get("sky-task")) return;

  const BLINK_MS = 600;
  const QUESTION = "Did the circled dot move?";
  const DISCLOSURE = "Helps analyze NASA SPHEREx data";
  const ABOUT =
    "These are infrared pictures of one small patch of sky, taken hours apart by NASA's SPHEREx space telescope. " +
    "Stars stay in place. Asteroids and other nearby objects shift between pictures. " +
    "Your answer, combined with many others, helps tell real moving objects from glitches.";

  const CSS = `
  :host {
    --_accent: var(--sky-task-accent, #FF8A4C);
    --_bg: var(--sky-task-bg, #F6F8FB);
    --_fg: var(--sky-task-fg, #121A28);
    --_muted: var(--sky-task-muted, #5A6577);
    --_line: var(--sky-task-line, #CDD5E0);
    --_sky: var(--sky-task-sky, #070C16);
    --_radius: var(--sky-task-radius, 10px);
    --_font: var(--sky-task-font, ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif);
    display: block;
    color-scheme: light;
    font-family: var(--_font);
    color: var(--_fg);
    -webkit-text-size-adjust: 100%;
  }
  :host([theme="dark"]) {
    --_bg: var(--sky-task-bg, #111A29);
    --_fg: var(--sky-task-fg, #E8EEF7);
    --_muted: var(--sky-task-muted, #9AA6B8);
    --_line: var(--sky-task-line, #2B374A);
    color-scheme: dark;
  }
  @media (prefers-color-scheme: dark) {
    :host([theme="auto"]) {
      --_bg: var(--sky-task-bg, #111A29);
      --_fg: var(--sky-task-fg, #E8EEF7);
      --_muted: var(--sky-task-muted, #9AA6B8);
      --_line: var(--sky-task-line, #2B374A);
      color-scheme: dark;
    }
  }
  :host([hidden]) { display: none; }
  :host(:not([layout="screen"])) { max-width: 380px; }
  :host([layout="screen"]) { container-type: inline-size; }

  * { box-sizing: border-box; }
  p { margin: 0; }
  button { font: inherit; color: inherit; }

  .st {
    position: relative;
    background: var(--_bg);
    border: 1px solid var(--_line);
    border-radius: var(--_radius);
    padding: 14px;
    display: grid;
    gap: 10px;
  }

  header { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; }
  .q { font-size: 1rem; font-weight: 650; line-height: 1.25; letter-spacing: -0.005em; }
  .prog { font-size: .8125rem; color: var(--_muted); font-variant-numeric: tabular-nums; white-space: nowrap; }

  .stage {
    position: relative;
    aspect-ratio: 1 / 1;
    width: min(100%, var(--sky-task-stage-max, 100%));
    justify-self: center;
    background: var(--_sky);
    border-radius: calc(var(--_radius) * .6);
    overflow: hidden;
    outline-offset: 3px;
  }
  .stage:focus-visible, button:focus-visible { outline: 2px solid var(--_accent); }
  canvas { display: block; width: 100%; height: 100%; }
  .veil {
    position: absolute; inset: 0; display: grid; place-items: center; text-align: center;
    padding: 16px; color: #E8EEF7; font-size: .875rem; background: color-mix(in srgb, var(--_sky) 82%, transparent);
  }
  .veil[hidden] { display: none; }
  .veil .retry { margin-top: 10px; }

  /* timeline: ticks spaced by real hours between pictures */
  .transport { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
  .icon {
    flex: none; width: 34px; height: 34px; border-radius: 8px; border: 1px solid var(--_line);
    background: transparent; display: grid; place-items: center; cursor: pointer; padding: 0;
  }
  .icon svg { width: 14px; height: 14px; fill: currentColor; }
  .icon:hover { border-color: var(--_muted); }
  .track { position: relative; flex: 1 1 190px; height: 34px; margin: 0 16px 0 14px; }
  .track::before {
    content: ""; position: absolute; left: 0; right: 0; top: 11px; height: 2px; background: var(--_line); border-radius: 1px;
  }
  .tick {
    position: absolute; top: 0; transform: translateX(-50%); display: grid; justify-items: center; gap: 3px;
    background: none; border: 0; padding: 4px 2px; cursor: pointer; color: var(--_muted);
    font-size: .6875rem; font-variant-numeric: tabular-nums; line-height: 1; border-radius: 6px; white-space: nowrap;
  }
  .tick::before {
    content: ""; width: 10px; height: 10px; border-radius: 50%; background: var(--_bg); border: 2px solid var(--_muted);
  }
  .tick[aria-current="true"] { color: var(--_fg); font-weight: 650; }
  .tick[aria-current="true"]::before { background: var(--_accent); border-color: var(--_accent); }

  .answers { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
  .answer {
    min-height: 46px; border-radius: 8px; border: 1.5px solid var(--_fg); background: transparent;
    font-weight: 650; font-size: 1rem; cursor: pointer; padding: 8px 10px;
  }
  .answer:hover:not(:disabled) { background: var(--_fg); color: var(--_bg); }
  .answer:disabled { opacity: .45; cursor: default; }

  footer { display: flex; justify-content: space-between; gap: 6px 12px; flex-wrap: wrap; font-size: .75rem; color: var(--_muted); line-height: 1.35; }
  .link { background: none; border: 0; padding: 0; text-decoration: underline; text-underline-offset: 2px; cursor: pointer; color: var(--_muted); font-size: inherit; }
  .link:hover { color: var(--_fg); }
  .about { font-size: .8125rem; line-height: 1.45; color: var(--_fg); border-left: 3px solid var(--_accent); padding: 2px 0 2px 10px; }
  .about[hidden] { display: none; }

  .done { display: grid; gap: 10px; justify-items: start; padding: 4px 0; }
  .done[hidden] { display: none; }
  .done .big { color: var(--_fg); font-size: 1.375rem; font-weight: 700; letter-spacing: -0.01em; }
  .done .ok::before { content: "✓ "; color: var(--_accent); }
  .done p { font-size: .875rem; color: var(--_muted); }
  .again { min-height: 40px; border-radius: 8px; border: 1.5px solid var(--_fg); background: transparent; font-weight: 600; padding: 6px 14px; cursor: pointer; }
  .again:hover { background: var(--_fg); color: var(--_bg); }
  .sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }

  /* game screen: two columns when the host gives us room */
  @container (min-width: 440px) {
    .st.task { grid-template-columns: minmax(0, 1.1fr) minmax(0, 1fr); grid-template-rows: auto auto 1fr auto; column-gap: 18px; }
    .st.task header { grid-column: 2; grid-row: 1; }
    .st.task .stage { grid-column: 1; grid-row: 1 / span 4; align-self: start; }
    .st.task .transport { grid-column: 2; grid-row: 2; }
    .st.task .answers { grid-column: 2; grid-row: 3; align-self: end; }
    .st.task footer { grid-column: 2; grid-row: 4; }
    .st.task .about { grid-column: 2; }
  }
  @media (prefers-reduced-motion: reduce) { * { transition: none !important; } }
  `;

  const ICON = {
    play: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 2.5v11l9-5.5z"/></svg>',
    pause: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 2.5h3v11H4zM9 2.5h3v11H9z"/></svg>',
    prev: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M11 2.5v11L3.5 8z"/></svg>',
    next: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M5 2.5v11L12.5 8z"/></svg>',
  };

  const fmtH = (h) => (h === 0 ? "0 h" : `+${Number.isInteger(h) ? h : h.toFixed(1)} h`);
  const store = {
    get(k) { try { return sessionStorage.getItem(k); } catch { return null; } },
    set(k, v) { try { sessionStorage.setItem(k, v); } catch { /* private mode etc. */ } },
    del(k) { try { sessionStorage.removeItem(k); } catch { /* ignore */ } },
  };
  const reducedMotion = () => window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;

  class HttpError extends Error {
    constructor(stage, status, message) { super(message); this.stage = stage; this.status = status; }
  }

  class SkyTask extends HTMLElement {
    static get observedAttributes() { return ["site-key", "count", "result", "api"]; }

    constructor() {
      super();
      this._root = this.attachShadow({ mode: "open" });
      this._root.innerHTML = `<style>${CSS}</style>
      <div class="st task" part="container" role="group" aria-label="Sky Task">
        <header part="header">
          <p class="q" id="q">${QUESTION}</p>
          <p class="prog" part="progress" aria-live="polite"></p>
        </header>
        <div class="stage" part="viewport" tabindex="0" role="img" aria-roledescription="blinking sky images">
          <canvas></canvas>
          <div class="veil" part="status"><div><p class="msg">Loading sky images…</p></div></div>
        </div>
        <div class="transport" part="controls">
          <button class="icon play" type="button" aria-label="Pause"></button>
          <button class="icon prev" type="button" aria-label="Previous picture">${ICON.prev}</button>
          <button class="icon next" type="button" aria-label="Next picture">${ICON.next}</button>
          <div class="track" role="group" aria-label="Pictures, by hours since the first"></div>
        </div>
        <div class="answers" part="answers" role="group" aria-labelledby="q">
          <button class="answer" part="answer answer-moving" type="button" data-choice="moving">Moving</button>
          <button class="answer" part="answer answer-not-moving" type="button" data-choice="not_moving">Not moving</button>
        </div>
        <div class="done" part="result" hidden aria-live="polite"></div>
        <footer part="footer">
          <span part="disclosure">${DISCLOSURE}</span>
          <button class="link about-btn" type="button" aria-expanded="false">What am I looking at?</button>
        </footer>
        <p class="about" part="about" hidden>${ABOUT}</p>
        <p class="sr" aria-live="polite"></p>
      </div>`;
      const $ = (s) => this._root.querySelector(s);
      this.$ = {
        st: $(".st"), prog: $(".prog"), stage: $(".stage"), canvas: $("canvas"), veil: $(".veil"),
        play: $(".play"), prev: $(".prev"), next: $(".next"), track: $(".track"),
        answers: [...this._root.querySelectorAll(".answer")], done: $(".done"), aboutBtn: $(".about-btn"),
        about: $(".about"), sr: $(".sr"), header: $("header"), transport: $(".transport"), answersBox: $(".answers"),
      };
      this._ctx = this.$.canvas.getContext("2d");
      this._timer = null;
      this._frame = 0;
      this._tasks = [];
      this._idx = 0;
      this._answers = [];
      this._token = null;
      this._busy = false;

      this.$.play.addEventListener("click", () => this._toggle());
      this.$.prev.addEventListener("click", () => this._step(-1));
      this.$.next.addEventListener("click", () => this._step(1));
      this.$.answers.forEach((b) => b.addEventListener("click", () => this._answer(b.dataset.choice)));
      this.$.aboutBtn.addEventListener("click", () => {
        const open = this.$.about.hidden;
        this.$.about.hidden = !open;
        this.$.aboutBtn.setAttribute("aria-expanded", String(open));
      });
      this._root.addEventListener("keydown", (e) => this._key(e));
      this._ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(() => this._draw()) : null;
    }

    connectedCallback() {
      if (!this.hasAttribute("theme")) this.setAttribute("theme", "auto");
      this._ro && this._ro.observe(this.$.stage);
      if (!this._started) { this._started = true; this._schedule(); }
    }
    disconnectedCallback() { this._stop(); this._ro && this._ro.disconnect(); }
    attributeChangedCallback(name, oldV, newV) {
      if (this._started && oldV !== newV && this.isConnected) this._schedule();
    }
    // coalesce several attribute changes (e.g. set right after upgrade) into one load
    _schedule() {
      if (this._pending) return;
      this._pending = true;
      queueMicrotask(() => { this._pending = false; this.reset(); });
    }

    // ------------------------------------------------------------ public API
    /** Start over with a fresh challenge. */
    reset() { this._setToken(null); this._load().catch(() => {}); }
    /** The CAPTCHA token after a passed `result="token"` challenge, else null. */
    getResponse() { return this._token; }

    // ------------------------------------------------------------ config
    get _api() {
      const a = this.getAttribute("api");
      return (a ? new URL(a, location.href).href : new URL("/v1", location.href).href).replace(/\/$/, "");
    }
    get _siteKey() { return this.getAttribute("site-key") || ""; }
    get _count() { const n = parseInt(this.getAttribute("count") || "2", 10); return Math.max(2, Math.min(10, isNaN(n) ? 2 : n)); }
    get _mode() { const m = this.getAttribute("result"); return ["none", "score", "token"].includes(m) ? m : "none"; }

    // ------------------------------------------------------------ network
    async _post(stage, path, body) {
      let res;
      try {
        res = await fetch(this._api + path, {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), credentials: "omit",
        });
      } catch (e) { throw new HttpError(stage, 0, "network"); }
      if (!res.ok) throw new HttpError(stage, res.status, `HTTP ${res.status}`);
      return res.json();
    }
    async _session(fresh) {
      const key = `sky-task:session:${this._api}:${this._siteKey}`;
      if (!fresh) { const s = store.get(key); if (s) return s; }
      const { session_id } = await this._post("session", "/sessions", { site_key: this._siteKey });
      store.set(key, session_id);
      return session_id;
    }
    _image(url) {
      return new Promise((resolve, reject) => {
        const img = new Image();
        img.decoding = "async";
        img.onload = () => resolve(img);
        img.onerror = () => reject(new HttpError("image", 0, "image failed to load"));
        img.src = new URL(url, this._api + "/").href;
      });
    }

    async _load() {
      const gen = (this._gen = (this._gen || 0) + 1);
      this._stop();
      this._busy = true;
      this._showTask(true);
      this._veil("Loading sky images…");
      this._setAnswering(false);
      this.$.prog.textContent = "";
      try {
        let sid = await this._session(false);
        let ch;
        try {
          ch = await this._post("challenge", "/challenge", { session_id: sid, count: this._count, result: this._mode });
        } catch (e) {
          if (e.status !== 401 && e.status !== 403 && e.status !== 404) throw e;
          sid = await this._session(true); // stale session: start a new one once
          ch = await this._post("challenge", "/challenge", { session_id: sid, count: this._count, result: this._mode });
        }
        if (gen !== this._gen) return;
        const imgs = await Promise.all(ch.tasks.map((t) => this._image(t.image_url)));
        if (gen !== this._gen) return;
        this._challenge = ch;
        this._tasks = ch.tasks.map((t, i) => ({ ...t, img: imgs[i] }));
        this._answers = [];
        this._idx = 0;
        this._busy = false;
        this._veil(null);
        this._startTask();
      } catch (e) {
        if (gen !== this._gen) return;
        this._busy = false;
        this._fail(e);
      }
    }

    _fail(e) {
      const msg = e.stage === "image"
        ? "The sky images didn't load. Check your connection, then try again."
        : e.status === 0
          ? "Can't reach the Sky Task service. Check your connection, then try again."
          : "The Sky Task service couldn't start a challenge. Try again in a moment.";
      this._veil(msg, true);
      this.dispatchEvent(new CustomEvent("sky-task-error", {
        bubbles: true, composed: true, detail: { stage: e.stage || "unknown", status: e.status ?? null, message: e.message },
      }));
    }

    _veil(text, retry) {
      const v = this.$.veil;
      if (!text) { v.hidden = true; return; }
      v.hidden = false;
      v.innerHTML = `<div><p class="msg"></p>${retry ? '<button class="again retry" type="button" part="retry">Try again</button>' : ""}</div>`;
      v.querySelector(".msg").textContent = text;
      if (retry) v.querySelector(".retry").addEventListener("click", () => this.reset());
    }

    // ------------------------------------------------------------ one task
    get _task() { return this._tasks[this._idx]; }

    _startTask() {
      const t = this._task;
      this.$.prog.textContent = this._tasks.length > 1 ? `${this._idx + 1} of ${this._tasks.length}` : "";
      this._frame = 0;
      this._buildTicks(t);
      this._setAnswering(true);
      this._draw();
      this.$.sr.textContent = `Picture set ${this._idx + 1} of ${this._tasks.length}. ${t.frame_count} pictures.`;
      if (reducedMotion()) this._stop(); else this._playLoop();
    }

    _buildTicks(t) {
      const offs = t.frame_offsets_h && t.frame_offsets_h.length === t.frame_count
        ? t.frame_offsets_h : Array.from({ length: t.frame_count }, (_, i) => i);
      const max = Math.max(...offs) || 1;
      // spread evenly when real spacing would make labels collide
      const minGap = Math.min(...offs.slice(1).map((o, i) => (o - offs[i]) / max));
      const pos = minGap < 0.16 ? offs.map((_, i) => i / (offs.length - 1)) : offs.map((o) => o / max);
      this.$.track.innerHTML = "";
      this._ticks = offs.map((o, i) => {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "tick";
        b.setAttribute("part", "tick");
        b.style.left = `${(pos[i] * 100).toFixed(2)}%`;
        b.textContent = fmtH(o);
        b.setAttribute("aria-label", `Picture ${i + 1} of ${offs.length}, ${o === 0 ? "first" : fmtH(o).replace("+", "") + " later"}`);
        b.addEventListener("click", () => { this._stop(); this._show(i); });
        this.$.track.appendChild(b);
        return b;
      });
    }

    _draw() {
      const t = this._task;
      const c = this.$.canvas;
      const rect = c.getBoundingClientRect();
      const dpr = Math.min(window.devicePixelRatio || 1, 3);
      const W = Math.max(1, Math.round(rect.width * dpr));
      if (c.width !== W) { c.width = W; c.height = W; }
      const ctx = this._ctx;
      ctx.fillStyle = "#000";
      ctx.fillRect(0, 0, W, W);
      if (!t) return;
      const fs = t.frame_size;
      ctx.imageSmoothingEnabled = false;
      ctx.drawImage(t.img, this._frame * fs, 0, fs, fs, 0, 0, W, W);
      const k = W / fs;
      const h = t.highlight || { x: fs / 2, y: fs / 2, r: 6 };
      const x = (h.x + 0.5) * k, y = (h.y + 0.5) * k, r = Math.max(h.r * k, 10 * dpr);
      const accent = getComputedStyle(this).getPropertyValue("--sky-task-accent").trim() || "#FF8A4C";
      ctx.lineWidth = 4 * dpr; ctx.strokeStyle = "rgba(0,0,0,.55)";
      ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.stroke();
      ctx.lineWidth = 2 * dpr; ctx.strokeStyle = accent;
      ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.stroke();
      (this._ticks || []).forEach((b, i) => b.setAttribute("aria-current", String(i === this._frame)));
      this.$.stage.setAttribute("aria-label",
        `Picture ${this._frame + 1} of ${t.frame_count}. A circle marks one dot.`);
    }

    _show(i) { const n = this._task.frame_count; this._frame = ((i % n) + n) % n; this._draw(); }
    _step(d) { if (!this._task) return; this._stop(); this._show(this._frame + d); }
    _playLoop() {
      this._stop();
      this._playing = true;
      this._setPlayIcon();
      this._timer = setInterval(() => this._show(this._frame + 1), BLINK_MS);
    }
    _stop() { clearInterval(this._timer); this._timer = null; this._playing = false; this._setPlayIcon(); }
    _toggle() { if (!this._task) return; this._playing ? this._stop() : this._playLoop(); }
    _setPlayIcon() {
      this.$.play.innerHTML = this._playing ? ICON.pause : ICON.play;
      this.$.play.setAttribute("aria-label", this._playing ? "Pause" : "Play");
    }

    _setAnswering(on) { this.$.answers.forEach((b) => (b.disabled = !on)); }

    _key(e) {
      if (e.altKey || e.ctrlKey || e.metaKey || !this._task || this._busy) return;
      const onButton = e.composedPath()[0]?.tagName === "BUTTON";
      if (e.key === "ArrowLeft") { this._step(-1); e.preventDefault(); }
      else if (e.key === "ArrowRight") { this._step(1); e.preventDefault(); }
      else if (e.key === " " && !onButton) { this._toggle(); e.preventDefault(); }
    }

    async _answer(choice) {
      if (this._busy || !this._task) return;
      this._answers.push({ task_ref: this._task.task_ref, choice });
      if (this._idx + 1 < this._tasks.length) {
        this._idx += 1;
        this._startTask();
        this.$.answers[choice === "moving" ? 0 : 1].focus({ preventScroll: true });
        return;
      }
      this._busy = true;
      this._stop();
      this._setAnswering(false);
      this._veil("Sending your answers…");
      try {
        const res = await this._post("submit", `/challenge/${encodeURIComponent(this._challenge.challenge_id)}/submit`, { answers: this._answers });
        this._busy = false;
        this._result(res);
      } catch (e) {
        this._busy = false;
        this._fail(e);
      }
    }

    // ------------------------------------------------------------ results
    _showTask(on) {
      const { header, stage, transport, answersBox, done, st } = this.$;
      [header, stage, transport, answersBox].forEach((el) => (el.hidden = !on));
      [header, stage, transport, answersBox].forEach((el) => (el.style.display = on ? "" : "none"));
      st.classList.toggle("task", on);
      done.hidden = on;
    }

    _result(res) {
      const mode = this._mode;
      if (mode === "token") this._setToken(res.passed ? res.token || null : null);
      this.dispatchEvent(new CustomEvent("sky-task-result", { bubbles: true, composed: true, detail: { mode, ...res } }));
      if (mode === "token" && !res.passed) {
        // never say which answer was wrong: just bring a fresh challenge
        this.$.sr.textContent = "Let's try a fresh set.";
        this._load();
        return;
      }
      this._veil(null);
      this._showTask(false);
      const d = this.$.done;
      if (mode === "token") {
        d.innerHTML = `<p class="big ok">Verified</p><p>Thanks for looking at the sky.</p>`;
      } else if (mode === "score") {
        d.innerHTML = `<p class="big"></p><p>Your answers now join everyone else's.</p><button class="again" type="button" part="again">Play again</button>`;
        d.querySelector(".big").textContent = `You scored ${Number(res.score)}%`;
      } else {
        d.innerHTML = `<p class="big ok">Answers recorded</p><p>Each answer is one more pair of eyes on the infrared sky.</p><button class="again" type="button" part="again">Look at another patch</button>`;
      }
      const again = d.querySelector(".again");
      if (again) { again.addEventListener("click", () => this.reset()); again.focus({ preventScroll: true }); }
    }

    _setToken(tok) {
      this._token = tok;
      const form = this.closest("form");
      if (!form) return;
      let input = this.querySelector('input[name="sky-task-token"]');
      if (!input) {
        input = document.createElement("input");
        input.type = "hidden";
        input.name = "sky-task-token";
        this.appendChild(input); // light DOM, so the host form submits it
      }
      input.value = tok || "";
    }
  }

  customElements.define("sky-task", SkyTask);
})();

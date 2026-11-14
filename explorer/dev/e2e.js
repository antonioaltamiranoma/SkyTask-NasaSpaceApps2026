const { chromium } = require("playwright");
// DEV ONLY end-to-end check of the widget and demo pages against the mock server (or a local service with SKYTASK_DEV=1).
// Usage (repo root):  python explorer/dev/mock_server.py --port 8099   then   node explorer/dev/e2e.js /tmp/shots
// Needs Playwright (npm i -D playwright && npx playwright install chromium). Prints PASS/FAIL lines; exits 1 on any FAIL.
const OUT = process.argv[2] || ".";
const BASE = process.env.SKYTASK_BASE || "http://127.0.0.1:8099";
(async () => {
  const browser = await chromium.launch();
  const results = [];
  const ok = (name, cond, info = "") => { results.push(`${cond ? "PASS" : "FAIL"} ${name} ${info}`); };

  for (const scheme of ["light", "dark"]) {
    for (const w of [320, 390, 1280]) {
      const ctx = await browser.newContext({ viewport: { width: w, height: 900 }, colorScheme: scheme, deviceScaleFactor: 1 });
      const page = await ctx.newPage();
      const errs = []; page.on("pageerror", (e) => errs.push(e.message)); page.on("console", (m) => m.type() === "error" && errs.push(m.text()));
      await page.goto(BASE + "/");
      await page.waitForFunction(() => { const w = document.querySelector("sky-task"); return w && w.shadowRoot && w.shadowRoot.querySelector(".veil").hidden; }, null, { timeout: 15000 });
      await page.waitForTimeout(300);
      await page.screenshot({ path: `${OUT}/index-${w}-${scheme}.png`, fullPage: true });
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
      ok(`index ${w} ${scheme} no horizontal scroll`, !overflow);
      ok(`index ${w} ${scheme} no errors`, errs.length === 0, errs.join(" | "));
      await ctx.close();
    }
  }

  // full flow, none / score / token, via the index page mode switch
  {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const page = await ctx.newPage();
    await page.goto(BASE + "/");
    await page.evaluate(() => { window.__ev = []; document.addEventListener("sky-task-result", (e) => window.__ev.push(e.detail)); });
    for (const mode of ["none", "score", "token"]) {
      await page.check(`input[value=${mode}]`);
      await page.waitForFunction(() => { const s = document.querySelector("sky-task").shadowRoot; return s.querySelector(".veil").hidden && !s.querySelector(".answer").disabled; });
      const n0 = await page.evaluate(() => window.__ev.length);
      // answer with the dev view's truth so token passes
      const cid = await page.evaluate(() => document.querySelector("sky-task")._challenge.challenge_id);
      const dev = await (await page.request.get(`${BASE}/v1/dev/challenge/${cid}`)).json();
      for (const t of dev.tasks) {
        const choice = t.answer || "moving";
        await page.locator("sky-task").locator(`.answer[data-choice=${choice}]`).click();
        await page.waitForTimeout(80);
      }
      await page.waitForFunction((n) => window.__ev.length > n, n0, { timeout: 8000 });
      const ev = await page.evaluate(() => window.__ev[window.__ev.length - 1]);
      ok(`flow ${mode} result event`, ev.mode === mode, JSON.stringify(ev));
      if (mode === "token") {
        const tok = await page.evaluate(() => document.querySelector("sky-task").getResponse());
        const hidden = await page.evaluate(() => document.querySelector('input[name="sky-task-token"]').value);
        ok("token in getResponse and hidden input", !!tok && tok === hidden);
        ok("submit button enabled after token", !(await page.locator("#go").isDisabled()));
      }
      await page.screenshot({ path: `${OUT}/flow-${mode}.png` });
    }
    // token fail -> fresh challenge, no "which was wrong"
    await page.evaluate(() => document.querySelector("sky-task").reset());
    await page.waitForFunction(() => { const s = document.querySelector("sky-task").shadowRoot; return s.querySelector(".veil").hidden && !s.querySelector(".answer").disabled; });
    const cid = await page.evaluate(() => document.querySelector("sky-task")._challenge.challenge_id);
    const dev = await (await page.request.get(`${BASE}/v1/dev/challenge/${cid}`)).json();
    const n0 = await page.evaluate(() => window.__ev.length);
    for (const t of dev.tasks) {
      const wrong = t.answer === "moving" ? "not_moving" : "moving";
      await page.locator("sky-task").locator(`.answer[data-choice=${wrong}]`).click();
      await page.waitForTimeout(80);
    }
    await page.waitForFunction((n) => window.__ev.length > n, n0);
    await page.waitForFunction((c) => { const w = document.querySelector("sky-task"); return w._challenge && w._challenge.challenge_id !== c && w.shadowRoot.querySelector(".veil").hidden; }, cid, { timeout: 8000 });
    ok("token fail loads a fresh challenge", true);
    // keyboard
    await page.locator("sky-task").locator(".stage").focus();
    const f0 = await page.evaluate(() => document.querySelector("sky-task")._frame);
    await page.keyboard.press("ArrowRight");
    const f1 = await page.evaluate(() => document.querySelector("sky-task")._frame);
    const playing = await page.evaluate(() => document.querySelector("sky-task")._playing);
    ok("arrow key steps and pauses", !playing && f1 !== undefined, `${f0}->${f1}`);
    await ctx.close();
  }

  // reduced motion: no auto blink
  {
    const ctx = await browser.newContext({ viewport: { width: 390, height: 800 }, reducedMotion: "reduce" });
    const page = await ctx.newPage();
    await page.goto(BASE + "/");
    await page.waitForFunction(() => { const s = document.querySelector("sky-task").shadowRoot; return s.querySelector(".veil").hidden; });
    const f0 = await page.evaluate(() => document.querySelector("sky-task")._frame);
    await page.waitForTimeout(1500);
    const f1 = await page.evaluate(() => document.querySelector("sky-task")._frame);
    ok("reduced motion: no autoplay", f0 === f1 && f0 === 0);
    await ctx.close();
  }

  // game page: desktop (side panel), portrait (bottom sheet), landscape
  for (const [label, vw, btn] of [["desktop", 1280, "Desktop"], ["portrait", 420, "Phone, portrait"], ["landscape", 1280, "Phone, landscape"]]) {
    const ctx = await browser.newContext({ viewport: { width: vw, height: 900 } });
    const page = await ctx.newPage();
    const errs = []; page.on("pageerror", (e) => errs.push(e.message));
    await page.goto(BASE + "/game.html");
    await page.getByRole("button", { name: btn }).click();
    await page.click("#scan");
    await page.waitForFunction(() => { const w = document.querySelector("sky-task"); return w && w.shadowRoot && w.shadowRoot.querySelector(".veil").hidden; }, null, { timeout: 15000 });
    await page.waitForTimeout(500);
    await page.locator("#frame").screenshot({ path: `${OUT}/game-${label}.png` });
    ok(`game ${label} no errors`, errs.length === 0, errs.join(" | "));
    if (label === "desktop") {
      await page.evaluate(() => { window.__ev = []; document.addEventListener("sky-task-result", (e) => window.__ev.push(e.detail)); });
      for (let i = 0; i < 3; i++) { await page.locator("sky-task").locator(".answer").first().click(); await page.waitForTimeout(80); }
      await page.waitForFunction(() => window.__ev.length > 0);
      const ev = await page.evaluate(() => window.__ev[0]);
      ok("game score event", typeof ev.score === "number", JSON.stringify(ev));
      await page.waitForTimeout(400);
      await page.locator("#frame").screenshot({ path: `${OUT}/game-result.png` });
    }
    await ctx.close();
  }

  // inspector
  {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 1000 } });
    const page = await ctx.newPage();
    await page.goto(BASE + "/inspector.html");
    await page.click("#run");
    await page.waitForSelector("#dev:not([hidden])", { timeout: 15000 });
    await page.click("#submit");
    await page.waitForTimeout(800);
    const bad = await page.locator(".check.bad").count();
    ok("inspector: no public-data problems on mock", bad === 0);
    await page.screenshot({ path: `${OUT}/inspector.png`, fullPage: true });
    await ctx.close();
  }
  console.log(results.join("\n"));
  if (results.some((r) => r.startsWith("FAIL"))) process.exitCode = 1;
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });

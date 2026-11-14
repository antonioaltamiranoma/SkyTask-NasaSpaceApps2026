// Where the demo pages find the Sky Task API. Load this as a classic script at the END of <body>
// (after the <sky-task> elements, before the deferred widget script runs).
// Default: same origin — the local service or the mock server serve both the pages and /v1.
// Override for this tab with ?api=https://SERVICE.run.app/v1
// When the site moves to Firebase Hosting, set DEFAULT_API to the Cloud Run URL + "/v1".
(function () {
  var DEFAULT_API = location.origin + "/v1";
  var q = new URLSearchParams(location.search).get("api");
  var saved = null;
  try {
    if (q) sessionStorage.setItem("skytask-api", q);
    saved = sessionStorage.getItem("skytask-api");
  } catch (e) { /* storage blocked: fall back to default */ }
  window.SKYTASK_API = q || saved || DEFAULT_API;
  document.querySelectorAll("sky-task:not([api])").forEach(function (el) { el.setAttribute("api", window.SKYTASK_API); });
  document.querySelectorAll("[data-api-label]").forEach(function (el) { el.textContent = window.SKYTASK_API; });
})();

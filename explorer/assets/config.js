// Where the demo pages find the Sky Task API. Load this as a classic script at the END of <body>
// (after the <sky-task> elements, before the deferred widget script runs).
// Default: same origin — the local service or the mock server serve both the pages and /v1.
// Override for this tab with ?api=https://SERVICE.run.app/v1
// On *.web.app / *.firebaseapp.com it uses the Cloud Run URL below.
(function () {
  // On Firebase Hosting the pages call the Cloud Run API cross-origin, like any host site embedding the widget.
  var CLOUD_API = "https://skytask-api-659703561463.us-central1.run.app/v1";
  var DEFAULT_API = /(\.web\.app|\.firebaseapp\.com)$/.test(location.hostname) ? CLOUD_API : location.origin + "/v1";
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

/* Hostzilla panel — lightweight job-status polling. No dependencies. */
(function () {
  "use strict";

  var TERMINAL = { ok: true, error: true };

  function esc(s) {
    if (s === null || s === undefined) return "";
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function renderResult(el, result) {
    if (!el || !result) return;
    var parts = [];
    if (result.url) {
      parts.push('<p><strong>URL:</strong> <a class="link" href="' +
        esc(result.url) + '" target="_blank" rel="noopener">' +
        esc(result.url) + "</a></p>");
    }
    if (result.docroot) parts.push('<p><strong>Doc root:</strong> <span class="mono">' + esc(result.docroot) + "</span></p>");
    if (result.db) parts.push('<p><strong>Database:</strong> <span class="mono">' + esc(result.db) + "</span></p>");
    if (result.php_sock) parts.push('<p><strong>PHP socket:</strong> <span class="mono">' + esc(result.php_sock) + "</span></p>");
    if (result.ssl) parts.push("<p><strong>SSL:</strong> " + esc(result.ssl) + "</p>");
    if (result.purged_db) parts.push("<p><strong>Purged DB:</strong> " + esc(result.purged_db) + "</p>");
    if (result.message) parts.push('<p class="error-text"><strong>Message:</strong> ' + esc(result.message) + "</p>");
    if (parts.length) el.innerHTML = parts.join("");
  }

  function pollJob() {
    var panel = document.getElementById("job-panel");
    if (!panel) return;

    var status = panel.getAttribute("data-status");
    var url = panel.getAttribute("data-status-url");
    if (!url || TERMINAL[status]) return;

    var badge = document.getElementById("job-status-badge");
    var logEl = document.getElementById("job-log");
    var resultEl = document.getElementById("job-result");
    var finishedEl = document.getElementById("job-finished");
    var hint = document.getElementById("job-live-hint");

    var timer = setInterval(function () {
      fetch(url, { headers: { "X-Requested-With": "fetch" } })
        .then(function (r) {
          if (!r.ok) throw new Error("status " + r.status);
          return r.json();
        })
        .then(function (data) {
          if (badge) {
            badge.textContent = data.status;
            badge.className = "badge badge-" + data.status;
          }
          if (logEl && data.log) logEl.textContent = data.log;
          if (finishedEl && data.finished_at) finishedEl.textContent = data.finished_at;
          renderResult(resultEl, data.result);

          if (TERMINAL[data.status]) {
            clearInterval(timer);
            if (hint) hint.classList.add("hidden");
          }
        })
        .catch(function () {
          /* transient error: keep polling */
        });
    }, 2000);
  }

  window.HZ_pollJob = pollJob;
})();

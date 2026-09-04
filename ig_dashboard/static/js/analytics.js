/* IG Console analytics — Chart.js helpers for History + Run Control */
(function (global) {
  "use strict";

  var PALETTE = [
    "#5fd4a4",
    "#4aa8e8",
    "#e8c06a",
    "#e87880",
    "#a78bfa",
    "#67e8f9",
    "#fb923c",
    "#94a3b8",
    "#f472b6",
    "#34d399",
  ];

  // Semantic outcome colors (operator UX) — never paint POST_DONE red
  var COLOR_OK = "#5fd4a4"; // green — real win
  var COLOR_SKIP = "#e8c06a"; // yellow — skipped / no work
  var COLOR_WARN = "#fb923c"; // orange — captcha / IP mask / cool
  var COLOR_MUTE = "#94a3b8"; // slate — unknown
  var FAIL_SHADES = [
    "#e87880",
    "#dc2626",
    "#f87171",
    "#b91c1c",
    "#fb7185",
    "#ef4444",
  ];

  function failShade(label) {
    var s = String(label || "");
    var h = 0;
    for (var i = 0; i < s.length; i++) {
      h = (h + s.charCodeAt(i) * (i + 1)) % FAIL_SHADES.length;
    }
    return FAIL_SHADES[h];
  }

  function colorForOutcome(label, kind) {
    var k = String(label || "")
      .trim()
      .toUpperCase();
    if (!k) return COLOR_MUTE;

    // Wins
    if (
      k === "POST_DONE" ||
      k === "LOGGED_IN" ||
      k === "WARMUP_DONE" ||
      k === "POST_LIVE"
    ) {
      return COLOR_OK;
    }

    // Skips / soft no-op (not a hard fail)
    if (
      k === "SKIPPED" ||
      k === "NO_IMAGE" ||
      k === "NO_ACCOUNT_DRIVE" ||
      k.indexOf("SKIP") >= 0
    ) {
      return COLOR_SKIP;
    }

    // Risk / cool-down (not green, not hard-fail red)
    if (
      k === "ALL_IPS_MASKED" ||
      k === "CAPTCHA" ||
      k === "POST_CAPTCHA" ||
      k === "HUMAN_BLOCKED" ||
      k === "HUMAN_CHECK" ||
      k === "CONTACT_VERIFY" ||
      k === "CHALLENGE"
    ) {
      return COLOR_WARN;
    }

    // Failures — red family (shade by label so pie slices stay distinct)
    if (
      k.indexOf("TIMEOUT") >= 0 ||
      k.indexOf("BLOCKED") >= 0 ||
      k.indexOf("DEAD") >= 0 ||
      k.indexOf("REJECT") >= 0 ||
      k.indexOf("STUCK") >= 0 ||
      k.indexOf("SUSPEND") >= 0 ||
      k.indexOf("NOT_FOUND") >= 0 ||
      k.indexOf("RATE_LIMIT") >= 0 ||
      k.indexOf("ERROR") >= 0 ||
      k.indexOf("WONT_OPEN") >= 0 ||
      k.indexOf("SIGNUP") >= 0 ||
      k.indexOf("META") >= 0 ||
      k === "NO_LOGIN_BTN" ||
      k === "LOGIN_TIMEOUT" ||
      k === "LOGIN_REJECTED" ||
      k === "PROXY_DEAD" ||
      k === "APP_WONT_OPEN" ||
      k === "ACCOUNT_SUSPENDED" ||
      k === "ACCOUNT_NOT_FOUND" ||
      k === "ACCOUNT_CHALLENGED" ||
      k === "TIP_STUCK" ||
      k === "UNKNOWN_STUCK" ||
      k === "POST_TIMEOUT" ||
      k === "POST_BLOCKED" ||
      k === "POST_RATE_LIMIT" ||
      (kind === "post" && k.indexOf("POST_") === 0 && k !== "POST_DONE")
    ) {
      return failShade(k);
    }

    if (kind === "format") {
      var fi = ["FEED", "REEL", "CAROUSEL", "STORY"].indexOf(k);
      if (fi >= 0) return PALETTE[fi];
      return PALETTE[Math.abs(k.length) % PALETTE.length];
    }

    return COLOR_MUTE;
  }

  function colorsForLabels(labels, kind) {
    return (labels || []).map(function (lab) {
      return colorForOutcome(lab, kind);
    });
  }

  var chartRefs = {};

  function destroyChart(id) {
    if (chartRefs[id]) {
      try {
        chartRefs[id].destroy();
      } catch (e) {}
      delete chartRefs[id];
    }
  }

  function canvasCtx(id) {
    var el = document.getElementById(id);
    return el && el.getContext ? el.getContext("2d") : null;
  }

  function countsToSeries(obj) {
    var labels = [];
    var data = [];
    Object.keys(obj || {}).forEach(function (k) {
      labels.push(k);
      data.push(obj[k]);
    });
    return { labels: labels, data: data };
  }

  function emptyMsg(wrapId, msg) {
    var wrap = document.getElementById(wrapId);
    if (!wrap) return;
    var note = wrap.querySelector(".analytics-empty");
    if (!note) {
      note = document.createElement("p");
      note.className = "analytics-empty muted";
      wrap.appendChild(note);
    }
    note.textContent = msg || "No data yet.";
    note.hidden = false;
  }

  function hideEmpty(wrapId) {
    var wrap = document.getElementById(wrapId);
    if (!wrap) return;
    var note = wrap.querySelector(".analytics-empty");
    if (note) note.hidden = true;
  }

  function baseOpts() {
    return {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          labels: { color: "#9ec4e8", boxWidth: 12, font: { size: 11 } },
        },
        tooltip: {
          backgroundColor: "rgba(8,12,20,0.92)",
          titleColor: "#e8f2ff",
          bodyColor: "#9ec4e8",
          borderColor: "rgba(160,200,235,0.2)",
          borderWidth: 1,
        },
      },
      scales: {
        x: {
          ticks: { color: "#6d7f94", font: { size: 10 } },
          grid: { color: "rgba(160,200,235,0.06)" },
        },
        y: {
          ticks: { color: "#6d7f94", font: { size: 10 } },
          grid: { color: "rgba(160,200,235,0.06)" },
          beginAtZero: true,
        },
      },
    };
  }

  function pieOpts() {
    var o = baseOpts();
    delete o.scales;
    return o;
  }

  function renderLineDaily(canvasId, daily) {
    if (typeof Chart === "undefined") return;
    destroyChart(canvasId);
    var ctx = canvasCtx(canvasId);
    if (!ctx) return;
    daily = daily || [];
    if (!daily.length) return;
    var labels = daily.map(function (d) {
      return (d.date || "").slice(5);
    });
    // Keep rates for tooltips only (operator-friendly: counts on chart)
    var rates = daily.map(function (d) {
      return d.success_rate != null ? d.success_rate : 0;
    });
    chartRefs[canvasId] = new Chart(ctx, {
      type: "line",
      data: {
        labels: labels,
        datasets: [
          {
            label: "Attempts",
            data: daily.map(function (d) {
              return d.attempts || 0;
            }),
            borderColor: "#4aa8e8",
            backgroundColor: "rgba(74,168,232,0.10)",
            tension: 0.25,
            fill: true,
            borderDash: [4, 3],
            pointRadius: 3,
            pointHoverRadius: 5,
          },
          {
            label: "POST_DONE",
            data: daily.map(function (d) {
              return d.post_done || 0;
            }),
            borderColor: "#5fd4a4",
            backgroundColor: "transparent",
            tension: 0.3,
            fill: false,
            pointRadius: 4,
            pointHoverRadius: 7,
            pointBackgroundColor: "#5fd4a4",
          },
        ],
      },
      options: (function () {
        var o = baseOpts();
        o.scales.y.title = {
          display: true,
          text: "Count",
          color: "#6d7f94",
          font: { size: 10 },
        };
        o.plugins.tooltip = o.plugins.tooltip || {};
        o.plugins.tooltip.callbacks = {
          afterBody: function (items) {
            if (!items || !items.length) return "";
            var idx = items[0].dataIndex;
            var rate = rates[idx];
            var att = (daily[idx] && daily[idx].attempts) || 0;
            var done = (daily[idx] && daily[idx].post_done) || 0;
            return [
              "Success rate: " + rate + "%",
              "(" + done + " / " + att + ")",
            ];
          },
        };
        return o;
      })(),
    });
  }

  function renderPie(canvasId, counts, kind) {
    if (typeof Chart === "undefined") return;
    destroyChart(canvasId);
    var ctx = canvasCtx(canvasId);
    if (!ctx) return;
    var s = countsToSeries(counts);
    if (!s.labels.length) return;
    chartRefs[canvasId] = new Chart(ctx, {
      type: "doughnut",
      data: {
        labels: s.labels,
        datasets: [
          {
            data: s.data,
            backgroundColor: colorsForLabels(s.labels, kind || "post"),
            borderWidth: 0,
          },
        ],
      },
      options: pieOpts(),
    });
  }

  function renderBar(canvasId, counts, horizontal, kind) {
    if (typeof Chart === "undefined") return;
    destroyChart(canvasId);
    var ctx = canvasCtx(canvasId);
    if (!ctx) return;
    var s = countsToSeries(counts);
    if (!s.labels.length) return;
    var isFormat = (kind || "") === "format" && !horizontal;
    chartRefs[canvasId] = new Chart(ctx, {
      type: "bar",
      data: {
        labels: s.labels,
        datasets: [
          {
            label: "Count",
            data: s.data,
            backgroundColor: colorsForLabels(s.labels, kind || "login"),
            borderRadius: isFormat ? 6 : 4,
            maxBarThickness: isFormat ? 52 : horizontal ? 28 : 64,
            categoryPercentage: isFormat ? 0.45 : 0.8,
            barPercentage: isFormat ? 0.7 : 0.9,
          },
        ],
      },
      options: (function () {
        var o = baseOpts();
        if (horizontal) {
          o.indexAxis = "y";
        }
        o.plugins.legend.display = false;
        if (isFormat) {
          o.layout = { padding: { left: 8, right: 8, top: 4, bottom: 0 } };
        }
        return o;
      })(),
    });
  }

  function setStat(id, text) {
    var el = document.getElementById(id);
    if (el) el.textContent = text;
  }

  function bindHistoryAnalytics(opts) {
    opts = opts || {};
    var days = opts.days || 14;
    var url =
      opts.url ||
      "/api/analytics/overview?days=" + encodeURIComponent(days);
    fetch(url, { credentials: "same-origin" })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        data = data || {};
        setStat("an-attempts", String(data.attempts || 0));
        setStat("an-post-done", String(data.post_done || 0));
        setStat(
          "an-success-rate",
          (data.success_rate != null ? data.success_rate : 0) + "%"
        );
        setStat("an-days", String(data.days || days) + "d");

        if (!(data.daily || []).length && !(data.attempts > 0)) {
          emptyMsg("analytics-line-wrap", "No ledger rows in this window yet.");
          emptyMsg("analytics-post-wrap", "No post outcomes yet.");
          emptyMsg("analytics-login-wrap", "No login outcomes yet.");
          emptyMsg("analytics-format-wrap", "No format mix yet.");
          return;
        }
        hideEmpty("analytics-line-wrap");
        hideEmpty("analytics-post-wrap");
        hideEmpty("analytics-login-wrap");
        hideEmpty("analytics-format-wrap");
        renderLineDaily("chart-daily", data.daily || []);
        renderPie("chart-post", data.post_counts || {}, "post");
        renderBar("chart-login", data.login_counts || {}, true, "login");
        renderBar("chart-format", data.format_counts || {}, false, "format");
      })
      .catch(function () {
        emptyMsg("analytics-line-wrap", "Could not load analytics.");
      });
  }

  function fillRunTable(rows) {
    var body = document.getElementById("run-an-tbody");
    if (!body) return;
    body.innerHTML = "";
    (rows || []).slice(0, 40).forEach(function (r) {
      var tr = document.createElement("tr");
      tr.innerHTML =
        "<td class='mono'>" +
        esc(r.time || "") +
        "</td><td>" +
        esc(r.username || "") +
        "</td><td class='mono'>" +
        esc(r.format || "feed") +
        "</td><td>" +
        esc(r.login || "") +
        "</td><td>" +
        esc(r.post || "") +
        "</td>";
      body.appendChild(tr);
    });
    if (!(rows || []).length) {
      var tr0 = document.createElement("tr");
      tr0.innerHTML =
        "<td colspan='5' class='muted'>No CSV rows in this run window.</td>";
      body.appendChild(tr0);
    }
  }

  function esc(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function openRunAnalytics(name) {
    var panel = document.getElementById("run-analytics-panel");
    var title = document.getElementById("run-an-title");
    if (title) title.textContent = name || "Run";
    if (panel) {
      panel.hidden = false;
      panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
    setStat("run-an-attempts", "…");
    setStat("run-an-post-done", "…");
    setStat("run-an-rate", "…");
    setStat("run-an-fail", "…");
    fetch(
      "/api/analytics/run?name=" + encodeURIComponent(name || ""),
      { credentials: "same-origin" }
    )
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        data = data || {};
        setStat("run-an-attempts", String(data.attempts || 0));
        setStat("run-an-post-done", String(data.post_done || 0));
        setStat(
          "run-an-rate",
          (data.success_rate != null ? data.success_rate : 0) + "%"
        );
        setStat("run-an-fail", data.top_fail || "—");
        var meta = document.getElementById("run-an-meta");
        if (meta) {
          meta.textContent =
            (data.start || "?") +
            (data.end ? " → " + data.end : " → now") +
            (data.error ? " · " + data.error : "");
        }
        renderPie("run-chart-post", data.post_counts || {}, "post");
        renderBar("run-chart-login", data.login_counts || {}, true, "login");
        renderBar("run-chart-format", data.format_counts || {}, false, "format");
        fillRunTable(data.rows || []);
      })
      .catch(function () {
        setStat("run-an-attempts", "0");
        setStat("run-an-fail", "load failed");
      });
  }

  function closeRunAnalytics() {
    var panel = document.getElementById("run-analytics-panel");
    if (panel) panel.hidden = true;
    ["run-chart-post", "run-chart-login", "run-chart-format"].forEach(
      destroyChart
    );
  }

  function bindRunAnalytics() {
    var list = document.getElementById("log-history-list");
    if (list) {
      list.addEventListener("click", function (ev) {
        var chartsBtn = ev.target.closest("[data-run-charts]");
        if (chartsBtn) {
          ev.preventDefault();
          ev.stopPropagation();
          openRunAnalytics(chartsBtn.getAttribute("data-run-charts"));
          return;
        }
        var row = ev.target.closest("[data-run-name]");
        if (!row) return;
        if (ev.target.closest("a.link")) return; // download
        ev.preventDefault();
        openRunAnalytics(row.getAttribute("data-run-name"));
      });
    }
    var closeBtn = document.getElementById("run-an-close");
    if (closeBtn) {
      closeBtn.addEventListener("click", function () {
        closeRunAnalytics();
      });
    }
  }

  global.IGAnalytics = {
    bindHistoryAnalytics: bindHistoryAnalytics,
    bindRunAnalytics: bindRunAnalytics,
    openRunAnalytics: openRunAnalytics,
    closeRunAnalytics: closeRunAnalytics,
  };
})(window);

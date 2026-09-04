/* IG Console client: nav progress, busy forms, fleet screens */
(function (global) {
  "use strict";

  var overlay = null;
  var labelEl = null;
  var navBar = null;

  function ensureBusy() {
    if (!overlay) overlay = document.getElementById("busy-overlay");
    if (!labelEl) labelEl = document.getElementById("busy-label");
    return overlay;
  }

  function showBusy(msg) {
    var el = ensureBusy();
    if (!el) return;
    if (labelEl) labelEl.textContent = msg || "Working…";
    el.hidden = false;
    document.documentElement.classList.add("is-busy");
  }

  function hideBusy() {
    var el = ensureBusy();
    if (!el) return;
    el.hidden = true;
    document.documentElement.classList.remove("is-busy");
  }

  function navProgressStart() {
    navBar = navBar || document.getElementById("nav-progress");
    if (!navBar) return;
    navBar.classList.remove("is-done", "is-fade");
    navBar.classList.add("is-on");
    var span = navBar.querySelector("span");
    if (span) {
      span.style.width = "0%";
      // force reflow then grow
      void span.offsetWidth;
      span.style.width = "";
    }
  }

  function navProgressDone() {
    navBar = navBar || document.getElementById("nav-progress");
    if (!navBar) return;
    navBar.classList.add("is-done");
    window.setTimeout(function () {
      navBar.classList.add("is-fade");
      navBar.classList.remove("is-on", "is-done");
    }, 220);
  }

  function bindNavProgress() {
    document.addEventListener(
      "click",
      function (ev) {
        var a = ev.target && ev.target.closest ? ev.target.closest("a") : null;
        if (!a) return;
        if (a.getAttribute("target") === "_blank") return;
        if (a.getAttribute("download") != null) return;
        var href = a.getAttribute("href") || "";
        if (!href || href.charAt(0) === "#") return;
        if (a.getAttribute("aria-disabled") === "true") return;
        if (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return;
        // same-origin navigations
        try {
          var url = new URL(a.href, window.location.href);
          if (url.origin !== window.location.origin) return;
          if (
            url.pathname === window.location.pathname &&
            url.search === window.location.search
          ) {
            return;
          }
        } catch (e) {
          return;
        }
        navProgressStart();
      },
      true
    );

    window.addEventListener("pageshow", function () {
      hideBusy();
      navProgressDone();
    });

    if (document.readyState === "complete") {
      navProgressDone();
    } else {
      window.addEventListener("load", navProgressDone);
    }
  }

  function bindLogTail(url, deviceUrl) {
    global.IGConsole = global.IGConsole || {};
    var pane = document.getElementById("log-pane");
    var btn = document.getElementById("btn-refresh-log");
    var auto = document.getElementById("auto-log");
    var nameEl = document.getElementById("log-file-name");
    var pathEl = document.getElementById("log-file-path");
    var dl = document.getElementById("btn-download-log");
    var serialFilter = document.getElementById("log-serial-filter");
    if (!pane) return;

    var busy = false;
    var knownSerials = {};

    function esc(s) {
      return String(s || "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;");
    }

    function syncSerialOptions(serials) {
      if (!serialFilter) return;
      var cur = serialFilter.value || "";
      (serials || []).forEach(function (s) {
        if (s) knownSerials[s] = true;
      });
      var list = Object.keys(knownSerials).sort();
      var html = '<option value="">All phones (merged)</option>';
      list.forEach(function (s) {
        html += '<option value="' + esc(s) + '">' + esc(s.slice(-10)) + "</option>";
      });
      serialFilter.innerHTML = html;
      if (cur && knownSerials[cur]) serialFilter.value = cur;
    }

    window.IGConsole._logTailSync = syncSerialOptions;

    function refresh() {
      if (busy) return;
      busy = true;
      if (btn) btn.classList.add("is-spinning");
      var serial = serialFilter ? serialFilter.value : "";
      var p;
      if (serial && deviceUrl) {
        p = fetch(deviceUrl + "?serial=" + encodeURIComponent(serial) + "&lines=200", {
          credentials: "same-origin",
        })
          .then(function (r) {
            if (!r.ok) throw new Error("HTTP " + r.status);
            return r.json();
          })
          .then(function (data) {
            if (data && typeof data.text === "string") {
              pane.textContent = data.text;
              pane.scrollTop = pane.scrollHeight;
            }
            if (data && data.log_name && nameEl) nameEl.textContent = data.log_name;
            if (data && data.log_path && pathEl) {
              pathEl.textContent = data.log_path;
              pathEl.setAttribute("title", data.log_path);
            }
          });
      } else {
        p = fetch(url + "?lines=150", { credentials: "same-origin" })
          .then(function (r) {
            if (!r.ok) throw new Error("HTTP " + r.status);
            return r.json();
          })
          .then(function (data) {
            if (data && typeof data.text === "string") {
              pane.textContent = data.text;
              pane.scrollTop = pane.scrollHeight;
            }
            if (data && data.log_name && nameEl) {
              nameEl.textContent = data.log_name;
            }
            if (data && data.log_path && pathEl) {
              pathEl.textContent = data.log_path;
              pathEl.setAttribute("title", data.log_path);
            }
            if (data && data.log_name && dl) {
              dl.setAttribute(
                "href",
                "/run/log/download?name=" + encodeURIComponent(data.log_name)
              );
            }
            if (data && data.error_shot_dir) {
              var shotDirEl = document.getElementById("error-shot-dir");
              if (shotDirEl) shotDirEl.textContent = data.error_shot_dir;
            }
          });
      }
      p.catch(function () {}).finally(function () {
        busy = false;
        if (btn) btn.classList.remove("is-spinning");
      });
    }

    if (btn) btn.addEventListener("click", refresh);
    if (serialFilter) serialFilter.addEventListener("change", refresh);

    setInterval(function () {
      if (auto && auto.checked) refresh();
    }, 4000);
  }

  function bindFleetBoard(boardUrl) {
    var tbody = document.getElementById("fleet-board-tbody");
    var modeEl = document.getElementById("fleet-board-mode");
    var activeEl = document.getElementById("fleet-board-active");
    if (!tbody || !boardUrl) return;

    function esc(s) {
      return String(s || "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;");
    }

    function phaseClass(phase) {
      if (phase === "done") return "is-ok";
      if (phase === "problem") return "is-bad";
      if (phase === "running" || phase === "posting" || phase === "starting") return "is-live";
      return "";
    }

    function refresh() {
      fetch(boardUrl, { credentials: "same-origin" })
        .then(function (r) {
          return r.json();
        })
        .then(function (data) {
          if (!data) return;
          if (modeEl) {
            modeEl.textContent = data.run_mode ? "Mode " + data.run_mode : "Mode —";
          }
          if (activeEl) {
            activeEl.textContent = data.active ? "Farm RUNNING" : "Farm idle";
            activeEl.classList.toggle("is-live", !!data.active);
          }
          var phones = data.phones || [];
          if (window.IGConsole && window.IGConsole._logTailSync) {
            window.IGConsole._logTailSync(
              phones.map(function (p) {
                return p.serial;
              })
            );
          }
          if (!phones.length) {
            tbody.innerHTML =
              '<tr><td colspan="7" class="muted">No phone activity this run yet.</td></tr>';
            return;
          }
          var rows = "";
          phones.forEach(function (p) {
            rows +=
              '<tr class="' +
              phaseClass(p.phase) +
              '">' +
              '<td class="mono" title="' +
              esc(p.serial) +
              '">' +
              esc(p.serial_short || p.serial) +
              "</td>" +
              "<td>" +
              esc(p.username || "—") +
              "</td>" +
              '<td class="mono">' +
              esc(p.clone || "—") +
              "</td>" +
              '<td class="mono">' +
              esc(p.format || "feed") +
              "</td>" +
              "<td>" +
              esc(p.phase || "idle") +
              "</td>" +
              "<td>" +
              esc(p.login || "—") +
              "</td>" +
              "<td>" +
              esc(p.post || "—") +
              "</td>" +
              "</tr>";
          });
          tbody.innerHTML = rows;
        })
        .catch(function () {});
    }

    refresh();
    setInterval(refresh, 5000);
  }

  function bindErrorShots(listUrl, fileUrl) {
    var grid = document.getElementById("error-shot-grid");
    var empty = document.getElementById("error-shot-empty");
    var countEl = document.getElementById("error-shot-count");
    var btn = document.getElementById("btn-refresh-shots");
    var auto = document.getElementById("auto-shots");
    var dirEl = document.getElementById("error-shot-dir");
    if (!grid) return;

    var busy = false;
    function esc(s) {
      return String(s || "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
    }

    function refresh() {
      if (busy) return;
      busy = true;
      if (btn) btn.classList.add("is-spinning");
      var dir = (dirEl && dirEl.textContent && dirEl.textContent !== "none yet")
        ? dirEl.textContent.trim()
        : "";
      var q = listUrl + "?limit=36";
      if (dir) q += "&dir=" + encodeURIComponent(dir);
      fetch(q, { credentials: "same-origin" })
        .then(function (r) {
          if (!r.ok) throw new Error("HTTP " + r.status);
          return r.json();
        })
        .then(function (data) {
          if (data && data.dir && dirEl) {
            dirEl.textContent = data.dir;
          }
          var shots = (data && data.shots) || [];
          if (countEl) {
            countEl.textContent = shots.length + " shot" + (shots.length === 1 ? "" : "s");
          }
          var cards = [];
          shots.forEach(function (s) {
            var png = s.png_name || "";
            var xml = s.xml_name || "";
            var d = data.dir || "";
            var imgHref = png
              ? fileUrl + "?name=" + encodeURIComponent(png) + "&dir=" + encodeURIComponent(d)
              : "";
            var xmlHref = xml
              ? fileUrl + "?name=" + encodeURIComponent(xml) + "&dir=" + encodeURIComponent(d) + "&download=1"
              : "";
            var pngDl = png
              ? fileUrl + "?name=" + encodeURIComponent(png) + "&dir=" + encodeURIComponent(d) + "&download=1"
              : "";
            cards.push(
              '<article class="error-shot-card' + (s.ok === false ? " is-miss" : "") + '">' +
                (imgHref
                  ? '<a class="error-shot-thumb" href="' + esc(imgHref) + '" target="_blank" rel="noopener">' +
                      '<img src="' + esc(imgHref) + '" alt="' + esc(s.result || "fail") + '" loading="lazy">' +
                    "</a>"
                  : '<div class="error-shot-thumb is-empty">no png</div>') +
                '<div class="error-shot-meta">' +
                  '<div class="error-shot-result mono">' + esc(s.result || "?") + "</div>" +
                  '<div class="muted">' + esc(s.username || "-") + " · " + esc(s.serial || "-") + "</div>" +
                  '<div class="muted">' + esc(s.ts || "") + "</div>" +
                  (s.note ? '<div class="muted error-shot-note">' + esc(s.note) + "</div>" : "") +
                  '<div class="error-shot-actions">' +
                    (pngDl ? '<a class="link" href="' + esc(pngDl) + '">PNG</a>' : "") +
                    (xmlHref ? '<a class="link" href="' + esc(xmlHref) + '">XML</a>' : "") +
                  "</div>" +
                "</div>" +
              "</article>"
            );
          });
          if (cards.length) {
            if (empty) empty.style.display = "none";
            grid.innerHTML = cards.join("");
          } else {
            grid.innerHTML = '<p class="muted" id="error-shot-empty">No error shots for this run yet.</p>';
          }
        })
        .catch(function () {})
        .finally(function () {
          busy = false;
          if (btn) btn.classList.remove("is-spinning");
        });
    }

    if (btn) btn.addEventListener("click", refresh);
    refresh();
    setInterval(function () {
      if (auto && auto.checked) refresh();
    }, 8000);
  }

  function igSetPerPage(sel) {
    try {
      showBusy("Updating rows…");
      navProgressStart();
      var u = new URL(window.location.href);
      var n = String((sel && sel.value) || "25");
      if (["10", "25", "50", "100"].indexOf(n) < 0) n = "25";
      u.searchParams.set("per_page", n);
      // Reset page indexes only. Do NOT match "per_page" (it ends in _page).
      var keys = [];
      u.searchParams.forEach(function (_v, key) {
        if (key === "per_page") return;
        if (key === "page" || /_page$/.test(key)) keys.push(key);
      });
      keys.forEach(function (key) {
        u.searchParams.set(key, "1");
      });
      window.location.assign(u.toString());
    } catch (e) {
      hideBusy();
    }
  }

  function reloadLive() {
    try {
      showBusy("Refreshing…");
      navProgressStart();
      var btn = document.querySelector(".icon-btn");
      if (btn) btn.classList.add("is-spinning");
      var u = new URL(window.location.href);
      u.searchParams.set("_ts", String(Date.now()));
      window.location.assign(u.toString());
    } catch (e) {
      window.location.reload();
    }
  }

  function bindBusyForms() {
    document.addEventListener(
      "submit",
      function (ev) {
        var form = ev.target;
        if (!form || form.tagName !== "FORM") return;
        if (form.getAttribute("data-no-busy") === "1") return;
        var msg =
          (ev.submitter && ev.submitter.getAttribute("data-busy")) ||
          form.getAttribute("data-busy") ||
          "Working…";
        showBusy(msg);
        navProgressStart();
        var buttons = form.querySelectorAll("button, input[type=submit]");
        for (var i = 0; i < buttons.length; i++) {
          buttons[i].disabled = true;
          buttons[i].classList.add("is-busy");
        }
      },
      true
    );

    document.addEventListener(
      "click",
      function (ev) {
        var a = ev.target && ev.target.closest ? ev.target.closest("a.btn") : null;
        if (!a) return;
        if (a.getAttribute("aria-disabled") === "true") return;
        if (a.classList.contains("is-disabled")) return;
        var href = a.getAttribute("href") || "";
        if (!href || href.charAt(0) === "#") return;
        if (a.closest(".pager") || a.getAttribute("data-busy")) {
          showBusy(a.getAttribute("data-busy") || "Loading…");
          navProgressStart();
        }
      },
      true
    );
  }

  function fmtAge(ageS) {
    if (ageS == null) return "never";
    if (ageS < 60) return ageS + "s";
    if (ageS < 3600) return Math.round(ageS / 60) + "m";
    return Math.round(ageS / 3600) + "h";
  }

  function bindFleetScreens(opts) {
    opts = opts || {};
    var grid = document.getElementById("screens-grid");
    if (!grid) return;

    var statusEl = document.getElementById("screens-status");
    var progressWrap = document.getElementById("screens-progress");
    var progressFill = document.getElementById("screens-progress-fill");
    var progressLabel = document.getElementById("screens-progress-label");
    var wakeEl = document.getElementById("screens-wake");
    var filter = "all";
    var selected = {};
    var phones = [];
    var lastCounts = {};
    var es = null;
    var lightboxIndex = -1;
    var viewZoom = 1;
    var closeTimer = null;

    function clampZoom(z) {
      return Math.max(0.5, Math.min(3, Math.round(z * 100) / 100));
    }

    function applyZoom(z) {
      viewZoom = clampZoom(z);
      var inner = document.getElementById("viewer-zoom-inner");
      var space = document.getElementById("viewer-zoom-space");
      var phone = document.getElementById("viewer-phone");
      var label = document.getElementById("lightbox-zoom-label");
      if (label) label.textContent = Math.round(viewZoom * 100) + "%";
      if (!inner || !phone) return;
      inner.style.transform = "scale(" + viewZoom + ")";
      var w = phone.offsetWidth || 0;
      var h = phone.offsetHeight || 0;
      // Layout not ready yet — retry next frame (log open / phone switch)
      if ((!w || !h) && viewZoom === 1) {
        requestAnimationFrame(function () {
          applyZoom(viewZoom);
        });
        return;
      }
      if (space && w && h) {
        if (viewZoom <= 1.01) {
          space.style.width = "";
          space.style.height = "";
          space.style.minWidth = "100%";
          space.style.minHeight = "100%";
          inner.style.width = "";
          inner.style.height = "";
        } else {
          space.style.width = Math.max(w * viewZoom + 48, w) + "px";
          space.style.height = Math.max(h * viewZoom + 48, h) + "px";
          space.style.minWidth = "";
          space.style.minHeight = "";
          inner.style.width = w + "px";
          inner.style.height = h + "px";
        }
      }
    }

    function resetZoom() {
      applyZoom(1);
      var stage = document.getElementById("viewer-stage");
      if (stage) {
        stage.scrollTop = 0;
        stage.scrollLeft = 0;
      }
    }

    function settleViewerLayout() {
      requestAnimationFrame(function () {
        requestAnimationFrame(function () {
          resetZoom();
        });
      });
    }

    function wake() {
      return !!(wakeEl && wakeEl.checked);
    }

    function esc(s) {
      return String(s || "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
    }

    function shortSerial(serial) {
      var s = String(serial || "");
      if (s.length <= 8) return s;
      return "…" + s.slice(-6);
    }

    function bandOf(p) {
      if (p.error) return "error";
      return p.band || "missing";
    }

    function bandLabel(band) {
      if (band === "fresh") return "fresh";
      if (band === "stale") return "stale";
      if (band === "error") return "error";
      return "no shot";
    }

    function setJobUi(job) {
      job = job || {};
      var active = !!job.active;
      var total = job.total || 0;
      var done = job.done || 0;
      if (progressWrap) {
        progressWrap.hidden = !(active || (total && done < total && job.message));
      }
      if (progressFill && total) {
        progressFill.style.width = Math.min(100, Math.round((done / total) * 100)) + "%";
      }
      if (progressLabel) {
        progressLabel.textContent = job.message || (active ? done + " / " + total : "");
      }
      var stop = document.getElementById("btn-stop-updates");
      if (stop) stop.disabled = !active;
      ["btn-update-stale", "btn-update-selected", "btn-update-all"].forEach(function (id) {
        var b = document.getElementById(id);
        if (b) b.disabled = active && job.mode !== "one";
      });
    }

    function setText(id, text) {
      var el = document.getElementById(id);
      if (el) el.textContent = text;
    }

    function updateCounts(data) {
      var c = (data && data.counts) || lastCounts || {};
      lastCounts = c;
      var n = (data && data.phones ? data.phones.length : phones.length) || 0;
      setText("cnt-phones", String(n));
      setText("cnt-fresh", String(c.fresh || 0));
      setText("cnt-stale", String(c.stale || 0));
      setText("cnt-missing", String(c.missing || 0));
      var adb = document.getElementById("pill-adb");
      if (adb) {
        adb.textContent = data && data.adb_ok === false ? "ADB issue" : "ADB ok";
        adb.classList.toggle("is-bad", !!(data && data.adb_ok === false));
      }
      var farm = document.getElementById("pill-farm");
      if (farm) {
        farm.textContent = data && data.farm_active ? "Farm running" : "Farm idle";
      }
    }

    function statusLine(data, extra) {
      var c = (data && data.counts) || {};
      var n = ((data && data.phones) || phones || []).length;
      var parts = [
        n + " on shelf",
        (c.fresh || 0) + " fresh",
        (c.stale || 0) + " stale",
        (c.missing || 0) + " missing",
      ];
      if (data && data.error) parts.push(String(data.error));
      if (extra) parts.push(extra);
      if (statusEl) {
        statusEl.textContent = parts.join(" · ");
        statusEl.classList.toggle("is-bad", !!(data && data.error) || /fail|could not/i.test(extra || ""));
      }
      updateCounts(data || { counts: c, phones: phones });
    }

    function thumbUrl(p) {
      return "/api/screens/" + encodeURIComponent(p.serial) + "/thumb?v=" + (p.mtime || 0);
    }

    function fullUrl(p) {
      return "/api/screens/" + encodeURIComponent(p.serial) + "/full?v=" + (p.mtime || 0);
    }

    function matchesFilter(p) {
      if (filter === "needs") return p.band === "stale" || p.band === "missing";
      if (filter === "injob") {
        var ph = p.job_phase || "";
        return ph === "running" || ph === "posting" || ph === "starting" || ph === "queued";
      }
      if (filter === "problems") {
        if (p.error || p.band === "missing") return true;
        if (p.job_phase === "problem") return true;
        var bad = ["CAPTCHA", "PROXY_DEAD", "LOGIN_META_ERROR", "UNKNOWN_STUCK", "POST_TIMEOUT"];
        return bad.indexOf(p.job_login || "") >= 0 || bad.indexOf(p.job_post || "") >= 0;
      }
      return true;
    }

    function lazyLoad() {
      var imgs = grid.querySelectorAll("img[data-src]");
      imgs.forEach(function (img) {
        var src = img.getAttribute("data-src");
        if (!src) return;
        img.removeAttribute("data-src");
        img.onload = function () {
          img.classList.add("is-loaded");
        };
        img.onerror = function () {
          img.classList.add("is-loaded");
          img.style.opacity = "0.25";
          var glass = img.closest(".phone-glass");
          if (glass && !glass.querySelector(".placeholder")) {
            var ph = document.createElement("div");
            ph.className = "placeholder";
            ph.textContent = "Thumb failed";
            glass.appendChild(ph);
          }
        };
        img.src = src;
      });
    }

    function render() {
      var html = "";
      var shown = 0;
      phones.forEach(function (p, idx) {
        if (!matchesFilter(p)) return;
        shown += 1;
        var band = bandOf(p);
        var sel = selected[p.serial] ? " is-selected" : "";
        var cap = p.capturing ? " is-capturing" : "";
        html +=
          '<article class="phone-tile band-' +
          band +
          sel +
          cap +
          '" data-serial="' +
          esc(p.serial) +
          '" data-idx="' +
          idx +
          '" title="' +
          esc(p.serial) +
          '">' +
          '<div class="phone-device">' +
          '<input type="checkbox" class="phone-select" data-select="' +
          esc(p.serial) +
          '" ' +
          (selected[p.serial] ? "checked " : "") +
          'title="Select for Update selected">' +
          '<span class="phone-band">' +
          bandLabel(band) +
          "</span>" +
          (p.job_post === "POST_DONE"
            ? '<span class="phone-job is-ok">DONE</span>'
            : p.job_phase === "running" || p.job_phase === "posting"
              ? '<span class="phone-job is-live">LIVE</span>'
              : p.job_phase === "problem"
                ? '<span class="phone-job is-bad">' + esc(p.job_login || "FAIL") + "</span>"
                : "") +
          '<div class="phone-notch" aria-hidden="true"></div>' +
          '<div class="phone-glass" data-shot="' +
          esc(p.serial) +
          '" title="Click to enlarge">' +
          (p.has_thumb
            ? '<img alt="" data-src="' + thumbUrl(p) + '" loading="lazy">'
            : '<div class="placeholder"><span>No shot yet</span><span>Tap Refresh</span></div>') +
          "</div>" +
          '<div class="phone-home" aria-hidden="true"></div>' +
          "</div>" +
          '<div class="phone-meta">' +
          '<div class="label-row">' +
          '<span class="short">' +
          esc(shortSerial(p.serial)) +
          "</span>" +
          '<span class="age">' +
          fmtAge(p.age_s) +
          "</span></div>" +
          '<div class="serial mono" title="' +
          esc(p.serial) +
          '">' +
          esc(p.serial) +
          "</div>" +
          '<div class="row">' +
          '<button type="button" class="btn phone-refresh" data-refresh="' +
          esc(p.serial) +
          '">Refresh</button></div>' +
          (p.error ? '<div class="err">' + esc(String(p.error).slice(0, 80)) + "</div>" : "") +
          "</div></article>";
      });
      if (!shown) {
        grid.innerHTML =
          '<div class="screens-empty"><strong>No phones in this filter</strong>' +
          (phones.length
            ? "Try All, or Rescan ADB if devices are missing."
            : "Rescan ADB, or check USB / adb devices on the farm PC.") +
          "</div>";
        return;
      }
      grid.innerHTML = html;
      lazyLoad();
    }

    function applyPhoneUpdate(result) {
      if (!result || !result.serial) return;
      for (var i = 0; i < phones.length; i++) {
        if (phones[i].serial === result.serial) {
          phones[i].capturing = false;
          if (result.ok) {
            phones[i].has_thumb = true;
            phones[i].has_full = true;
            phones[i].mtime = result.mtime || Date.now() / 1000;
            phones[i].captured_at = result.captured_at || phones[i].captured_at;
            phones[i].age_s = 0;
            phones[i].band = "fresh";
            phones[i].error = "";
          } else {
            phones[i].error = result.error || "capture failed";
          }
          break;
        }
      }
      render();
      var box = document.getElementById("screens-lightbox");
      if (box && !box.hidden) {
        var visible = phones.filter(matchesFilter);
        var cur = visible[lightboxIndex];
        if (cur && result.serial === cur.serial && result.ok) {
          openLightbox(lightboxIndex);
        }
      }
    }

    function loadSnapshot() {
      return fetch("/api/screens", { credentials: "same-origin" })
        .then(function (r) {
          if (!r.ok) throw new Error("HTTP " + r.status);
          return r.json();
        })
        .then(function (data) {
          phones = data.phones || [];
          statusLine(data);
          setJobUi(data.job || {});
          render();
        })
        .catch(function (err) {
          if (statusEl) {
            statusEl.textContent =
              "Could not load phone list" +
              (err && err.message ? " (" + err.message + ")" : "") +
              ". Retrying…";
            statusEl.classList.add("is-bad");
          }
          // Keep any phones already on shelf; do not wipe grid.
        });
    }

    function postJob(body) {
      return fetch("/api/screens/jobs", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }).then(function (r) {
        return r.json();
      });
    }

    function connectEvents() {
      if (es) {
        try {
          es.close();
        } catch (e) {}
      }
      if (!("EventSource" in window)) return;
      es = new EventSource("/api/screens/events");
      function onMsg(ev) {
        var data = {};
        try {
          data = JSON.parse(ev.data || "{}");
        } catch (e) {}
        if (ev.type === "snapshot") {
          phones = data.phones || phones;
          statusLine(data);
          setJobUi(data.job || {});
          render();
          return;
        }
        if (ev.type === "phone.started") {
          phones.forEach(function (p) {
            if (p.serial === data.serial) p.capturing = true;
          });
          render();
        }
        if (ev.type === "phone.done" || ev.type === "phone.fail") {
          applyPhoneUpdate(data);
        }
        if (
          ev.type === "job.started" ||
          ev.type === "job.progress" ||
          ev.type === "job.done" ||
          ev.type === "job.paused" ||
          ev.type === "job.cancelled"
        ) {
          setJobUi(data);
          if (ev.type === "job.done" || ev.type === "job.paused") {
            loadSnapshot();
          }
        }
      }
      [
        "snapshot",
        "phone.started",
        "phone.done",
        "phone.fail",
        "job.started",
        "job.progress",
        "job.done",
        "job.paused",
        "job.cancelled",
      ].forEach(function (t) {
        es.addEventListener(t, onMsg);
      });
    }

    function openLightbox(idx) {
      var visible = phones.filter(matchesFilter);
      if (!visible.length) return;
      lightboxIndex = Math.max(0, Math.min(idx, visible.length - 1));
      var p = visible[lightboxIndex];
      var box = document.getElementById("screens-lightbox");
      var img = document.getElementById("lightbox-img");
      var empty = document.getElementById("lightbox-empty");
      var spinner = document.getElementById("lightbox-spinner");
      if (!box || !img) return;

      var band = bandOf(p);
      var title = document.getElementById("viewer-title");
      var shortEl = document.getElementById("lightbox-short");
      var serialEl = document.getElementById("lightbox-serial");
      var ageEl = document.getElementById("lightbox-age");
      var posEl = document.getElementById("lightbox-pos");
      var badge = document.getElementById("lightbox-badge");
      var errWrap = document.getElementById("lightbox-err-wrap");
      var errEl = document.getElementById("lightbox-err");
      var openFull = document.getElementById("lightbox-open-full");
      var prevBtn = document.getElementById("lightbox-prev");
      var nextBtn = document.getElementById("lightbox-next");

      if (title) title.textContent = "Phone " + shortSerial(p.serial);
      if (shortEl) shortEl.textContent = shortSerial(p.serial);
      if (serialEl) serialEl.textContent = p.serial;
      if (ageEl) ageEl.textContent = fmtAge(p.age_s);
      if (posEl) posEl.textContent = (lightboxIndex + 1) + " / " + visible.length;
      if (badge) {
        badge.textContent = bandLabel(band);
        badge.className = "viewer-badge is-" + band;
      }
      if (errWrap && errEl) {
        if (p.error) {
          errWrap.hidden = false;
          errEl.textContent = String(p.error);
        } else {
          errWrap.hidden = true;
          errEl.textContent = "";
        }
      }
      if (prevBtn) prevBtn.disabled = lightboxIndex <= 0;
      if (nextBtn) nextBtn.disabled = lightboxIndex >= visible.length - 1;

      var hasShot = !!(p.has_full || p.has_thumb);
      var loadToken = String(p.serial) + ":" + String(p.mtime || 0);
      img.setAttribute("data-load-token", loadToken);

      // Clear stale frame immediately so switching phones never shows the wrong shot
      img.style.opacity = "0";
      img.classList.add("is-hidden");

      if (empty) {
        empty.innerHTML =
          "<strong>No screenshot yet</strong><span>Capture this phone to fill the screen.</span>";
        if (hasShot) {
          empty.hidden = true;
          empty.classList.add("is-hidden");
        } else {
          empty.hidden = false;
          empty.classList.remove("is-hidden");
        }
      }
      if (openFull) {
        if (hasShot) {
          openFull.href = fullUrl(p);
          openFull.removeAttribute("aria-disabled");
          openFull.classList.remove("is-disabled");
        } else {
          openFull.href = "#";
          openFull.setAttribute("aria-disabled", "true");
          openFull.classList.add("is-disabled");
        }
      }

      if (hasShot) {
        var src = fullUrl(p);
        if (spinner) spinner.hidden = false;
        function showImg() {
          if (img.getAttribute("data-load-token") !== loadToken) return;
          img.style.opacity = "1";
          img.hidden = false;
          img.classList.remove("is-hidden");
          if (spinner) spinner.hidden = true;
          if (empty) {
            empty.hidden = true;
            empty.classList.add("is-hidden");
          }
          settleViewerLayout();
        }
        img.onload = showImg;
        img.onerror = function () {
          if (img.getAttribute("data-load-token") !== loadToken) return;
          if (spinner) spinner.hidden = true;
          img.classList.add("is-hidden");
          if (empty) {
            empty.hidden = false;
            empty.classList.remove("is-hidden");
            empty.innerHTML =
              "<strong>Image failed</strong><span>Try Refresh screen.</span>";
          }
        };
        img.hidden = false;
        img.src = src + (src.indexOf("?") >= 0 ? "&" : "?") + "_=" + Date.now();
        img.alt = "Screenshot " + p.serial;
        if (img.complete && img.naturalWidth > 0) {
          showImg();
        }
      } else {
        if (spinner) spinner.hidden = true;
        img.removeAttribute("src");
        img.hidden = true;
        img.classList.add("is-hidden");
        img.style.opacity = "1";
      }

      // Keep log panel open across phone switches (stable layout); reload for new serial
      var logPanel = document.getElementById("viewer-log-panel");
      var logWasOpen = !!(logPanel && !logPanel.hidden);
      var copyBtn = document.getElementById("lightbox-copy-serial");
      if (copyBtn) {
        copyBtn.textContent = "Copy serial";
        copyBtn.classList.remove("is-copied");
      }

      var wasOpen = box.classList.contains("is-open");
      box.hidden = false;
      if (closeTimer) {
        clearTimeout(closeTimer);
        closeTimer = null;
      }
      if (!wasOpen) {
        box.classList.remove("is-open");
        requestAnimationFrame(function () {
          requestAnimationFrame(function () {
            box.classList.add("is-open");
            settleViewerLayout();
            if (logWasOpen) loadDeviceLog();
          });
        });
      } else {
        box.classList.add("is-open");
        settleViewerLayout();
        if (logWasOpen) loadDeviceLog();
      }
      document.documentElement.classList.add("is-lightbox");
    }

    function closeLightbox() {
      var box = document.getElementById("screens-lightbox");
      if (!box) return;
      box.classList.remove("is-open");
      document.documentElement.classList.remove("is-lightbox");
      setLogPanelOpen(false);
      if (closeTimer) clearTimeout(closeTimer);
      closeTimer = setTimeout(function () {
        box.hidden = true;
        resetZoom();
        closeTimer = null;
      }, 280);
    }

    function currentLightboxSerial() {
      var visible = phones.filter(matchesFilter);
      var p = visible[lightboxIndex];
      return p ? p.serial : "";
    }

    grid.addEventListener("click", function (ev) {
      var refresh = ev.target.closest("[data-refresh]");
      if (refresh) {
        ev.preventDefault();
        ev.stopPropagation();
        postJob({
          mode: "one",
          serials: [refresh.getAttribute("data-refresh")],
          wake: wake(),
        });
        return;
      }
      var sel = ev.target.closest("[data-select]");
      if (sel) {
        ev.stopPropagation();
        var sid = sel.getAttribute("data-select");
        selected[sid] = !!sel.checked;
        var tileSel = sel.closest(".phone-tile");
        if (tileSel) tileSel.classList.toggle("is-selected", !!sel.checked);
        return;
      }
      var shot = ev.target.closest("[data-shot]");
      if (shot) {
        var serial = shot.getAttribute("data-shot");
        var visible = phones.filter(matchesFilter);
        var vi = visible.findIndex(function (p) {
          return p.serial === serial;
        });
        if (vi < 0) return;
        openLightbox(vi);
        return;
      }
      var tile = ev.target.closest(".phone-tile");
      if (!tile) return;
      var serial2 = tile.getAttribute("data-serial");
      selected[serial2] = !selected[serial2];
      render();
    });

    document.querySelectorAll(".chip-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        document.querySelectorAll(".chip-btn").forEach(function (b) {
          b.classList.remove("is-on");
        });
        btn.classList.add("is-on");
        filter = btn.getAttribute("data-filter") || "all";
        render();
      });
    });

    function selectedSerials() {
      return Object.keys(selected).filter(function (k) {
        return selected[k];
      });
    }

    var btnStale = document.getElementById("btn-update-stale");
    if (btnStale) {
      btnStale.addEventListener("click", function () {
        postJob({ mode: "stale", wake: wake() }).then(function (r) {
          if (!r.ok && statusEl) {
            statusEl.textContent = r.error || "Could not start job";
            statusEl.classList.add("is-bad");
          }
        });
      });
    }
    var btnSel = document.getElementById("btn-update-selected");
    if (btnSel) {
      btnSel.addEventListener("click", function () {
        var s = selectedSerials();
        if (!s.length) {
          if (statusEl) {
            statusEl.textContent = "Select phones first (checkbox on each device).";
            statusEl.classList.add("is-bad");
          }
          return;
        }
        postJob({ mode: "selected", serials: s, wake: wake() });
      });
    }
    var btnAll = document.getElementById("btn-update-all");
    if (btnAll) {
      btnAll.addEventListener("click", function () {
        if (phones.length > 10) {
          if (
            !window.confirm(
              "Update all " + phones.length + " phones? This uses ADB for several minutes."
            )
          ) {
            return;
          }
        }
        postJob({ mode: "all", wake: wake() });
      });
    }
    var btnStop = document.getElementById("btn-stop-updates");
    if (btnStop) {
      btnStop.addEventListener("click", function () {
        fetch("/api/screens/jobs/cancel", {
          method: "POST",
          credentials: "same-origin",
        });
      });
    }
    var btnRescan = document.getElementById("btn-rescan");
    if (btnRescan) {
      btnRescan.addEventListener("click", function () {
        fetch("/api/screens/rescan", { method: "POST", credentials: "same-origin" })
          .then(function (r) {
            return r.json();
          })
          .then(function (data) {
            phones = data.phones || [];
            statusLine(data);
            render();
          })
          .catch(function () {
            if (statusEl) {
              statusEl.textContent = "Rescan failed.";
              statusEl.classList.add("is-bad");
            }
          });
      });
    }

    var lbClose = document.getElementById("lightbox-close");
    if (lbClose) lbClose.addEventListener("click", closeLightbox);
    var lbPrev = document.getElementById("lightbox-prev");
    if (lbPrev) {
      lbPrev.addEventListener("click", function (ev) {
        ev.stopPropagation();
        openLightbox(lightboxIndex - 1);
      });
    }
    var lbNext = document.getElementById("lightbox-next");
    if (lbNext) {
      lbNext.addEventListener("click", function (ev) {
        ev.stopPropagation();
        openLightbox(lightboxIndex + 1);
      });
    }
    var lbRefresh = document.getElementById("lightbox-refresh");
    if (lbRefresh) {
      lbRefresh.addEventListener("click", function (ev) {
        ev.stopPropagation();
        var s = currentLightboxSerial();
        if (s) postJob({ mode: "one", serials: [s], wake: wake() });
      });
    }
    function setLogPanelOpen(open) {
      var panel = document.getElementById("viewer-log-panel");
      var shell = document.getElementById("viewer-shell");
      var btn = document.getElementById("lightbox-device-log");
      if (panel) panel.hidden = !open;
      if (shell) shell.classList.toggle("is-log-open", !!open);
      if (btn) btn.classList.toggle("is-active", !!open);
      settleViewerLayout();
    }

    function loadDeviceLog() {
      var s = currentLightboxSerial();
      var pane = document.getElementById("lightbox-log-pane");
      var meta = document.getElementById("lightbox-log-meta");
      if (!s || !pane) return;
      setLogPanelOpen(true);
      pane.textContent = "Loading log for " + s + "…";
      if (meta) meta.textContent = "";
      fetch(
        "/api/log/device?serial=" + encodeURIComponent(s) + "&lines=300",
        { credentials: "same-origin" }
      )
        .then(function (r) {
          return r.json();
        })
        .then(function (data) {
          if (meta) {
            meta.textContent = data.log_name
              ? data.log_name + " · " + (data.match_count || 0) + " lines"
              : data.error || "";
          }
          pane.textContent =
            (data && data.text) || data.error || "(no log for this phone)";
          pane.scrollTop = pane.scrollHeight;
        })
        .catch(function () {
          pane.textContent = "(failed to load device log)";
        });
    }
    var lbDevLog = document.getElementById("lightbox-device-log");
    if (lbDevLog) {
      lbDevLog.addEventListener("click", function (ev) {
        ev.stopPropagation();
        var panel = document.getElementById("viewer-log-panel");
        if (panel && !panel.hidden) {
          setLogPanelOpen(false);
          return;
        }
        loadDeviceLog();
      });
    }
    var lbLogReload = document.getElementById("lightbox-log-reload");
    if (lbLogReload) {
      lbLogReload.addEventListener("click", function (ev) {
        ev.stopPropagation();
        loadDeviceLog();
      });
    }
    var lbLogClose = document.getElementById("lightbox-log-close");
    if (lbLogClose) {
      lbLogClose.addEventListener("click", function (ev) {
        ev.stopPropagation();
        setLogPanelOpen(false);
      });
    }
    var copySerialBtn = document.getElementById("lightbox-copy-serial");
    if (copySerialBtn) {
      copySerialBtn.addEventListener("click", function (ev) {
        ev.stopPropagation();
        var s = currentLightboxSerial();
        if (!s) return;
        function ok() {
          copySerialBtn.textContent = "Copied";
          copySerialBtn.classList.add("is-copied");
          setTimeout(function () {
            copySerialBtn.textContent = "Copy serial";
            copySerialBtn.classList.remove("is-copied");
          }, 1200);
        }
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(s).then(ok).catch(function () {
            window.prompt("Copy serial", s);
          });
        } else {
          window.prompt("Copy serial", s);
        }
      });
    }
    var openFullBtn = document.getElementById("lightbox-open-full");
    if (openFullBtn) {
      openFullBtn.addEventListener("click", function (ev) {
        if (openFullBtn.getAttribute("aria-disabled") === "true") {
          ev.preventDefault();
        }
      });
    }
    var zIn = document.getElementById("lightbox-zoom-in");
    if (zIn) {
      zIn.addEventListener("click", function (ev) {
        ev.stopPropagation();
        applyZoom(viewZoom + 0.25);
      });
    }
    var zOut = document.getElementById("lightbox-zoom-out");
    if (zOut) {
      zOut.addEventListener("click", function (ev) {
        ev.stopPropagation();
        applyZoom(viewZoom - 0.25);
      });
    }
    var zReset = document.getElementById("lightbox-zoom-reset");
    if (zReset) {
      zReset.addEventListener("click", function (ev) {
        ev.stopPropagation();
        resetZoom();
      });
    }
    var stageEl = document.getElementById("viewer-stage");
    if (stageEl) {
      stageEl.addEventListener(
        "wheel",
        function (ev) {
          if (!(ev.ctrlKey || ev.metaKey)) return;
          ev.preventDefault();
          var delta = ev.deltaY > 0 ? -0.1 : 0.1;
          applyZoom(viewZoom + delta);
        },
        { passive: false }
      );
    }
    var shell = document.getElementById("viewer-shell");
    if (shell) {
      shell.addEventListener("click", function (ev) {
        ev.stopPropagation();
      });
    }
    var boxEl = document.getElementById("screens-lightbox");
    if (boxEl) {
      boxEl.addEventListener("click", function (ev) {
        if (ev.target === boxEl) closeLightbox();
      });
    }
    document.addEventListener("keydown", function (ev) {
      var lb = document.getElementById("screens-lightbox");
      if (!lb || lb.hidden || !lb.classList.contains("is-open")) return;
      if (ev.key === "Escape") closeLightbox();
      if (ev.key === "ArrowLeft") openLightbox(lightboxIndex - 1);
      if (ev.key === "ArrowRight") openLightbox(lightboxIndex + 1);
      if (ev.key === "+" || ev.key === "=") applyZoom(viewZoom + 0.25);
      if (ev.key === "-" || ev.key === "_") applyZoom(viewZoom - 0.25);
      if (ev.key === "0") resetZoom();
    });

    loadSnapshot();
    connectEvents();
    // Soft retry once if first paint was empty (ADB hiccup)
    setTimeout(function () {
      if (!phones.length) loadSnapshot();
    }, 2500);
  }


  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      bindBusyForms();
      bindNavProgress();
    });
  } else {
    bindBusyForms();
    bindNavProgress();
  }

  function bindCloneMap(url) {
    var modal = document.getElementById("clone-map-modal");
    var body = document.getElementById("clone-map-body");
    var btn = document.getElementById("btn-clone-map");
    var btn2 = document.getElementById("btn-clone-map-inline");
    var close = document.getElementById("clone-map-close");
    var refresh = document.getElementById("clone-map-refresh");
    if (!modal || !body || !url) return;

    function esc(s) {
      return String(s || "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;");
    }

    function openModal() {
      modal.hidden = false;
      requestAnimationFrame(function () {
        modal.classList.add("is-open");
      });
      load(true);
    }

    function closeModal() {
      modal.classList.remove("is-open");
      window.setTimeout(function () {
        modal.hidden = true;
      }, 280);
    }

    function load(fresh) {
      body.innerHTML = "<p class='muted'>Reading phones + downloaded_ig…</p>";
      var q = url + (url.indexOf("?") >= 0 ? "&" : "?") + (fresh ? "fresh=1" : "");
      fetch(q, { credentials: "same-origin" })
        .then(function (r) { return r.json(); })
        .then(render)
        .catch(function (e) {
          body.innerHTML = "<p class='muted'>Could not load clone map: " + esc(e) + "</p>";
        });
    }

    function render(data) {
      if (!data || data.ok === false) {
        body.innerHTML = "<p class='muted'>" + esc((data && data.error) || "No clone data") + "</p>";
        return;
      }
      var spreadClass = data.spread_ok ? "is-ok" : "is-warn";
      var even = data.even_each;
      var stats =
        "<div class='clone-map-stats'>" +
        chip(data.phones, "Phones") +
        chip(data.ready_phones, "Ready phones") +
        chip(data.unique_on_phones, "Unique on phones") +
        chip(data.open_ok_on_phones, "OPEN_OK (farm)", data.allowlist_on ? "is-ok" : "") +
        chip(data.ignored_on_phones, "Ignored", data.ignored_on_phones ? "is-warn" : "") +
        chip((data.unique_lo || 0) + "–" + (data.unique_hi || 0), "Spread now", spreadClass) +
        chip(data.apk_on_disk, "APKs on disk") +
        chip(even, "Even target / phone", "is-ok") +
        "</div>";
      var note =
        "<p class='clone-map-note'>" +
        (data.allowlist_on
          ? "Farm posts only <b>OPEN_OK</b> clones. Grey suffixes are ignored (not uninstalled). "
          : "") +
        "Even install: unused APKs in <code>nomix_api\\downloaded_ig</code> " +
        "go to the phone with the fewest unique clones. Cap " +
        esc(data.cap) +
        ". One APK → one phone. " +
        (data.apk_gb ? esc(data.apk_gb) + " GB on disk. " : "") +
        "After even pass: " +
        esc(data.target_lo) + "–" + esc(data.target_hi) +
        " unique/phone. Adds queued: " +
        esc(data.adds_total) +
        ".</p>";
      var cards = (data.devices || [])
        .map(function (d) {
          var cls = "clone-card";
          if (d.error) cls += " is-low";
          else if (d.need > 0) cls += " is-low";
          else if (d.unique_n > (data.target_hi || d.unique_n)) cls += " is-fat";
          else cls += " is-ok";
          var okSet = {};
          (d.open_ok || []).forEach(function (s) { okSet[s] = true; });
          var ignSet = {};
          (d.ignored || []).forEach(function (s) { ignSet[s] = true; });
          var chips = (d.unique || []).map(function (s) {
            var k = "clone-suf";
            if (data.allowlist_on && ignSet[s]) k += " is-skip";
            else if (!data.allowlist_on || okSet[s]) k += " is-ok";
            return "<span class='" + k + "'>" + esc(s) + "</span>";
          }).join(" ");
          return (
            "<article class='" + cls + "'>" +
            "<div class='clone-card-head'><span class='mono'>" +
            esc(d.short) +
            "</span><span class='clone-card-n'>" +
            esc(data.allowlist_on ? (d.open_ok_n + "/" + d.unique_n) : d.unique_n) +
            "<small> / " +
            esc(d.target) +
            "</small></span></div>" +
            "<div class='clone-bar' title='now " +
            esc(d.unique_n) +
            " · even target " +
            esc(d.target) +
            "'>" +
            "<div class='clone-bar-target' style='width:" +
            esc(d.target_pct) +
            "%'></div>" +
            "<div class='clone-bar-fill' style='width:" +
            esc(d.pct) +
            "%'></div>" +
            "</div>" +
            "<div class='clone-suffixes'>" +
            (chips || "—") +
            (d.need ? " · need +" + esc(d.need) : "") +
            "</div></article>"
          );
        })
        .join("");
      if (!cards) cards = "<p class='muted'>No ADB phones. Plug them in on the Windows host.</p>";
      else cards = "<div class='clone-map-grid'>" + cards + "</div>";
      body.innerHTML = stats + note + cards;
    }

    function chip(v, label, extra) {
      return (
        "<div class='clone-map-stat " +
        (extra || "") +
        "'><b>" +
        esc(v) +
        "</b><span>" +
        esc(label) +
        "</span></div>"
      );
    }

    if (btn) btn.addEventListener("click", openModal);
    if (btn2) btn2.addEventListener("click", openModal);
    if (close) close.addEventListener("click", closeModal);
    if (refresh) refresh.addEventListener("click", function () { load(true); });
    modal.addEventListener("click", function (ev) {
      if (ev.target === modal) closeModal();
    });
    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape" && modal.classList.contains("is-open")) closeModal();
    });
  }

  function bindPhoneLogs(url) {
    var grid = document.getElementById("phone-log-grid");
    var countEl = document.getElementById("phone-log-count");
    var dirEl = document.getElementById("phone-log-dir");
    if (!grid || !url) return;

    var focus = "";

    function esc(s) {
      return String(s || "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;");
    }

    function render(data) {
      var phones = (data && data.phones) || [];
      if (countEl) {
        countEl.textContent = phones.length ? phones.length + " phone log(s)" : "0 phones";
      }
      if (dirEl) dirEl.textContent = (data && data.dir) || "";
      if (!phones.length) {
        grid.innerHTML =
          "<p class='muted' id='phone-log-empty'>Start posting or Run due now — panes appear as each phone process starts.</p>";
        return;
      }
      var html = "";
      phones.forEach(function (p) {
        var cls = "phone-log-card" + (focus && focus === p.serial ? " is-focus" : "");
        html +=
          "<article class='" +
          cls +
          "' data-serial='" +
          esc(p.serial) +
          "'>" +
          "<header title='Click to enlarge'><span class='mono'>" +
          esc(p.serial_short || p.serial) +
          "</span><span class='muted'>" +
          esc(p.lines) +
          " lines</span></header>" +
          "<pre>" +
          esc(p.text || "") +
          "</pre></article>";
      });
      grid.innerHTML = html;
      grid.querySelectorAll(".phone-log-card header").forEach(function (h) {
        h.addEventListener("click", function () {
          var card = h.closest(".phone-log-card");
          var s = card && card.getAttribute("data-serial");
          focus = focus === s ? "" : s || "";
          refresh();
        });
      });
      grid.querySelectorAll(".phone-log-card pre").forEach(function (pre) {
        pre.scrollTop = pre.scrollHeight;
      });
    }

    function refresh() {
      fetch(url + "?lines=90", { credentials: "same-origin" })
        .then(function (r) {
          return r.json();
        })
        .then(render)
        .catch(function () {});
    }

    refresh();
    setInterval(refresh, 3000);
  }

  global.IGConsole = global.IGConsole || {};
  global.IGConsole.bindCloneMap = bindCloneMap;
  global.IGConsole.bindLogTail = bindLogTail;
  global.IGConsole.bindFleetBoard = bindFleetBoard;
  global.IGConsole.bindErrorShots = bindErrorShots;
  global.IGConsole.bindPhoneLogs = bindPhoneLogs;
  global.IGConsole.setPerPage = igSetPerPage;
  global.IGConsole.reloadLive = reloadLive;
  global.IGConsole.showBusy = showBusy;
  global.IGConsole.hideBusy = hideBusy;
  global.IGConsole.bindFleetScreens = bindFleetScreens;
  global.IGConsole.navProgressStart = navProgressStart;
  global.IGConsole.navProgressDone = navProgressDone;
})(window);

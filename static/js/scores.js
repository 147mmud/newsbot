/* Live Scores header widget - vanilla JS, ~3 KB, no dependencies.
 * Data: ESPN public scoreboard JSON (free, no key). Falls back to the
 * data/scores.json snapshot that the GitHub Action writes every run.
 */
(function () {
  "use strict";
  var cfg = window.NB || {};
  var track = document.getElementById("sb-track");
  var bar = document.getElementById("scorebar");
  if (!track || !bar) return;

  var tabs = bar.querySelectorAll(".sb-tabs button");
  var current = "rugby";
  var timer = null;
  var snapshot = null;
  try { current = localStorage.getItem("nb-sport") || current; } catch (e) {}

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function side(c) {
    var t = c.team || {};
    var s = c.score;
    if (s && typeof s === "object") s = s.displayValue;
    return {
      name: c.displayName || t.displayName || t.name || "",
      abbr: c.abbreviation || t.abbreviation || "",
      score: s == null ? "" : String(s),
      logo: c.logo || t.logo || "",
      home: c.homeAway === "home"
    };
  }

  /* Mirrors bot/scores.py normalize() - handles both ESPN response shapes */
  function normalize(data) {
    var out = [];
    (data.sports || []).forEach(function (sp) {
      (sp.leagues || []).forEach(function (lg) {
        var lname = lg.shortName || lg.abbreviation || lg.name || "";
        (lg.events || []).forEach(function (ev) {
          var comps = (ev.competitors || []).map(side);
          if (comps.length < 2) return;
          var st = (ev.fullStatus && ev.fullStatus.type) || {};
          out.push({
            league: lname,
            state: typeof ev.status === "string" ? ev.status : (st.state || "pre"),
            detail: ev.summary || st.shortDetail || "",
            date: ev.date || "", link: ev.link || "", teams: comps.slice(0, 2)
          });
        });
      });
    });
    var lname2 = ((data.leagues || [])[0] || {}).abbreviation || "";
    (data.events || []).forEach(function (ev) {
      var comp = (ev.competitions || [])[0] || {};
      var comps = (comp.competitors || []).map(side);
      if (comps.length < 2) return;
      var st = (ev.status && ev.status.type) || {};
      out.push({
        league: lname2, state: st.state || "pre", detail: st.shortDetail || "",
        date: ev.date || "", link: ((ev.links || [])[0] || {}).href || "", teams: comps.slice(0, 2)
      });
    });
    var order = { "in": 0, pre: 1, post: 2 };
    function rank(s) { return s in order ? order[s] : 3; }
    out.sort(function (a, b) {
      var d = rank(a.state) - rank(b.state);
      return d || (a.state === "post" ? 0 : (a.date > b.date ? 1 : -1));
    });
    return out.slice(0, 25);
  }

  function timeLabel(e) {
    if (e.state !== "pre" || !e.date) return e.detail || "";
    var d = new Date(e.date);
    if (isNaN(d)) return e.detail || "";
    var today = new Date().toDateString() === d.toDateString();
    return (today ? "" : d.toLocaleDateString([], { weekday: "short" }) + " ") +
      d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  }

  var PALETTE = ["#dc2626", "#2563eb", "#16a34a", "#d97706", "#7c3aed", "#0891b2", "#db2777", "#4b5563"];
  function teamColor(name) {
    var h = 0; name = String(name || "");
    for (var i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) | 0;
    return PALETTE[Math.abs(h) % PALETTE.length];
  }

  function render(events) {
    if (!events || !events.length) {
      track.innerHTML = '<span class="sb-msg">No ' + esc(current === "nfl" ? "NFL" : current) +
        " matches right now – check back soon.</span>";
      return;
    }
    track.innerHTML = events.map(function (e) {
      var a = e.teams[0], b = e.teams[1];
      if (a.home && !b.home) { var t = a; a = b; b = t; }
      var post = e.state === "post", sa = parseFloat(a.score), sb = parseFloat(b.score);
      var showScore = e.state !== "pre";
      function row(x, win, lose) {
        var label = x.abbr || x.name || "";
        var badge = x.logo
          ? '<span class="sb-badge"><img src="' + esc(x.logo) + '" alt="" loading="lazy" width="16" height="16"></span>'
          : '<span class="sb-badge" style="background:' + teamColor(x.name || label) + '">' + esc(label.charAt(0)) + "</span>";
        return '<div class="sb-team' + (post && win ? " win" : "") + (post && lose ? " lose" : "") + '">' + badge +
          '<span class="t">' + esc(label) + '</span><span class="s">' + (showScore ? esc(x.score) : "–") + "</span></div>";
      }
      var state = e.state === "in" ? "live" : (post ? "post" : "pre");
      var chip = e.state === "in" ? (e.detail || "LIVE") : (post ? (e.detail && e.detail.length <= 6 ? e.detail : "FT") : timeLabel(e));
      var tag = e.link ? "a" : "div";
      return "<" + tag + ' class="sb-card ' + state + '"' +
        (e.link ? ' href="' + esc(e.link) + '" target="_blank" rel="noopener nofollow"' : "") +
        ' title="' + esc(a.name + " vs " + b.name) + '">' +
        '<div class="sb-meta"><span class="sb-lg">' + esc(e.league) + '</span><span class="sb-chip">' + esc(chip) + "</span></div>" +
        row(a, sa > sb, sa < sb) + row(b, sb > sa, sb < sa) + "</" + tag + ">";
    }).join("");
  }

  function fetchJSON(url, ms) {
    var ctrl = window.AbortController ? new AbortController() : null;
    var to = setTimeout(function () { ctrl && ctrl.abort(); }, ms || 6000);
    return fetch(url, { signal: ctrl && ctrl.signal, cache: "no-store" })
      .then(function (r) { clearTimeout(to); if (!r.ok) throw new Error(r.status); return r.json(); });
  }

  function loadSnapshot() {
    if (snapshot) return Promise.resolve(snapshot);
    return fetchJSON(cfg.scoresFallback, 5000).then(function (d) { snapshot = d; return d; });
  }

  function load() {
    var sport = current;
    var url = (cfg.scoreEndpoints || {})[sport];
    var live = url ? fetchJSON(url).then(normalize) : Promise.reject();
    return live.catch(function () {
      return loadSnapshot().then(function (d) { return (d.sports || {})[sport] || []; });
    }).then(function (events) {
      if (sport !== current) return;     // user switched tab meanwhile
      render(events);
      schedule(events.some(function (e) { return e.state === "in"; }) ? 60000 : 300000);
    }).catch(function () {
      track.innerHTML = '<span class="sb-msg">Scores unavailable right now.</span>';
      schedule(300000);
    });
  }

  function schedule(ms) {
    clearTimeout(timer);
    timer = setTimeout(function () { if (!document.hidden) load(); else schedule(ms); }, ms);
  }

  function select(sport) {
    current = sport;
    try { localStorage.setItem("nb-sport", sport); } catch (e) {}
    tabs.forEach(function (b) { b.setAttribute("aria-selected", b.dataset.sport === sport ? "true" : "false"); });
    track.innerHTML = '<span class="sb-msg">Loading…</span>';
    load();
  }

  tabs.forEach(function (b) { b.addEventListener("click", function () { select(b.dataset.sport); }); });
  var next = bar.querySelector(".sb-next");
  if (next) next.addEventListener("click", function () { track.scrollLeft += track.clientWidth * 0.8; });
  var prev = bar.querySelector(".sb-prev");
  if (prev) prev.addEventListener("click", function () { track.scrollLeft -= track.clientWidth * 0.8; });
  document.addEventListener("visibilitychange", function () { if (!document.hidden) load(); });

  select(current);
})();

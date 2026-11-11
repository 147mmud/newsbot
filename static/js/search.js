/* Client-side site search over /search-index.json (loaded on first use). */
(function () {
  var base = (window.NB && NB.base) || "/";
  var idx = null, loading = null;
  function load() {
    if (idx) return Promise.resolve(idx);
    if (!loading) loading = fetch(base + "search-index.json").then(function (r) { return r.json(); }).then(function (d) { idx = d; return d; });
    return loading;
  }
  function norm(s) { return (s || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, ""); }
  function run(q, max) {
    var terms = norm(q).split(/\s+/).filter(Boolean); if (!terms.length) return [];
    var res = [];
    idx.forEach(function (a, i) {
      var h = norm(a.h), t = norm(a.t), s = 0;
      for (var k = 0; k < terms.length; k++) {
        var w = terms[k], inH = h.indexOf(w) > -1, inT = t.indexOf(w) > -1 || norm(a.c).indexOf(w) > -1;
        if (!inH && !inT) return;
        s += inH ? 3 : 1; if (h.indexOf(w) === 0) s += 1;
      }
      res.push([s, i]);
    });
    res.sort(function (x, y) { return y[0] - x[0] || x[1] - y[1]; });
    return res.slice(0, max).map(function (r) { return idx[r[1]]; });
  }
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  function item(a) { return "<a class='sr-item' href='" + base + "news/" + a.s + "/'><b>" + esc(a.h) + "</b><span>" + esc(a.c) + " · " + esc(a.d) + "</span></a>"; }

  /* header search bar */
  var tog = document.getElementById("search-toggle"), bar = document.getElementById("search-bar");
  if (tog && bar) {
    var inp = bar.querySelector("input"), list = bar.querySelector(".sr-list");
    tog.addEventListener("click", function () {
      var open = bar.hasAttribute("hidden"); if (open) { bar.removeAttribute("hidden"); inp.focus(); load(); } else { bar.setAttribute("hidden", ""); }
      tog.setAttribute("aria-expanded", open ? "true" : "false");
    });
    inp.addEventListener("input", function () {
      var q = inp.value.trim(); if (q.length < 2) { list.innerHTML = ""; return; }
      load().then(function () { var r = run(q, 6); list.innerHTML = r.length ? r.map(item).join("") + "<a class='sr-all' href='" + base + "search/?q=" + encodeURIComponent(q) + "'>See all results ›</a>" : "<p class='sr-none'>No stories found.</p>"; });
    });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape" && !bar.hasAttribute("hidden")) { bar.setAttribute("hidden", ""); tog.focus(); } });
  }

  /* full results page */
  var page = document.getElementById("search-results");
  if (page) {
    var f = document.getElementById("search-form"), qi = f.querySelector("input"), cnt = document.getElementById("search-count");
    function show(q) {
      if (!q) { page.innerHTML = ""; cnt.textContent = ""; return; }
      load().then(function () { var r = run(q, 60); cnt.textContent = r.length + (r.length === 60 ? "+" : "") + " result" + (r.length === 1 ? "" : "s") + " for “" + q + "”"; page.innerHTML = r.length ? r.map(item).join("") : "<p class='sr-none'>No stories found. Try different words.</p>"; });
    }
    var q0 = new URLSearchParams(location.search).get("q") || ""; qi.value = q0; show(q0); qi.focus();
    f.addEventListener("submit", function (e) { e.preventDefault(); var q = qi.value.trim(); history.replaceState(null, "", "?q=" + encodeURIComponent(q)); show(q); });
  }
})();

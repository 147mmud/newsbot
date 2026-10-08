/* Adsterra loader - keeps ads fast and CLS-free.
 *  - Banners (728x90, 320x50, 300x250, 160x600): each rendered in its own
 *    iframe when the slot is ~300px from the viewport. Each iframe has its
 *    own `atOptions`, so several banners can share a page safely, and
 *    document.write() inside Adsterra's invoke.js works normally.
 *  - Native Banner: injected into the page (it needs its container div).
 *  - Social Bar + Popunder: injected after the page has loaded, so they
 *    never delay First Contentful Paint / LCP.
 */
(function () {
  "use strict";

  var MOBILE = window.matchMedia("(max-width: 767px)");

  function pickTemplate(slot) {
    var d = slot.querySelector('template[data-variant="desktop"]');
    var m = slot.querySelector('template[data-variant="mobile"]');
    if (MOBILE.matches) return m || (d && +d.dataset.w <= window.innerWidth ? d : null);
    return d || m;
  }

  function renderBanner(slot) {
    var tpl = pickTemplate(slot);
    var box = slot.querySelector(".ad-box");
    if (!tpl || !box) return;
    var w = +tpl.dataset.w, h = +tpl.dataset.h;
    var f = document.createElement("iframe");
    f.width = w; f.height = h;
    f.setAttribute("scrolling", "no");
    f.setAttribute("frameborder", "0");
    f.setAttribute("title", "Advertisement");
    f.style.cssText = "width:" + w + "px;height:" + h + "px;border:0;overflow:hidden";
    f.srcdoc = "<!doctype html><html><head><meta charset='utf-8'><style>html,body{margin:0;padding:0;overflow:hidden;background:transparent}</style></head><body>" +
      tpl.innerHTML + "</body></html>";
    box.appendChild(f);
  }

  /* Scripts added via innerHTML don't execute - recreate them. */
  function injectHTML(target, html) {
    var holder = document.createElement("div");
    holder.innerHTML = html;
    Array.prototype.slice.call(holder.childNodes).forEach(function (node) {
      if (node.nodeName === "SCRIPT") {
        var s = document.createElement("script");
        Array.prototype.forEach.call(node.attributes, function (a) { s.setAttribute(a.name, a.value); });
        s.text = node.text;
        target.appendChild(s);
      } else {
        target.appendChild(node);
      }
    });
  }

  function renderNative(slot) {
    var tpl = slot.querySelector("template");
    if (tpl) injectHTML(slot, tpl.innerHTML);
  }

  function lazy(selector, fn) {
    var slots = document.querySelectorAll(selector);
    if (!("IntersectionObserver" in window)) { slots.forEach(fn); return; }
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) { io.unobserve(e.target); fn(e.target); }
      });
    }, { rootMargin: "300px 0px" });
    slots.forEach(function (s) {
      if (s.offsetParent !== null || s.closest(".sticky-ad")) io.observe(s); // skip display:none slots
    });
  }

  function globalUnits() {
    ["ad-social-bar", "ad-popunder"].forEach(function (id) {
      var tpl = document.getElementById(id);
      if (tpl) injectHTML(document.body, tpl.innerHTML);
    });
  }

  function init() {
    lazy("[data-ad]", renderBanner);
    lazy("[data-ad-native]", renderNative);

    var sticky = document.getElementById("sticky-ad");
    if (sticky) {
      sticky.querySelector(".sticky-close").addEventListener("click", function () {
        sticky.hidden = true; sticky.remove();
      });
    }

    var fired = false;
    function fire() { if (!fired) { fired = true; globalUnits(); } }
    if (document.readyState === "complete") setTimeout(fire, 1500);
    else window.addEventListener("load", function () { setTimeout(fire, 1500); });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();

/* Page-local code for the home page. The ScrollCraft engine (scrollcraft.js) is untouched.
   One bespoke behaviour: the stack drawing beside the post list is the index. It lights the
   layer of the post under the eye as you scroll, and a tap on a layer filters the list. */
(function () {
  "use strict";
  var reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  var phone = matchMedia("(max-width: 860px)");

  // Theme toggle, same storage key as PaperMod so the choice follows the reader into posts.
  var tgl = document.querySelector("[data-theme-toggle]");
  if (tgl) tgl.addEventListener("click", function () {
    var next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    try { localStorage.setItem("pref-theme", next); } catch (e) {}
  });

  if (window.ScrollCraft) window.ScrollCraft.mount(document.body);

  var list = document.querySelector(".list");
  if (!list) return;
  var rows = Array.prototype.slice.call(list.querySelectorAll(".row"));
  var btns = Array.prototype.slice.call(list.querySelectorAll("[data-filter]"));
  var count = list.querySelector("[data-count]");
  var items = list.querySelector(".list__items");
  var filter = "";
  var lit = null;

  function setCount(n) {
    if (!count) return;
    count.textContent = n === 1 ? count.getAttribute("data-one") : count.getAttribute("data-many").replace("%d", n);
  }

  function applyFilter(id) {
    filter = id;
    var n = 0;
    rows.forEach(function (r) {
      var show = !id || r.getAttribute("data-layer") === id;
      if (show) { r.removeAttribute("hidden"); n++; } else r.setAttribute("hidden", "");
    });
    btns.forEach(function (b) { b.setAttribute("aria-pressed", b.getAttribute("data-filter") === id ? "true" : "false"); });
    setCount(n);
    light();
    // Keep the list heading in view so a filter from the bottom of a long list does not strand the reader.
    var top = list.getBoundingClientRect().top;
    if (top < -40) window.scrollTo({ top: list.getBoundingClientRect().top + scrollY - (phone.matches ? 56 : 72), behavior: reduced ? "instant" : "smooth" });
  }

  // Light the layer of the post nearest the reader's eye line (upper third of the viewport).
  function light() {
    var eye = innerHeight * 0.38, best = null, bestD = Infinity;
    rows.forEach(function (r) {
      if (r.hasAttribute("hidden")) return;
      var b = r.getBoundingClientRect();
      if (b.bottom < 0 || b.top > innerHeight) return;
      var d = Math.abs((b.top + b.bottom) / 2 - eye);
      if (d < bestD) { bestD = d; best = r; }
    });
    var id = best ? best.getAttribute("data-layer") : null;
    if (filter) id = null; // a pressed filter already says which layer; no second highlight
    if (id === lit) return;
    lit = id;
    btns.forEach(function (b) {
      if (id && b.getAttribute("data-filter") === id) b.setAttribute("aria-current", "true");
      else b.removeAttribute("aria-current");
    });
  }

  btns.forEach(function (b) {
    b.addEventListener("click", function () {
      var id = b.getAttribute("data-filter");
      applyFilter(id === filter && id ? "" : id); // tapping the active layer again shows all
    });
  });

  var ticking = false;
  addEventListener("scroll", function () {
    if (ticking) return; ticking = true;
    requestAnimationFrame(function () { ticking = false; light(); });
  }, { passive: true });
  addEventListener("resize", light, { passive: true });
  if (items) light();
})();

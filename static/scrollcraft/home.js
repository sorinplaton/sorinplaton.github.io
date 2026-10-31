/* Page-local code for the ScrollCraft home page. The engine (scrollcraft.js) is untouched;
   this file adds the one bespoke behaviour: the docked stack drawing is the shelf's index.
   It lights the layer of the post in view, and a click on a layer brings its first post in. */
(function () {
  "use strict";
  var reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  var phone = matchMedia("(max-width: 860px)");

  // Ten posts on a phone are ~8 screen widths of rail; give them more scroll per card there.
  var shelf = document.querySelector(".shelf");
  if (shelf && phone.matches) shelf.setAttribute("data-sc-span", "8");

  // Theme toggle, same storage key as PaperMod so the choice follows the reader into posts.
  var tgl = document.querySelector("[data-theme-toggle]");
  if (tgl) tgl.addEventListener("click", function () {
    var next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    try { localStorage.setItem("pref-theme", next); } catch (e) {}
  });

  var sc = window.ScrollCraft && window.ScrollCraft.mount(document.body);

  if (!shelf) return;
  var rail = shelf.querySelector(".rail");
  var cards = Array.prototype.slice.call(shelf.querySelectorAll(".obj"));
  var btns = Array.prototype.slice.call(shelf.querySelectorAll("[data-jump]"));
  var index = shelf.querySelector(".index");
  var EXTRA = parseFloat(rail.getAttribute("data-sc-pan")) || 0;
  var current = null;

  // Where the reader's eye is on the shelf: the middle of the area not covered by the index.
  function focusX() {
    if (phone.matches) return innerWidth / 2;
    var r = index.getBoundingClientRect();
    return r.right + (innerWidth - r.right) / 2;
  }

  function update() {
    var fx = focusX(), best = null, bestD = Infinity;
    cards.forEach(function (c) {
      var r = c.getBoundingClientRect();
      var d = Math.abs(r.left + r.width / 2 - fx);
      if (d < bestD) { bestD = d; best = c; }
    });
    // Before the first card reaches the eye line, nothing is lit: the reader is still on the heading.
    if (best) {
      var r0 = cards[0].getBoundingClientRect();
      if (r0.left > fx + 40) best = null;
    }
    if (best === current) return;
    if (current) current.classList.remove("is-current");
    current = best;
    var layer = best ? best.getAttribute("data-layer") : null;
    if (best) best.classList.add("is-current");
    btns.forEach(function (b) {
      if (b.getAttribute("data-jump") === layer) b.setAttribute("aria-current", "true");
      else b.removeAttribute("aria-current");
    });
  }

  // Scroll position at which a given card sits centred on the reader's eye line (focusX).
  // The engine pans by (rail.scrollWidth - innerWidth) * (1 + extra) * p over (act height - viewport),
  // and the rail starts at x = 0, so a card's on-screen x at p is offsetLeft - travel * p.
  function scrollFor(card) {
    var travel = (rail.scrollWidth - innerWidth) * (1 + EXTRA);
    var want = card.offsetLeft + card.offsetWidth / 2 - focusX();
    var p = travel > 0 ? Math.min(1, Math.max(0, want / travel)) : 0;
    var top = shelf.getBoundingClientRect().top + scrollY;
    return top + p * (shelf.offsetHeight - innerHeight);
  }

  function bring(card) {
    if (reduced) {
      // Under reduced motion the engine turns the rail into a native horizontal scroller.
      card.scrollIntoView({ block: "nearest", inline: "center", behavior: "instant" });
      return;
    }
    window.scrollTo({ top: Math.round(scrollFor(card)) + 2, behavior: "smooth" });
  }

  btns.forEach(function (b) {
    b.addEventListener("click", function () {
      var id = b.getAttribute("data-jump");
      var first = cards.filter(function (c) { return c.getAttribute("data-layer") === id; })[0];
      if (first) bring(first);
    });
  });

  // Keyboard: tabbing into the index pins the shelf first, so the buttons are on screen.
  index.addEventListener("focusin", function () {
    if (reduced) return;
    var r = shelf.getBoundingClientRect();
    if (r.top > 0 || r.bottom < innerHeight) window.scrollTo({ top: Math.round(r.top + scrollY) + 1, behavior: "instant" });
  });

  // Keyboard: tabbing through post links brings each card fully into view.
  rail.addEventListener("focusin", function (e) {
    var card = e.target.closest && e.target.closest(".obj");
    if (!card || reduced) return;
    window.scrollTo({ top: Math.round(scrollFor(card)) + 2, behavior: "instant" });
  });

  var ticking = false;
  addEventListener("scroll", function () {
    if (ticking) return; ticking = true;
    requestAnimationFrame(function () { ticking = false; update(); });
  }, { passive: true });
  if (reduced) rail.parentElement.addEventListener("scroll", update, { passive: true });
  addEventListener("resize", update, { passive: true });
  update();
})();

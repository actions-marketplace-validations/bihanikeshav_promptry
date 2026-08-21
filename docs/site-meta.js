/*
 * site-meta.js — single source of truth for the GitHub repo, the published
 * version, and the release date, shared by index.html, docs.html, and
 * architecture.html. Nothing version- or URL-specific should be hard-coded in
 * the pages: they carry only fallbacks that this script canonicalizes on load.
 *
 * On load it:
 *   1. Rewrites every [data-gh] anchor's href from the single REPO constant,
 *      so an org/rename is a one-line change here (not a sweep across pages).
 *   2. Fetches the latest GitHub release (cached in sessionStorage for an hour
 *      to stay well under the unauthenticated rate limit) and fills in:
 *        - .ver                 -> "vX.Y.Z"
 *        - #release-link        -> text "vX.Y.Z" + href to the release
 *        - [data-release-date]  -> "Month YYYY" of the release
 *        - #install-line        -> "Successfully installed promptry-X.Y.Z"
 *   3. Fails silently offline / rate-limited, leaving the markup fallbacks.
 */
(function () {
  "use strict";

  // The one place the repository lives. Change this on an org/rename and every
  // page's links + version fetch follow.
  var REPO = "promptry/promptry";
  var GH = "https://github.com/" + REPO;
  var API = "https://api.github.com/repos/" + REPO + "/releases/latest";
  var CACHE_KEY = "promptry-release";
  var CACHE_TTL_MS = 60 * 60 * 1000; // 1 hour

  function canonicalizeLinks() {
    var links = document.querySelectorAll("[data-gh]");
    for (var i = 0; i < links.length; i++) {
      var path = links[i].getAttribute("data-gh") || "";
      links[i].href = path ? GH + "/" + path.replace(/^\/+/, "") : GH;
    }
  }

  function monthYear(iso) {
    if (!iso) return null;
    var d = new Date(iso);
    if (isNaN(d.getTime())) return null;
    return d.toLocaleDateString("en-US", { month: "long", year: "numeric" });
  }

  function apply(rel) {
    if (!rel || !rel.tag) return;
    var v = "v" + rel.tag;
    var vers = document.querySelectorAll(".ver");
    for (var i = 0; i < vers.length; i++) vers[i].textContent = v;

    var link = document.getElementById("release-link");
    if (link) {
      link.textContent = v;
      if (rel.url) link.href = rel.url;
    }

    if (rel.date) {
      var dates = document.querySelectorAll("[data-release-date]");
      for (var j = 0; j < dates.length; j++) dates[j].textContent = rel.date;
    }

    var install = document.getElementById("install-line");
    if (install) install.textContent = "Successfully installed promptry-" + rel.tag;
  }

  function readCache() {
    try {
      var raw = sessionStorage.getItem(CACHE_KEY);
      if (!raw) return null;
      var c = JSON.parse(raw);
      if (!c || typeof c.at !== "number") return null;
      if (Date.now() - c.at > CACHE_TTL_MS) return null;
      return c.rel;
    } catch (e) {
      return null;
    }
  }

  function writeCache(rel) {
    try {
      sessionStorage.setItem(CACHE_KEY, JSON.stringify({ at: Date.now(), rel: rel }));
    } catch (e) {
      /* private mode / disabled storage: fetch again next load */
    }
  }

  function loadVersion() {
    var cached = readCache();
    if (cached) {
      apply(cached);
      return;
    }
    fetch(API)
      .then(function (r) {
        return r.ok ? r.json() : Promise.reject(new Error("HTTP " + r.status));
      })
      .then(function (j) {
        var tag = String(j.tag_name || "").replace(/^v/, "");
        if (!tag) return;
        var rel = { tag: tag, url: j.html_url || GH + "/releases", date: monthYear(j.published_at) };
        writeCache(rel);
        apply(rel);
      })
      .catch(function () {
        /* offline / rate-limited: keep the static fallbacks already in the DOM */
      });
  }

  function init() {
    canonicalizeLinks();
    loadVersion();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();

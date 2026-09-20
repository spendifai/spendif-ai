/*
 * lang_switch.js - language selection for a statically hosted site.
 *
 * Why this file exists: a static host does no content negotiation, so the only
 * place where the visitor's language preference can be read is the page itself.
 * This is also the place where it is easy to build a redirect loop or to trap a
 * reader in a language they did not ask for, which is why the policy lives in
 * one reviewed file and gets copied, not rewritten per site.
 *
 * Rule and rationale: PROJECT_BLUEPRINT.md 3.0quinquies. In short: English is
 * canonical and always exists; the switch happens only towards a translation
 * that is actually published; it happens once; it never overrides an explicit
 * choice; without JavaScript the reader stays on English, which is correct.
 *
 * Site shape assumed (see languages.js):
 *   /            /guide/        <- English, canonical
 *   /it/         /it/guide/     <- one prefix per translation, mirrored tree
 *
 * Install in <head>, before any rendered content, so a reader never sees a
 * flash of the wrong language:
 *   <script src="/languages.js"></script>
 *   <script src="/lang_switch.js"></script>
 *
 * Switcher markup, anywhere in the page:
 *   <nav data-lang-switcher aria-label="Language"></nav>
 *
 * Matching <head> of an English page (PRODUCT_BLUEPRINT.md 4.2, block B):
 *   <link rel="canonical"  href="https://example.org/guide/">
 *   <link rel="alternate" hreflang="en"        href="https://example.org/guide/">
 *   <link rel="alternate" hreflang="it"        href="https://example.org/it/guide/">
 *   <link rel="alternate" hreflang="x-default" href="https://example.org/guide/">
 */
(function () {
  'use strict';

  var config = window.SITE_LANGUAGES;
  // A single-language site needs no switch: leaving early keeps the page free of
  // a language widget that would offer nothing.
  if (!config || !config.available || config.available.length < 2) return;

  var DEFAULT = config.default || 'en';
  var BASE = (config.base || '').replace(/\/+$/, '');
  var STORAGE_KEY = 'site.lang';
  var codes = config.available.map(function (language) { return language.code; });

  // The path with the publication base stripped, so the same script serves a
  // site at a domain root and a project page published under /repo-name/.
  function sitePath() {
    var path = window.location.pathname;
    if (BASE && path.indexOf(BASE) === 0) path = path.slice(BASE.length);
    return path || '/';
  }

  // Which language the visitor is reading, taken from the URL prefix: a link
  // shared into a translation must keep that translation.
  function currentCode() {
    var prefix = sitePath().split('/')[1];
    return prefix && prefix !== DEFAULT && codes.indexOf(prefix) !== -1 ? prefix : DEFAULT;
  }

  // The query string carried across a switch, minus the one-shot ?lang= override:
  // keeping it would pin the reader to the language they just left.
  function carriedQuery() {
    var params = new URLSearchParams(window.location.search);
    params.delete('lang');
    var rest = params.toString();
    return rest ? '?' + rest : '';
  }

  // The same page in another language. Translations mirror the English tree, so
  // this is adding or removing one prefix rather than consulting a map of URLs.
  function urlFor(code) {
    var path = sitePath();
    var current = currentCode();
    if (current !== DEFAULT) path = path.slice(current.length + 1) || '/';
    var target = code === DEFAULT ? path : '/' + code + path;
    return BASE + target + carriedQuery() + window.location.hash;
  }

  // Storage can throw outright in private modes, and a language preference is
  // never worth breaking the page for: both accessors fail quiet.
  function storedChoice() {
    try { return window.localStorage.getItem(STORAGE_KEY); } catch (error) { return null; }
  }

  function remember(code) {
    try { window.localStorage.setItem(STORAGE_KEY, code); } catch (error) { /* choice just does not persist */ }
  }

  // The first browser language for which a translation actually exists. Exact
  // tag first (pt-BR when that variant is published), then base language, so
  // de-AT reads the German pages instead of falling through to English.
  function detectedCode() {
    var wanted = window.navigator.languages || [window.navigator.language || ''];
    for (var index = 0; index < wanted.length; index++) {
      var tag = String(wanted[index]).toLowerCase();
      if (codes.indexOf(tag) !== -1) return tag;
      var base = tag.split('-')[0];
      if (codes.indexOf(base) !== -1) return base;
    }
    return null;
  }

  // The entire redirect policy in one function, so the four conditions can be
  // checked by reading it: one direction only (canonical English towards a
  // translation), decided once, never over an explicit choice, never towards a
  // language this site does not publish. Returns true when the browser is being
  // sent elsewhere, so the caller can stop.
  function route() {
    var override = new URLSearchParams(window.location.search).get('lang');
    if (override && codes.indexOf(override) !== -1) {
      remember(override);
      if (override !== currentCode()) {
        window.location.replace(urlFor(override));
        return true;
      }
      return false;
    }

    // Landing on a translated URL is itself an explicit choice: record it and
    // leave the reader where they are. Nothing ever redirects away from a
    // translation, which is what makes a loop impossible.
    if (currentCode() !== DEFAULT) {
      if (!storedChoice()) remember(currentCode());
      return false;
    }

    var choice = storedChoice();
    if (!choice) {
      choice = detectedCode() || DEFAULT;
      remember(choice); // decided once: a later visit is never re-detected
    }
    if (choice !== DEFAULT && codes.indexOf(choice) !== -1) {
      window.location.replace(urlFor(choice));
      return true;
    }
    return false;
  }

  // The switcher is what makes the automatic choice reversible: detection is a
  // courtesy to whoever arrives, not a cage, so every published language stays
  // one visible click away and the click is remembered.
  function renderSwitcher() {
    var host = document.querySelector('[data-lang-switcher]');
    if (!host) return;
    var current = currentCode();
    config.available.forEach(function (language) {
      var link = document.createElement('a');
      link.href = urlFor(language.code);
      link.textContent = language.name; // each language written in its own name
      link.setAttribute('lang', language.code);
      link.setAttribute('hreflang', language.code);
      if (language.code === current) link.setAttribute('aria-current', 'true');
      link.addEventListener('click', function () { remember(language.code); });
      host.appendChild(link);
    });
  }

  if (route()) return;

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', renderSwitcher);
  } else {
    renderSwitcher();
  }
})();

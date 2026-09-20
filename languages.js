/*
 * languages.js - the single declaration of which languages this site serves.
 *
 * Why this file exists: the list of published translations must live in exactly
 * one place. Read by lang_switch.js and by the language switcher it renders;
 * nothing else repeats it. A duplicated list drifts, and a drifted list sends
 * readers to a 404.
 *
 * Rule and rationale: PROJECT_BLUEPRINT.md 3.0quinquies.
 *
 * Conventions assumed by lang_switch.js:
 *   - "default" is the canonical language and has NO path prefix. Keep it "en":
 *     English is the version that always exists.
 *   - every other code is also its path prefix ("it" -> /it/...), on a tree that
 *     mirrors the English one. Switching language is then adding or removing one
 *     prefix, never a lookup table to keep in sync.
 *   - "base" is the path the site is published at: "" at a domain root,
 *     "/repo-name" for a project page.
 *
 * Only complete, current translations belong in "available". A translation
 * frozen two versions back is worse than a missing one, because the reader has
 * no way to tell. When you cannot keep it up to date, remove the entry: the
 * files may stay online, they simply stop being a destination.
 */
window.SITE_LANGUAGES = {
  // spendif.ai is a custom domain, so the site is published at the root and the
  // base is empty. A project page would need '/spendif-ai' here.
  base: '',
  default: 'en',
  available: [
    { code: 'en', name: 'English' },
    { code: 'it', name: 'Italiano' },
    { code: 'de', name: 'Deutsch' },
    { code: 'es', name: 'Español' },
    { code: 'fr', name: 'Français' },
    { code: 'ja', name: '日本語' },
    { code: 'nl', name: 'Nederlands' },
    { code: 'pl', name: 'Polski' },
    { code: 'pt', name: 'Português' }
  ]
};

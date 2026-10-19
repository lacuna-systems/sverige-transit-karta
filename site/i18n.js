// UI language. The strings live in i18n/<lang>.json (English is the fallback for any missing key).
// The language is the one picked in the settings menu, or else detected: Swedish for a Swedish
// keyboard layout or a browser set to Swedish, otherwise English.
const LANGUAGES = { en: "English", sv: "Svenska" };
const DEFAULT_LANG = "en";

/** localStorage that fails quietly (private windows, blocked site data). */
const prefs = {
  get: key => { try { return localStorage.getItem(key); } catch { return null; } },
  set: (key, value) => { try { localStorage.setItem(key, value); } catch { /* not kept */ } },
};

const i18n = {
  choice: Object.hasOwn(LANGUAGES, prefs.get("lang")) ? prefs.get("lang") : "auto",
  detected: DEFAULT_LANG,
  lang: DEFAULT_LANG,
  strings: {},
  fallback: {},
};
const loadStrings = lang => fetch(`i18n/${lang}.json`).then(r => r.json());

/** A UI string, with {name} placeholders filled from params. A string with plural forms
 *  ({ "one": ..., "other": ... }) is chosen by params.n. */
function t(key, params = {}) {
  let s = i18n.strings[key] ?? i18n.fallback[key] ?? key;
  if (typeof s === "object") s = s[new Intl.PluralRules(i18n.lang).select(params.n)] ?? s.other;
  return s.replace(/\{(\w+)\}/g, (m, k) => (k in params ? params[k] : m));
}

/** Fill the page's static strings: data-i18n sets the text, data-i18n-<attribute> an attribute. */
function translatePage() {
  document.documentElement.lang = i18n.lang;
  document.title = t("title");
  for (const el of document.querySelectorAll("[data-i18n]")) el.textContent = t(el.dataset.i18n);
  for (const attr of ["title", "placeholder", "aria-label"])
    for (const el of document.querySelectorAll(`[data-i18n-${attr}]`)) el.setAttribute(attr, t(el.getAttribute(`data-i18n-${attr}`)));
}

/** Switch to a language ("auto" for the detected one), then tell the page to re-render ("uilanguage"). */
async function setLanguage(choice) {
  i18n.choice = choice;
  prefs.set("lang", choice);
  const lang = choice === "auto" ? i18n.detected : choice;
  const strings = lang === DEFAULT_LANG ? i18n.fallback : await loadStrings(lang);
  if (i18n.choice !== choice) return;  // changed again while loading
  Object.assign(i18n, { lang, strings });
  translatePage();
  document.dispatchEvent(new Event("uilanguage"));
}

// The Swedish (and Finnish) layout has å right of P and ö, ä right of L. Norwegian and Danish put å
// there too, German ö and ä, so it takes å together with ö or ä to tell.
const SWEDISH_KEYS = { BracketLeft: "å", Semicolon: "ö", Quote: "ä" };
const isSwedishLayout = keys => keys.BracketLeft === "å" && (keys.Semicolon === "ö" || keys.Quote === "ä");

/** Language detection notes in the console, e.g. `[i18n] keyboard layout: BracketLeft=å ...`. */
const debug = (...args) => console.info("[i18n]", ...args);

/** "sv" when the keyboard layout is Swedish, as far as the browser tells (Chromium does); else null. */
async function keyboardLanguage() {
  if (!navigator.keyboard?.getLayoutMap) {
    debug("keyboard layout API not available; will watch typed keys instead");
    return null;
  }
  try {
    const layout = await navigator.keyboard.getLayoutMap();
    const keys = Object.fromEntries(Object.keys(SWEDISH_KEYS).map(k => [k, layout.get(k)]));
    const swedish = isSwedishLayout(keys);
    debug("keyboard layout:", Object.entries(keys).map(([k, v]) => `${k}=${v}`).join(" "), swedish ? "→ Swedish" : "→ not Swedish");
    return swedish ? "sv" : null;
  } catch (e) {
    debug("keyboard layout not readable here:", e.message);  // e.g. in a frame
    return null;
  }
}

/** The first supported language among the browser's preferred ones. */
const browserLanguage = () =>
  (navigator.languages || [navigator.language]).map(l => l.slice(0, 2).toLowerCase()).find(l => Object.hasOwn(LANGUAGES, l)) || DEFAULT_LANG;

// Browsers without the layout API reveal the layout as the visitor types: once å and ö or ä have come
// from their Swedish keys, switch an automatically chosen language to Swedish.
const typedKeys = {};
addEventListener("keydown", e => {
  if (i18n.detected === "sv" || !e.key || SWEDISH_KEYS[e.code] !== e.key.toLowerCase()) return;
  typedKeys[e.code] = SWEDISH_KEYS[e.code];
  debug(`typed ${e.key} on ${e.code}`, isSwedishLayout(typedKeys) ? "→ Swedish layout" : "(needs å and ö or ä)");
  if (!isSwedishLayout(typedKeys)) return;
  i18n.detected = "sv";
  prefs.set("typedLayout", "sv");  // so the next visit starts in Swedish too
  if (i18n.choice === "auto") setLanguage("auto");
  else document.dispatchEvent(new Event("uilanguage"));  // the "Automatic (…)" option names the detected language
});

/** Resolves once the strings for the starting language are loaded and the page is translated. */
const i18nReady = (async () => {
  const [fallback, keyboard] = await Promise.all([loadStrings(DEFAULT_LANG), keyboardLanguage()]);
  i18n.fallback = fallback;
  const typed = prefs.get("typedLayout"), browser = browserLanguage();
  i18n.detected = keyboard || typed || browser;
  debug(`detected ${i18n.detected} (keyboard: ${keyboard || "-"}, earlier typing: ${typed || "-"},`,
    `browser: ${browser} from ${JSON.stringify(navigator.languages || [navigator.language])}); using ${i18n.choice === "auto" ? "it" : `chosen ${i18n.choice}`}`);
  await setLanguage(i18n.choice);
})();

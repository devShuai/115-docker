// Runs before the remote desktop web client starts. The client picks its UI
// language from the browser, so present Simplified Chinese unless the URL asks
// for another language, for example vnc.html?lang=en.
(function () {
  "use strict";
  var language = new URLSearchParams(window.location.search).get("lang") || "zh-CN";
  try {
    Object.defineProperty(window.navigator, "languages", {
      configurable: true,
      get: function () { return [language]; }
    });
    Object.defineProperty(window.navigator, "language", {
      configurable: true,
      get: function () { return language; }
    });
  } catch (error) {
    // The client then follows the browser language.
  }
  // KasmVNC: type Chinese with the visitor's own input method. Only the first
  // visit is changed; turning the option off in the settings panel is kept.
  try {
    if (window.localStorage.getItem("enable_ime") === null) {
      window.localStorage.setItem("enable_ime", "true");
    }
  } catch (error) {
    // Storage can be blocked; the client keeps its own default.
  }
})();

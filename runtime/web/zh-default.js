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

  // Moving between monitors can change DPR without changing the CSS viewport.
  // KasmVNC normally notices this only when the pointer leaves the window.
  // Ask both clients to recalculate immediately; use their own debounce and
  // mouse-coordinate mapping, and retain the user's Native Resolution choice.
  if (window.matchMedia) {
    var ratio, query;
    function watchRatio() {
      if (query) {
        if (query.removeEventListener) query.removeEventListener("change", ratioChanged);
        else query.removeListener(ratioChanged);
      }
      ratio = window.devicePixelRatio || 1;
      query = window.matchMedia("(resolution: " + ratio + "dppx)");
      if (query.addEventListener) query.addEventListener("change", ratioChanged);
      else query.addListener(ratioChanged);
    }
    function ratioChanged() {
      watchRatio();
      window.requestAnimationFrame(function () {
        window.dispatchEvent(new Event("resize"));
      });
    }
    function checkRatio() {
      if (ratio !== (window.devicePixelRatio || 1)) ratioChanged();
    }
    watchRatio();
    window.addEventListener("pageshow", checkRatio);
    document.addEventListener("visibilitychange", checkRatio);
  }
})();

// Show the splash for 2 seconds on the first launch of each session only.
// Loaded as an external script (not inline) so the PWA CSP can use script-src 'self'.
(function () {
  var splash = document.getElementById("hc-splash");
  if (!splash) return;
  var seen = false;
  try { seen = sessionStorage.getItem("homecave_splash_shown") === "1"; } catch (e) {}
  if (seen) { splash.remove(); return; }
  try { sessionStorage.setItem("homecave_splash_shown", "1"); } catch (e) {}
  setTimeout(function () {
    splash.classList.add("hide");
    setTimeout(function () { splash.remove(); }, 400);
  }, 2000);
})();

// DEVELOPMENT ONLY: reload the page when you save a template / CSS / JS / Python file.
// Loaded only when the app runs in debug mode on the laptop (see _head.html).
(function () {
  let version = null;
  async function check() {
    try {
      const r = await fetch("/dev/livereload", { cache: "no-store" });
      const { v } = await r.json();
      if (version !== null && v !== version) {
        // keep the user's place: don't reload while they're typing in a form
        const el = document.activeElement;
        const typing = el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA") && el.value;
        if (!typing) return location.reload();
      }
      version = v;
    } catch (e) { /* server restarting after a Python change - try again */ }
  }
  check();
  setInterval(check, 1000);
})();

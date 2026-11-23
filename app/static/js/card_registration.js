// RFID card registration page: Start -> tap card -> approve. Polls the server every second.
(function () {
  const panels = document.querySelectorAll("[data-panel]");
  const msgBox = document.querySelector("[data-message]");
  let polling = null;

  function show(name) {
    panels.forEach((p) => p.classList.toggle("d-none", p.dataset.panel !== name));
  }
  function message(text) {
    msgBox.textContent = text || "";
    msgBox.classList.toggle("d-none", !text);
  }

  async function refresh() {
    let s;
    try { s = await (await fetch(CARD_URLS.status)).json(); } catch (e) { return; }
    if (s.has_card) { stop(); show("done"); return; }

    const reg = s.registration;
    const active = reg && (reg.status === "waiting" || reg.status === "detected");
    if (!active) {
      stop();
      show("idle");
      if (reg && reg.status === "expired") message("Time ran out. Click Start to try again.");
      return;
    }
    document.querySelectorAll("[data-seconds]").forEach((el) => (el.textContent = reg.seconds_left));
    document.querySelector("[data-uid]").textContent = reg.detected_uid || "";
    // e.g. "Card 1A2B... is already registered to another account" (set by the reader)
    const note = document.querySelector("[data-reg-note]");
    if (note) {
      note.textContent = reg.message || "";
      note.classList.toggle("d-none", !reg.message);
    }
    show(reg.status);
    start();
  }

  function start() { if (!polling) polling = setInterval(refresh, 1000); }
  function stop() { clearInterval(polling); polling = null; }

  document.querySelectorAll("[data-action]").forEach((btn) =>
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      try {
        const r = await postJSON(CARD_URLS.action.replace("ACTION", btn.dataset.action));
        message(r.ok ? "" : r.message);   // e.g. "The card reader is being used by another student..."
      } finally {
        btn.disabled = false;
        refresh();
      }
    })
  );

  refresh();
})();

// Use the same result countdown screens as the physical LCD.
(function () {
  const display = document.getElementById("lcd-preview");
  const preview = JSON.parse(document.getElementById("lcd-preview-data").textContent);
  if (!preview.frames.length) return;

  const started = performance.now();
  function show(lines) {
    display.textContent = lines.join("\n");
  }
  function update() {
    const frame = Math.floor((performance.now() - started) / 1000);
    if (frame < preview.frames.length) {
      show(preview.frames[frame]);
      setTimeout(update, Math.max(1, (frame + 1) * 1000 - (performance.now() - started)));
      return;
    }
    show(preview.ready);
    // Registration may have been confirmed or cancelled during the countdown.
    fetch(display.dataset.statusUrl, { cache: "no-store" })
      .then(response => {
        if (!response.ok) throw new Error("LCD refresh failed");
        return response.json();
      })
      .then(state => show(state.lines))
      .catch(() => {}); // Keep the ready screen if the server is restarting.
  }
  update();
})();

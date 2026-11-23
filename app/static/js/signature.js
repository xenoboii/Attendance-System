// Signature pad: draw -> preview -> save as a trimmed, transparent PNG.
(function () {
  const canvas = document.getElementById("signature-canvas");
  const pad = new SignaturePad(canvas, { penColor: "#0b1f4d", minWidth: 0.8, maxWidth: 2.6 });

  // Make the canvas sharp on phones (high DPI) and keep the drawing on resize
  function resize() {
    const data = pad.toData();
    const ratio = Math.max(window.devicePixelRatio || 1, 1);
    canvas.width = canvas.offsetWidth * ratio;
    canvas.height = canvas.offsetHeight * ratio;
    canvas.getContext("2d").scale(ratio, ratio);
    pad.clear();
    pad.fromData(data);
  }
  window.addEventListener("resize", resize);
  resize();
  const box = document.getElementById("signature-box");
  pad.addEventListener("endStroke", () => box && box.classList.add("has-ink"));

  // Crop the empty space around the signature so it fits nicely in report cells
  function trimmedPNG() {
    const ctx = canvas.getContext("2d");
    const { width, height } = canvas;
    const pixels = ctx.getImageData(0, 0, width, height).data;
    let top = height, left = width, right = -1, bottom = -1;
    for (let y = 0; y < height; y++) {
      for (let x = 0; x < width; x++) {
        if (pixels[(y * width + x) * 4 + 3] > 0) {
          if (x < left) left = x; if (x > right) right = x;
          if (y < top) top = y; if (y > bottom) bottom = y;
        }
      }
    }
    const pad_ = 8;
    left = Math.max(0, left - pad_); top = Math.max(0, top - pad_);
    right = Math.min(width - 1, right + pad_); bottom = Math.min(height - 1, bottom + pad_);
    const w = right - left + 1, h = bottom - top + 1;
    // <canvas data-max-width="360">: shrink the saved image (check-in signatures - one per
    // check-in, so they must stay small on the SD card)
    const maxW = Number(canvas.dataset.maxWidth) || 0;
    const k = maxW && w > maxW ? maxW / w : 1;
    const out = document.createElement("canvas");
    out.width = Math.max(1, Math.round(w * k));
    out.height = Math.max(1, Math.round(h * k));
    const octx = out.getContext("2d");
    octx.imageSmoothingQuality = "high";
    octx.drawImage(canvas, left, top, w, h, 0, 0, out.width, out.height);
    return out.toDataURL("image/png");
  }
  // the pad can live in a pop-up: it has no size until the pop-up is shown
  const modal = canvas.closest(".modal");
  if (modal) modal.addEventListener("shown.bs.modal", () => { pad.clear(); if (box) box.classList.remove("has-ink"); resize(); });

  const emptyMsg = document.getElementById("empty-msg");
  const previewWrap = document.getElementById("preview-wrap");
  const preview = document.getElementById("preview");

  function requireDrawing() {
    const empty = pad.isEmpty();
    emptyMsg.classList.toggle("d-none", !empty);
    return !empty;
  }

  document.getElementById("clear-btn").addEventListener("click", () => {
    pad.clear();
    if (previewWrap) previewWrap.classList.add("d-none");
    if (box) box.classList.remove("has-ink");
  });
  pad.addEventListener("endStroke", () => emptyMsg.classList.add("d-none"));

  const previewBtn = document.getElementById("preview-btn");          // not on the check-in pop-up
  if (previewBtn) previewBtn.addEventListener("click", () => {
    if (!requireDrawing()) return;
    preview.src = trimmedPNG();
    previewWrap.classList.remove("d-none");
  });

  document.getElementById("signature-form").addEventListener("submit", (e) => {
    if (!requireDrawing()) { e.preventDefault(); return; }
    document.getElementById("signature-data").value = trimmedPNG();
  });
})();

// Shared helpers for all pages.

// Keep the scroll position when an action reloads the SAME page (save, mark, delete...),
// so the page doesn't jump back to the top. Going to a different page starts at the top.
(function () {
  const KEY = "keep-scroll";
  try {
    const saved = JSON.parse(sessionStorage.getItem(KEY) || "null");
    sessionStorage.removeItem(KEY);
    if (saved && saved.path === location.pathname && Date.now() - saved.t < 5000) {
      history.scrollRestoration = "manual";
      window.scrollTo(0, saved.y);
      document.addEventListener("DOMContentLoaded", () => window.scrollTo(0, saved.y));
    }
  } catch (_) { /* storage blocked: just scroll normally */ }
  window.addEventListener("pagehide", () => {
    try { sessionStorage.setItem(KEY, JSON.stringify({ path: location.pathname, y: window.scrollY, t: Date.now() })); }
    catch (_) {}
  });
})();

// Floating toasts (warnings / errors after an action): fade out by themselves, or on ✕.
function dismissToast(t) { t.classList.add("hiding"); setTimeout(() => t.remove(), 300); }
function armToast(t) {
  setTimeout(() => dismissToast(t), +t.dataset.autohide || 5000);
  t.querySelector(".btn-close").addEventListener("click", () => dismissToast(t));
}
document.querySelectorAll(".app-toast").forEach(armToast);
// same toast from JavaScript: showToast("Saved.", "success" | "info" | "warning" | "danger")
window.showToast = function (msg, cat) {
  cat = cat || "info";
  let stack = document.querySelector(".toast-stack");
  if (!stack) { stack = document.createElement("div"); stack.className = "toast-stack"; document.body.appendChild(stack); }
  const icon = { warning: "exclamation-triangle-fill", danger: "x-circle-fill", success: "check-circle-fill" }[cat] || "info-circle-fill";
  const t = document.createElement("div");
  t.className = `app-toast app-toast-${cat}`;
  t.dataset.autohide = cat === "warning" || cat === "danger" ? 7000 : 4000;
  t.innerHTML = `<i class="bi bi-${icon}"></i><span></span><button type="button" class="btn-close" aria-label="Close"></button>`;
  t.querySelector("span").textContent = msg;
  stack.appendChild(t);
  armToast(t);
};

// Mobile sidebar: close when tapping outside it
document.addEventListener("click", (e) => {
  const sb = document.getElementById("sidebar");
  if (sb && sb.classList.contains("show") && !sb.contains(e.target)) sb.classList.remove("show");
});

// <button data-toggle-password="inputId"> shows / hides a password
document.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-toggle-password]");
  if (!btn) return;
  const input = document.getElementById(btn.dataset.togglePassword);
  const show = input.type === "password";
  input.type = show ? "text" : "password";
  btn.querySelector(".bi").className = "bi bi-" + (show ? "eye-slash" : "eye");
});

// <button data-copy="text"> copies to clipboard.
// The Pi serves plain http://attendance.lan, where navigator.clipboard is not allowed,
// so fall back to the older copy command.
document.addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-copy]");
  if (!btn) return;
  let ok = false;
  try { await navigator.clipboard.writeText(btn.dataset.copy); ok = true; } catch (_) {
    const ta = Object.assign(document.createElement("textarea"), { value: btn.dataset.copy });
    ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.appendChild(ta); ta.select();
    try { ok = document.execCommand("copy"); } catch (__) { ok = false; }
    ta.remove();
  }
  if (!ok) return;
  const old = btn.innerHTML;
  btn.innerHTML = '<i class="bi bi-check2"></i> Copied';
  setTimeout(() => (btn.innerHTML = old), 1500);
});

// ---------------------------------------------------------------------------
// Device battery (instructors): refresh the sidebar / topbar badge and the
// low-battery warning every minute, without reloading the page.
// ---------------------------------------------------------------------------
(function () {
  const src = document.querySelector("[data-battery-url]");
  if (!src) return;
  const badges = document.querySelectorAll("[data-battery]"), warn = document.querySelector("[data-battery-warning]");
  function paint(b) {
    badges.forEach((el) => {
      el.hidden = !b;
      if (!b) return;
      el.className = el.className.replace(/battery-(ok|low|critical|none)/, "battery-" + b.level);
      el.querySelector("[data-battery-icon]").className = "bi bi-" + b.icon;
      el.querySelector("[data-battery-text]").textContent = b.percent + "%";
      el.title = `Attendance device battery: ${b.percent}%${b.charging ? ", charging" : ""}`;
    });
    if (warn) {
      warn.hidden = !(b && b.level !== "ok" && !b.charging);
      if (b) warn.querySelector("[data-battery-warn-text]").textContent = b.percent + "%";
    }
    document.dispatchEvent(new CustomEvent("battery", { detail: b }));
  }
  async function refresh() {
    try {
      const r = await fetch(src.dataset.batteryUrl, { headers: { "X-Requested-With": "fetch" } });
      if (r.ok) paint((await r.json()).battery);
    } catch (e) { /* offline for a moment - keep the last value */ }
  }
  setInterval(refresh, 60000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
})();

// ---------------------------------------------------------------------------
// Device clock (backup for the RTC module): compare the device's time with this phone's.
//   [data-clock-warning]  banner on every prof page, shown only when they differ by > 2 min
//   [data-clock-card]     Settings card: both clocks live + the sync button
//   [data-clock-sync]     button: set the device's clock to this phone's time
// ---------------------------------------------------------------------------
(function () {
  const banner = document.querySelector("[data-clock-warning]");
  const card = document.querySelector("[data-clock-card]");
  const host = card || banner;
  if (!host) return;
  const url = host.dataset.clockUrl, WARN_MS = 120000;
  let offset = null;                     // device time minus phone time (ms)

  const fmtDiff = (ms) => {
    const s = Math.round(Math.abs(ms) / 1000), m = Math.round(s / 60), h = Math.round(m / 60), d = Math.round(h / 24);
    const txt = s < 90 ? `${s} seconds` : m < 90 ? `${m} minutes` : h < 48 ? `${h} hours` : `${d} days`;
    return `${txt} ${ms < 0 ? "behind" : "ahead"}`;
  };
  const fmtTime = (ms) => new Date(ms).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit", second: "2-digit" });

  async function check() {
    const t0 = Date.now();
    const r = await fetch(url, { headers: { "X-Requested-With": "fetch" } });
    if (!r.ok) return;
    const j = await r.json();
    offset = j.now_ms - (t0 + Date.now()) / 2;          // middle of the request = fair comparison
    const wrong = Math.abs(offset) > WARN_MS || j.behind_last_record;
    if (banner) {
      banner.querySelector("[data-clock-diff]").textContent =
        Math.abs(offset) > WARN_MS ? fmtDiff(offset) : "earlier than the last check-in";
      banner.classList.toggle("d-none", !wrong);
    }
    if (card) {
      const st = card.querySelector("[data-clock-state]");
      st.textContent = wrong ? `Device is ${fmtDiff(offset)}. Sync it.` : "Device clock is correct.";
      st.className = "small " + (wrong ? "text-danger fw-semibold" : "text-success");
    }
  }
  if (card) setInterval(() => {                        // live clocks on the Settings card
    const now = Date.now();
    card.querySelector("[data-clock-phone]").textContent = fmtTime(now);
    if (offset !== null) card.querySelector("[data-clock-device]").textContent = fmtTime(now + offset);
  }, 1000);

  document.querySelectorAll("[data-clock-sync]").forEach((btn) => btn.addEventListener("click", async () => {
    btn.disabled = true;
    try {
      const r = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": document.querySelector('meta[name="csrf-token"]').content },
        body: JSON.stringify({ epoch_ms: Date.now() }),
      });
      const j = await r.json();
      if (!j.ok) throw new Error(j.error);
      window.showToast && window.showToast(j.simulated
        ? "Laptop test: the clock wasn't changed (only the Pi's is)." : "Device clock set from this phone.", j.simulated ? "info" : "success");
      await check();
    } catch (e) {
      window.showToast ? window.showToast(e.message || "Couldn't set the clock.", "danger") : alert(e.message);
    } finally { btn.disabled = false; }
  }));
  // An instructor is logged in on this device, so its time is trusted: if the device's clock
  // is off by more than a minute, correct it without asking. The server refuses while a class
  // is taking check-ins (cut-offs must not move mid-class) - the banner + button stay for that.
  async function autoSync() {
    await check();
    if (offset === null || Math.abs(offset) <= 60000) return;
    const was = fmtDiff(offset);
    const r = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": document.querySelector('meta[name="csrf-token"]').content },
      body: JSON.stringify({ epoch_ms: Date.now(), auto: true }),
    });
    const j = await r.json();
    if (!j.ok || j.held || j.simulated || !j.changed) return;      // laptop test / held: stay quiet
    window.showToast && window.showToast(`Device time corrected from this device (it was ${was}).`, "success");
    await check();
  }
  autoSync().catch(() => {});
})();

// ---------------------------------------------------------------------------
// Section name = Course + Year + Section, previewed live: "BSCpE" + 4th + "a" -> BSCpE 4A
// ---------------------------------------------------------------------------
document.querySelectorAll("[data-section-name]").forEach((row) => {
  const [course, year, letter] = ["course", "year_level", "section_letter"].map((n) => row.querySelector(`[name=${n}]`));
  const out = row.parentElement.querySelector("[data-preview]");
  const show = () => {
    const c = course.value.trim().replace(/\s+/g, " "), y = year.value, l = letter.value.trim().toUpperCase();
    out.textContent = c || y || l ? `${c || "…"} ${y || "…"}${l || "…"}` : "—";
  };
  [course, year, letter].forEach((el) => { el.addEventListener("input", show); el.addEventListener("change", show); });
  show();
});

// ---------------------------------------------------------------------------
// Foldable cards: <details class="fold" open data-fold> fold on phones (less scrolling).
// data-fold-open keeps one open on phones too (e.g. it has an error or something to do).
// On bigger screens they're always open.
// ---------------------------------------------------------------------------
(function () {
  const phone = window.matchMedia("(max-width: 767.98px)");
  const apply = () => document.querySelectorAll("details[data-fold]").forEach((d) => {
    d.open = !phone.matches || d.hasAttribute("data-fold-open");
  });
  apply();
  phone.addEventListener && phone.addEventListener("change", apply);
  // a big screen can't fold them
  document.addEventListener("toggle", (e) => {
    if (e.target.matches && e.target.matches("details[data-fold]") && !phone.matches && !e.target.open) e.target.open = true;
  }, true);
})();

// ---------------------------------------------------------------------------
// Confirmations: any <form data-confirm="message"> shows a styled pop-up
// (instead of the browser's plain "OK / Cancel" box) before it submits.
//   data-confirm-ok="Delete"   optional confirm-button text
//   data-confirm-title="..."   optional title
//   data-confirm-icon="key-fill"  optional Bootstrap icon name
// Destructive actions (delete / remove / end / reset ...) get a red button.
// ---------------------------------------------------------------------------
(function () {
  const DANGER = /\b(delete|remove|end|reset|cancel)\b/i;
  let modalEl, modal, pending = null;

  function build() {
    modalEl = document.createElement("div");
    modalEl.className = "modal fade confirm-modal";
    modalEl.tabIndex = -1;
    modalEl.setAttribute("aria-hidden", "true");
    modalEl.innerHTML = `
      <div class="modal-dialog modal-dialog-centered modal-sm">
        <div class="modal-content">
          <div class="modal-body text-center pt-4 pb-3 px-4">
            <div class="confirm-icon mb-3"><i class="bi"></i></div>
            <h2 class="h6 fw-bold mb-2" data-title></h2>
            <p class="small text-muted mb-0" data-message></p>
          </div>
          <div class="modal-footer justify-content-center gap-2 pb-4 pt-0 border-0">
            <button type="button" class="btn btn-light px-3" data-bs-dismiss="modal">Cancel</button>
            <button type="button" class="btn px-3" data-ok></button>
          </div>
        </div>
      </div>`;
    document.body.appendChild(modalEl);
    modal = new bootstrap.Modal(modalEl);
    modalEl.querySelector("[data-ok]").addEventListener("click", () => {
      const p = pending; pending = null;
      modal.hide();
      if (!p) return;
      p.form.dataset.confirmed = "1";                       // let the real submit through
      if (p.submitter && p.form.requestSubmit) p.form.requestSubmit(p.submitter);
      else p.form.submit();
    });
  }

  document.addEventListener("submit", (e) => {
    const form = e.target;
    const msg = form.dataset && form.dataset.confirm;
    if (!msg) return;
    if (form.dataset.confirmed === "1") { delete form.dataset.confirmed; return; }
    e.preventDefault();
    if (typeof bootstrap === "undefined") {                  // safety net: plain confirm
      if (window.confirm(msg)) { form.dataset.confirmed = "1"; form.requestSubmit(e.submitter); }
      return;
    }
    if (!modalEl) build();
    const label = (e.submitter && e.submitter.textContent.trim()) || "Confirm";
    const danger = DANGER.test(form.dataset.confirmOk || label) || DANGER.test(msg.split("?")[0]);
    modalEl.querySelector("[data-title]").textContent = form.dataset.confirmTitle || (danger ? "Are you sure?" : "Please confirm");
    modalEl.querySelector("[data-message]").textContent = msg;
    const ok = modalEl.querySelector("[data-ok]");
    ok.textContent = form.dataset.confirmOk || label;
    ok.className = "btn px-3 " + (danger ? "btn-danger" : "btn-primary");
    modalEl.querySelector(".confirm-icon").className = "confirm-icon mb-3 " + (danger ? "danger" : "");
    modalEl.querySelector(".confirm-icon .bi").className =
      "bi bi-" + (form.dataset.confirmIcon || (danger ? "exclamation-triangle-fill" : "question-circle-fill"));
    pending = { form, submitter: e.submitter };
    modal.show();
    setTimeout(() => ok.focus(), 200);
  });
})();

// Dropdown menus inside tables (the ⋯ menu on each student) would be cut off by the
// table's scroll box. "fixed" positioning lets them float above everything instead.
if (typeof bootstrap !== "undefined") {
  document.querySelectorAll('.table-responsive [data-bs-toggle="dropdown"], .table-card [data-bs-toggle="dropdown"]')
    .forEach((el) => bootstrap.Dropdown.getOrCreateInstance(el, {
      popperConfig: (cfg) => ({ ...cfg, strategy: "fixed" }),
    }));
}

// <tr data-href="..."> : clicking anywhere on the row opens it (links/buttons inside still work)
document.addEventListener("click", (e) => {
  const row = e.target.closest("tr[data-href]");
  if (!row || e.target.closest("a, button, form, input, .dropdown")) return;
  location.href = row.dataset.href;
});

// Dashboard panels (My subjects / Today's classes): when side by side, stretch them down to
// the bottom of the window; extra items scroll inside. When stacked (phones), keep the CSS height.
(function () {
  const panels = [...document.querySelectorAll(".panel")];
  if (!panels.length) return;
  function fit() {
    panels.forEach((p) => p.style.removeProperty("--panel-h"));
    const tops = panels.map((p) => Math.round(p.getBoundingClientRect().top + window.scrollY));
    const sideBySide = panels.length > 1 && tops.every((t) => t === tops[0]);
    if (!sideBySide) return;
    const room = window.innerHeight - (tops[0] - window.scrollY) - 28;      // 28px gap at the bottom
    const h = Math.max(380, room);
    panels.forEach((p) => p.style.setProperty("--panel-h", h + "px"));
  }
  fit();
  window.addEventListener("resize", fit);
  document.fonts && document.fonts.ready.then(fit);
})();

// POST with the CSRF token, returns parsed JSON.
async function postJSON(url, body = {}) {
  const token = document.querySelector('meta[name="csrf-token"]').content;
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": token },
    body: JSON.stringify(body),
  });
  return res.json();
}

// Breadcrumb trail: add a back button that goes one level up (the last link in the trail).
(function () {
  const add = () => document.querySelectorAll(".crumbs").forEach((crumbs) => {
    const links = crumbs.querySelectorAll("a:not(.crumb-back)");
    if (!links.length || crumbs.querySelector(".crumb-back")) return;
    const parent = links[links.length - 1];
    const back = document.createElement("a");
    back.className = "crumb-back";
    back.href = parent.href;
    back.title = "Back to " + parent.textContent.trim();
    back.setAttribute("aria-label", back.title);
    back.innerHTML = '<i class="bi bi-arrow-left"></i>';
    crumbs.prepend(back);
  });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", add); else add();
})();
// Attendance tab: the Upcoming / Ended lists stay open or folded the way you left them,
// also after the page reloads (e.g. right after marking a date as "no class").
(function () {
  document.querySelectorAll("details.attendance-fold").forEach((d, i) => {
    const key = "fold:" + location.pathname + ":" + i;
    try {
      const saved = sessionStorage.getItem(key);
      if (saved !== null) d.open = saved === "1";
    } catch (e) {}
    d.addEventListener("toggle", () => { try { sessionStorage.setItem(key, d.open ? "1" : "0"); } catch (e) {} });
  });
})();
// Phones: tables turn into stacked cards (CSS, .table under 576px). Each cell needs its
// column name to show as a small label, so copy the header text onto every cell as
// data-label. Runs again when rows are swapped in live (session roster refresh).
(function () {
  const label = (root) => (root.querySelectorAll ? root : document).querySelectorAll("table.table").forEach((table) => {
    const heads = [...table.querySelectorAll("thead th")].map((th) => th.textContent.trim());
    if (!heads.length) return;
    table.classList.add("table-stack");
    table.querySelectorAll("tbody tr").forEach((tr) => {
      let col = 0;
      [...tr.children].forEach((td) => {
        if (!td.hasAttribute("data-label") && heads[col]) td.setAttribute("data-label", heads[col]);
        if (td.colSpan > 1) td.classList.add("cell-wide");
        col += td.colSpan || 1;
      });
    });
  });
  const run = () => {
    label(document);
    new MutationObserver((changes) => {
      if (changes.some((c) => [...c.addedNodes].some((n) => n.nodeType === 1 && (n.matches("table, tr, tbody") || n.querySelector("table, tr")))))
        label(document);
    }).observe(document.body, { childList: true, subtree: true });
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", run); else run();
})();

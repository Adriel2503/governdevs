// Notificaciones efímeras (aria-live) para feedback de acciones.

import { el } from "./api.js";

let region;

function ensureRegion() {
  if (!region) {
    region = el("div", { class: "toast-region", attrs: { "aria-live": "polite", "aria-atomic": "false" } });
    document.body.appendChild(region);
  }
  return region;
}

export function toast(message, { type = "info", timeout = 4000 } = {}) {
  const node = el("div", { class: `toast ${type === "error" ? "error" : ""}`, text: message, attrs: { role: "status" } });
  ensureRegion().appendChild(node);
  setTimeout(() => {
    node.style.opacity = "0";
    node.style.transition = "opacity .2s";
    setTimeout(() => node.remove(), 200);
  }, timeout);
}

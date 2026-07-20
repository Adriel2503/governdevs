// Modal accesible reutilizable (visor de reglas): Esc para cerrar, click en el
// backdrop cierra, y devuelve el foco al elemento que lo abrió.

import { el } from "./api.js";

let backdrop, titleEl, pathEl, bodyPre, lastFocused;

function build() {
  bodyPre = el("pre");
  titleEl = el("h3", { attrs: { id: "modalTitle" } });
  pathEl = el("span", { class: "modal-path" });
  const closeBtn = el("button", {
    class: "ghost",
    text: "Cerrar ✕",
    attrs: { type: "button", "aria-label": "Cerrar" },
    on: { click: closeModal },
  });
  const head = el("div", { class: "modal-head" }, [el("div", {}, [titleEl, pathEl]), closeBtn]);
  const body = el("div", { class: "modal-body" }, [bodyPre]);
  const modal = el("div", {
    class: "modal",
    attrs: { role: "dialog", "aria-modal": "true", "aria-labelledby": "modalTitle" },
  }, [head, body]);

  backdrop = el("div", { class: "modal-backdrop" }, [modal]);
  backdrop.addEventListener("click", (e) => { if (e.target === backdrop) closeModal(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && backdrop.classList.contains("open")) closeModal(); });
  document.body.appendChild(backdrop);
}

export function openModal({ title, path, content }) {
  if (!backdrop) build();
  lastFocused = document.activeElement;
  titleEl.textContent = title || "";
  pathEl.textContent = path || "";
  bodyPre.textContent = content || "";
  bodyPre.scrollTop = 0;
  backdrop.classList.add("open");
  backdrop.querySelector("button").focus();
}

export function closeModal() {
  if (!backdrop) return;
  backdrop.classList.remove("open");
  if (lastFocused && typeof lastFocused.focus === "function") lastFocused.focus();
}

// Modal accesible reutilizable (visor de reglas): Esc para cerrar, click en el
// backdrop cierra, y devuelve el foco al elemento que lo abrió.
//
// El contenido se muestra RENDERIZADO. Antes era un <pre> con el .md crudo, que
// para un documento de 12 KB lleno de bloques de código es ilegible.
//
// Queda el botón "Ver original" porque estos documentos son la norma: si el
// render se equivoca en algo, tiene que haber forma de leer exactamente lo que
// está guardado — que es además lo que reciben los agentes por MCP.

import { el } from "./api.js";
import { renderMarkdown } from "./markdown.js";

let backdrop, titleEl, pathEl, bodyEl, toggleBtn, lastFocused;
let crudo = "";
let verCrudo = false;

function pintar() {
  if (verCrudo) bodyEl.replaceChildren(el("pre", { class: "md-crudo", text: crudo }));
  else bodyEl.replaceChildren(el("div", { class: "md" }, [renderMarkdown(crudo)]));

  toggleBtn.textContent = verCrudo ? "Ver formateado" : "Ver original";
  toggleBtn.setAttribute("aria-pressed", String(verCrudo));
  bodyEl.scrollTop = 0;
}

function build() {
  bodyEl = el("div", { class: "modal-body" });
  titleEl = el("h3", { attrs: { id: "modalTitle" } });
  pathEl = el("span", { class: "modal-path" });

  toggleBtn = el("button", {
    class: "secondary btn-sm",
    attrs: { type: "button", "aria-pressed": "false" },
    on: { click: () => { verCrudo = !verCrudo; pintar(); } },
  });
  const closeBtn = el("button", {
    class: "ghost",
    text: "Cerrar ✕",
    attrs: { type: "button", "aria-label": "Cerrar" },
    on: { click: closeModal },
  });

  const head = el("div", { class: "modal-head" }, [
    el("div", {}, [titleEl, pathEl]),
    el("div", { class: "modal-acciones" }, [toggleBtn, closeBtn]),
  ]);
  const modal = el("div", {
    class: "modal",
    attrs: { role: "dialog", "aria-modal": "true", "aria-labelledby": "modalTitle" },
  }, [head, bodyEl]);

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
  crudo = content || "";
  verCrudo = false; // cada documento abre formateado, sin arrastrar el modo anterior
  pintar();
  backdrop.classList.add("open");
  toggleBtn.focus();
}

function closeModal() {
  if (!backdrop) return;
  backdrop.classList.remove("open");
  if (lastFocused && typeof lastFocused.focus === "function") lastFocused.focus();
}

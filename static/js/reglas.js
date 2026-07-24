// Bloque "Reglas indexadas": un chip por regla, visor en modal y búsqueda BM25
// con resaltado seguro (sin innerHTML) de los fragmentos que devuelve el backend.

import { getJSON, el } from "./api.js";
import { toast } from "./toast.js";
import { openModal } from "./modal.js";

const $ = (id) => document.getElementById(id);

// "2.-Application-Handler-con-Wolverine.md" -> "Application Handler con Wolverine"
function displayName(archivo) {
  return archivo
    .replace(/\.md$/i, "")
    .replace(/^\d+[.\-\s]*/, "")
    .replace(/-/g, " ")
    .trim();
}

let activeChip = null;

async function openRegla(capa, chip) {
  try {
    const detalle = await getJSON(`/wiki/reglas/${encodeURIComponent(capa)}`);
    if (activeChip) activeChip.setAttribute("aria-pressed", "false");
    chip.setAttribute("aria-pressed", "true");
    activeChip = chip;
    openModal({ title: displayName(detalle.archivo), path: detalle.ruta_relativa, content: detalle.contenido });
  } catch (e) {
    toast(`No se pudo abrir la regla: ${e.message}`, { type: "error" });
  }
}

export async function refreshReglas() {
  const list = $("reglasList");
  try {
    const reglas = await getJSON("/wiki/reglas");
    $("reglasCount").textContent = reglas.length ? `${reglas.length} reglas` : "";
    if (!reglas.length) {
      list.replaceChildren(
        el("span", { class: "muted", text: "Sin reglas indexadas todavía. Importalas con «Escanear», arriba." })
      );
      return;
    }
    list.replaceChildren(
      ...reglas.map((r) => {
        const chip = el("button", {
          class: "regla-chip",
          text: displayName(r.archivo),
          attrs: { type: "button", "aria-pressed": "false", title: r.ruta_relativa },
        });
        chip.addEventListener("click", () => openRegla(r.capa, chip));
        return chip;
      })
    );
  } catch (e) {
    list.replaceChildren(el("span", { class: "muted", text: `Error al listar reglas: ${e.message}` }));
  }
}

function highlightInto(container, snippet) {
  // El backend marca coincidencias con **...**; las convertimos en <mark>.
  snippet.split("**").forEach((part, i) => {
    if (i % 2 === 1) container.append(el("mark", { text: part }));
    else container.append(document.createTextNode(part));
  });
}

function bindSearch() {
  $("buscarForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const q = $("buscarQuery").value.trim();
    const out = $("buscarResults");
    if (!q) { out.replaceChildren(); return; }
    try {
      const results = await getJSON("/wiki/buscar?q=" + encodeURIComponent(q));
      out.replaceChildren();
      if (!results.length) {
        out.append(el("p", { class: "muted", text: "Sin resultados." }));
        return;
      }
      for (const r of results) {
        const snippet = el("span", { class: "sr-snippet" });
        highlightInto(snippet, r.snippet || "");
        out.append(el("div", { class: "search-result" }, [el("span", { class: "sr-file", text: r.archivo }), snippet]));
      }
    } catch (err) {
      out.replaceChildren(el("p", { class: "muted", text: `Error: ${err.message}` }));
    }
  });
}

export function initReglas() {
  bindSearch();
  refreshReglas();
}

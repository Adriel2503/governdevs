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

// `chip` es opcional: desde un resultado de búsqueda se abre la misma regla,
// pero no hay chip que marcar como activo.
async function openRegla(capa, chip) {
  try {
    const detalle = await getJSON(`/wiki/reglas/${encodeURIComponent(capa)}`);
    if (chip) {
      if (activeChip) activeChip.setAttribute("aria-pressed", "false");
      chip.setAttribute("aria-pressed", "true");
      activeChip = chip;
    }
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

// El backend ya entrega el fragmento partido en tramos, con `hit` indicando
// cuáles coincidieron. Antes acá se partía por '**' —la marca de SQLite/FTS5—
// y desde la migración a ParadeDB eso resaltaba la negrita del markdown en vez
// de la coincidencia.
function resaltarEn(container, tramos) {
  for (const tramo of tramos) {
    if (tramo.hit) container.append(el("mark", { text: tramo.t }));
    else container.append(document.createTextNode(tramo.t));
  }
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
        resaltarEn(snippet, r.tramos || [{ t: r.snippet || "", hit: false }]);
        // Encontrar la regla y no poder abrirla obligaba a ir a buscar el chip
        // a mano. El backend ya devuelve la capa en cada resultado.
        out.append(
          el("button", {
            class: "search-result",
            attrs: { type: "button", "aria-label": `Abrir ${r.archivo}` },
            on: { click: () => openRegla(r.capa) },
          }, [el("span", { class: "sr-file", text: r.archivo }), snippet])
        );
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

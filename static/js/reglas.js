// Panel de lineamientos: chips por regla, visor en modal y búsqueda FTS5 con
// resaltado seguro (sin innerHTML) de los fragmentos que devuelve el backend.

import { getJSON, postJSON, el } from "./api.js";
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

// "Sincronizar fuente" lee los .md bundleados en el contenedor. Si no están
// montados (el deploy los trata como confidenciales y no los hornea en la
// imagen), el botón no sirve y el estado vacío tiene que mandar al importador,
// que es el camino que sí funciona.
let wikiBundleada = true;

export function aplicarCapacidades(caps) {
  if (caps?.wiki_bundle !== false) return;
  wikiBundleada = false;

  const btn = $("syncWikiBtn");
  btn.disabled = true;
  btn.setAttribute("title", "No hay lineamientos montados en el servidor: importalos desde un repositorio o un ZIP.");
  refreshReglas(); // repinta el estado vacío con el texto correcto
}

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
        el("span", {
          class: "muted",
          text: wikiBundleada
            ? "Sin reglas indexadas. Usa “Sincronizar fuente”."
            : "Sin reglas indexadas. Importalas desde “Lineamientos oficiales”, arriba.",
        })
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

function bindSync() {
  $("syncWikiBtn").addEventListener("click", async () => {
    const status = $("wikiSyncStatus");
    const btn = $("syncWikiBtn");
    btn.disabled = true;
    status.textContent = "Sincronizando…";
    try {
      const r = await postJSON("/wiki/sync", {});
      status.textContent = `${r.reglas_indexadas} reglas indexadas`;
      toast("Lineamientos reindexados.");
      refreshReglas();
    } catch (e) {
      status.textContent = "";
      toast(`Error al sincronizar: ${e.message}`, { type: "error" });
    } finally {
      btn.disabled = false;
    }
  });
}

export function initReglas() {
  bindSearch();
  bindSync();
  refreshReglas();
}

// Importaciones cargadas: qué lotes de lineamientos hay en la base y cómo
// quitarlos.
//
// La unidad es la FUENTE ('archivo.zip#carpeta/elegida'), no el documento
// suelto: es como uno razona cuando se equivoca — "subí este ZIP y elegí mal la
// carpeta". Borrar por fuente deja intactas las otras importaciones.

import { getJSON, del, el } from "./api.js";
import { toast } from "./toast.js";
import { refreshReglas } from "./reglas.js";

// La fuente viene como 'origen#carpeta'; separarla hace legible una cadena que
// si no ocupa media pantalla.
function partir(fuente) {
  const i = fuente.indexOf("#");
  return i === -1 ? [fuente, "(raíz)"] : [fuente.slice(0, i), fuente.slice(i + 1)];
}

function vacio(tbody) {
  const td = el("td", { attrs: { colspan: "4" } }, [
    el("div", { class: "empty-state" }, [
      el("div", { class: "es-icon", text: "◇" }),
      el("p", { text: "Todavía no importaste lineamientos. Usá «Desde repositorio» o «Subir ZIP», arriba." }),
    ]),
  ]);
  tbody.replaceChildren(el("tr", {}, [td]));
}

function fila(f) {
  const [origen, carpeta] = partir(f.fuente);

  const quitar = el("button", {
    class: "ghost btn-sm",
    text: "Quitar",
    attrs: { type: "button", "aria-label": `Quitar los lineamientos de ${origen}` },
    on: { click: () => borrar(f) },
  });

  return el("tr", {}, [
    el("td", { class: "repo-name", text: origen }),
    el("td", { class: "mono", text: carpeta }),
    el("td", { class: "num", text: f.documentos }),
    el("td", { class: "actions" }, [quitar]),
  ]);
}

export async function refrescarFuentes() {
  let fuentes;
  try {
    fuentes = await getJSON("/wiki/fuentes");
  } catch (e) {
    toast(`No se pudieron cargar las importaciones: ${e.message}`, { type: "error" });
    return;
  }

  const tbody = document.querySelector("#fuentesTable tbody");
  if (!fuentes.length) vacio(tbody);
  else tbody.replaceChildren(...fuentes.map(fila));
}

async function borrar(f) {
  const [origen, carpeta] = partir(f.fuente);
  const ok = window.confirm(
    `¿Quitar los ${f.documentos} lineamientos importados de:\n\n` +
      `  ${origen}\n  carpeta: ${carpeta}\n\n` +
      "Se borran de la base. Los agentes dejan de verlos al instante. " +
      "Podés volver a importarlos cuando quieras."
  );
  if (!ok) return;

  try {
    const r = await del(`/wiki/fuentes?fuente=${encodeURIComponent(f.fuente)}`);
    toast(`${r.eliminados} lineamientos eliminados.`);
    refrescarFuentes();
    refreshReglas(); // el panel de reglas quedó desactualizado
  } catch (e) {
    toast(`No se pudo eliminar: ${e.message}`, { type: "error" });
  }
}

export function initFuentes() {
  refrescarFuentes();
}

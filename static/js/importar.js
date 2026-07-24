// Import de lineamientos: escanea una fuente (repo git o ZIP subido), muestra el
// árbol de carpetas con conteo de .md y deja indexar la carpeta elegida. Flujo
// en dos pasos contra /wiki/import/{git,zip} y /wiki/import/index. Al terminar
// refresca la lista de reglas existente. Reutiliza el(), api(), toast().

import { api, postJSON, el } from "./api.js";
import { toast } from "./toast.js";
import { refreshReglas } from "./reglas.js";
import { refrescarFuentes } from "./fuentes.js";

const $ = (id) => document.getElementById(id);

let importId = null;
let fuente = null;

function setMode(git) {
  $("importModeGit").setAttribute("aria-pressed", String(git));
  $("importModeZip").setAttribute("aria-pressed", String(!git));
  $("importGitForm").hidden = !git;
  $("importZipForm").hidden = git;
  $("importResult").replaceChildren();
}

async function runScan(btn, fn) {
  if (btn) btn.disabled = true;
  const out = $("importResult");
  out.replaceChildren(el("span", { class: "muted", text: "Escaneando fuente…" }));
  try {
    const res = await fn();
    importId = res.import_id;
    fuente = res.fuente;
    renderCarpetas(res.carpetas || []);
  } catch (err) {
    out.replaceChildren(el("p", { class: "muted", text: `Error: ${err.message}` }));
  } finally {
    if (btn) btn.disabled = false;
  }
}

function renderCarpetas(carpetas) {
  const out = $("importResult");
  if (!carpetas.length) {
    out.replaceChildren(el("p", { class: "muted", text: "No se encontraron archivos .md en la fuente." }));
    return;
  }
  const items = carpetas.map((c, i) =>
    el("label", { class: "carpeta-item" }, [
      el("input", { attrs: { type: "radio", name: "carpetaImport", value: c.path, checked: i === 0 ? true : null } }),
      el("span", { class: "carpeta-path", text: c.path || "Todo (raíz)" }),
      el("span", { class: "carpeta-count", text: `${c.archivos_md} .md` }),
    ])
  );
  const indexBtn = el("button", {
    class: "secondary",
    text: "Indexar carpeta seleccionada",
    attrs: { type: "button" },
    on: { click: () => runIndex(indexBtn) },
  });
  out.replaceChildren(
    el("p", { class: "muted", text: `Fuente: ${fuente} — elige la carpeta a indexar:` }),
    el("div", { class: "carpeta-list" }, items),
    indexBtn
  );
}

async function runIndex(btn) {
  const sel = document.querySelector('input[name="carpetaImport"]:checked');
  if (!sel) {
    toast("Elige una carpeta.", { type: "error" });
    return;
  }
  btn.disabled = true;
  try {
    const r = await postJSON("/wiki/import/index", { import_id: importId, carpeta: sel.value });
    toast(`${r.indexadas} lineamientos indexados desde “${r.carpeta}”.`);
    $("importResult").replaceChildren();
    importId = null;
    refreshReglas();
    refrescarFuentes(); // la importación recién hecha tiene que aparecer en la lista
  } catch (err) {
    toast(`No se pudo indexar: ${err.message}`, { type: "error" });
    btn.disabled = false;
  }
}

export function initImportar() {
  const git = $("importGitForm");
  const zip = $("importZipForm");
  if (!git || !zip) return; // panel ausente → no cablear

  $("importModeGit").addEventListener("click", () => setMode(true));
  $("importModeZip").addEventListener("click", () => setMode(false));

  // El input de archivo está oculto (se dispara desde el label estilado), así
  // que el nombre del elegido hay que mostrarlo a mano.
  $("importZipFile").addEventListener("change", (e) => {
    const f = e.target.files[0];
    $("importZipName").textContent = f ? f.name : "Ningún archivo elegido";
  });

  git.addEventListener("submit", (e) => {
    e.preventDefault();
    const url = $("importUrl").value.trim();
    const token = $("importToken").value.trim() || undefined;
    if (!url) return;
    runScan(e.submitter, () => postJSON("/wiki/import/git", { url, token }));
  });

  zip.addEventListener("submit", (e) => {
    e.preventDefault();
    const file = $("importZipFile").files[0];
    if (!file) {
      toast("Elige un archivo ZIP.", { type: "error" });
      return;
    }
    // multipart: FormData sin header JSON (api() reenvía el body tal cual).
    const fd = new FormData();
    fd.append("file", file);
    runScan(e.submitter, () => api("/wiki/import/zip", { method: "POST", body: fd }));
  });
}

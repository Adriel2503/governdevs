// Panel de credenciales: es el prerrequisito de todo el flujo de GitHub.
// Sin una credencial cargada, un repo se registra y se indexa, pero NO se le
// puede registrar el webhook — y sin webhook no hay reindexado en push ni
// verificación de PR.
//
// El token se escribe una vez y no vuelve nunca: `GET /credenciales` selecciona
// columnas explícitas y excluye `token_cifrado` (app/credenciales.py). Este
// módulo no puede mostrarlo aunque quisiera, y así debe seguir.

import { getJSON, postJSON, del, el } from "./api.js";
import { toast } from "./toast.js";

const $ = (id) => document.getElementById(id);

// Quien quiera enterarse de que la lista cambió (el <select> del registro).
const suscriptores = new Set();
let ultimas = [];

export function alCambiarCredenciales(fn) {
  suscriptores.add(fn);
  fn(ultimas); // estado actual, para que el suscriptor no arranque vacío
}

function fecha(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleDateString("es-PE");
}

function emptyRow(tbody) {
  const td = el("td", { attrs: { colspan: "5" } }, [
    el("div", { class: "empty-state" }, [
      el("div", { class: "es-icon", text: "◇" }),
      el("p", {
        text: "Todavía no hay credenciales. Sin una, los repositorios se registran pero no se actualizan solos.",
      }),
    ]),
  ]);
  tbody.replaceChildren(el("tr", {}, [td]));
}

function renderRow(cred) {
  const quitar = el("button", {
    class: "ghost btn-sm",
    text: "Quitar",
    attrs: { type: "button", "aria-label": `Quitar credencial ${cred.alias}` },
    on: { click: () => borrar(cred) },
  });

  return el("tr", {}, [
    el("td", { class: "repo-name", text: cred.alias }),
    el("td", {}, [el("span", { class: "badge listo", text: cred.tipo === "pat" ? "PAT" : cred.tipo })]),
    el("td", { text: cred.github_login || "—" }),
    el("td", { text: fecha(cred.creado_en) }),
    el("td", { class: "actions" }, [quitar]),
  ]);
}

async function refrescarCredenciales() {
  let creds;
  try {
    creds = await getJSON("/credenciales");
  } catch (e) {
    toast(`No se pudieron cargar las credenciales: ${e.message}`, { type: "error" });
    return;
  }

  ultimas = creds;
  const tbody = document.querySelector("#credsTable tbody");
  if (!creds.length) emptyRow(tbody);
  else tbody.replaceChildren(...creds.map(renderRow));

  for (const fn of suscriptores) fn(creds);
}

async function borrar(cred) {
  // La FK es ON DELETE SET NULL: los repos NO se borran, quedan sin credencial
  // y dejan de poder sincronizar. El aviso tiene que decir eso, no un genérico.
  const ok = window.confirm(
    `¿Quitar la credencial "${cred.alias}"?\n\n` +
      "Los repositorios que la usan NO se borran, pero se quedan sin token: " +
      "dejan de poder clonar y de actualizarse solos."
  );
  if (!ok) return;

  try {
    await del(`/credenciales/${encodeURIComponent(cred.id)}`);
    toast(`Credencial "${cred.alias}" eliminada.`);
    refrescarCredenciales();
  } catch (e) {
    toast(`No se pudo eliminar: ${e.message}`, { type: "error" });
  }
}

function bindForm() {
  $("credForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const alias = $("credAlias").value.trim();
    const token = $("credToken").value.trim();
    const github_login = $("credLogin").value.trim() || undefined;
    if (!alias || !token) return;

    const btn = e.submitter;
    if (btn) btn.disabled = true;
    try {
      await postJSON("/credenciales", { alias, token, github_login });
      toast(`Credencial "${alias}" guardada y cifrada.`);
      e.target.reset();
      refrescarCredenciales();
    } catch (err) {
      toast(`No se pudo guardar: ${err.message}`, { type: "error" });
    } finally {
      if (btn) btn.disabled = false;
    }
  });
}

export function initCredenciales() {
  bindForm();
  refrescarCredenciales();
}

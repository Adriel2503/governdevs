// Panel de actividad: la evidencia de que el CI/CD está vivo.
//
// Dos vistas de la misma pregunta ("¿qué pasó?"): los reindexados que dispara
// cada push, y las verificaciones que dispara cada PR.
//
// Los endpoints son POR REPO (/repos/{name}/jobs, /repos/{name}/revisiones), así
// que se hace fan-out con Promise.all y se mezcla en una sola tabla cronológica
// — mismo patrón que withGraphCounts en repos.js. Con la cantidad de repos de un
// piloto (unidades) es irrelevante; si algún día se pasan de ~20, conviene un
// endpoint global /actividad en vez de N peticiones.

import { getJSON, el } from "./api.js";
import { toast } from "./toast.js";

const POLL_MS = 4000;
const EN_CURSO = new Set(["encolado", "corriendo", "generando"]);

let vista = "jobs"; // "jobs" | "revisiones"
let pollTimer = null;
let ultimaFirma = "";

const $ = (id) => document.getElementById(id);

const ICONO = {
  ok: "✅",
  error: "❌",
  corriendo: "⏳",
  generando: "⏳",
  encolado: "⏸",
  advertencias: "⚠️",
  viola: "❌",
};

const COLUMNAS = {
  jobs: ["Repositorio", "Evento", "Commit", "Estado", "Encolado", "Duración"],
  revisiones: ["Repositorio", "PR", "Rama", "Autor", "Estado", "Creada"],
};

function estado(valor) {
  const icono = ICONO[valor] ?? "•";
  return el("span", { class: `badge ${valor}`, text: `${icono} ${valor}` });
}

function hora(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleTimeString("es-PE", { hour: "2-digit", minute: "2-digit" });
}

function duracion(inicio, fin) {
  if (!inicio || !fin) return "—";
  const ms = new Date(fin) - new Date(inicio);
  if (!Number.isFinite(ms) || ms < 0) return "—";
  if (ms < 1000) return `${ms} ms`;
  const s = Math.round(ms / 1000);
  return s < 60 ? `${s} s` : `${Math.floor(s / 60)} m ${s % 60} s`;
}

const corto = (sha) => (sha ? sha.slice(0, 7) : "—");

function filaJob(job) {
  const celdaEstado = el("td", {}, [estado(job.estado)]);
  // El mensaje de error solo vive en la base; acá se ve sin abrir psql.
  if (job.estado === "error" && job.mensaje) {
    celdaEstado.append(el("div", { class: "muted mini", text: job.mensaje.slice(0, 90) }));
    celdaEstado.setAttribute("title", job.mensaje);
  }

  return el("tr", {}, [
    el("td", { class: "repo-name", text: job.repo_name }),
    el("td", { text: job.evento }),
    el("td", { class: "mono", text: corto(job.commit_sha) }),
    celdaEstado,
    el("td", { text: hora(job.encolado_en) }),
    el("td", { class: "num", text: duracion(job.iniciado_en, job.finalizado_en) }),
  ]);
}

function filaRevision(rev) {
  // Si hay comentario en GitHub, la fila enlaza a él: cierra el círculo entre
  // esta tabla y el veredicto real que ve el desarrollador.
  const pr = rev.comentario_url
    ? el("a", {
        text: `#${rev.pr_numero}`,
        attrs: { href: rev.comentario_url, target: "_blank", rel: "noopener" },
      })
    : el("span", { text: rev.pr_numero != null ? `#${rev.pr_numero}` : "—" });

  return el("tr", {}, [
    el("td", { class: "repo-name", text: rev.repo_name }),
    el("td", {}, [pr]),
    el("td", { text: rev.rama || "—" }),
    el("td", { text: rev.autor || "—" }),
    el("td", {}, [estado(rev.estado)]),
    el("td", { text: hora(rev.creado_en) }),
  ]);
}

function vacio(tbody, columnas) {
  const texto =
    vista === "jobs"
      ? "Todavía no hubo sincronizaciones. Aparecen acá al registrar un repositorio o al hacer push a la rama vigilada."
      : "Todavía no hubo revisiones. Aparecen acá al abrir o actualizar un pull request hacia la rama vigilada.";
  const td = el("td", { attrs: { colspan: String(columnas) } }, [
    el("div", { class: "empty-state" }, [
      el("div", { class: "es-icon", text: "◇" }),
      el("p", { text: texto }),
    ]),
  ]);
  tbody.replaceChildren(el("tr", {}, [td]));
}

async function traer(repos) {
  const ruta = vista === "jobs" ? "jobs" : "revisiones";
  const porRepo = await Promise.all(
    repos.map(async (r) => {
      try {
        return await getJSON(`/repos/${encodeURIComponent(r.name)}/${ruta}`);
      } catch {
        return []; // un repo que falla no puede tumbar la tabla entera
      }
    })
  );

  const clave = vista === "jobs" ? "encolado_en" : "creado_en";
  return porRepo.flat().sort((a, b) => String(b[clave] ?? "").localeCompare(String(a[clave] ?? "")));
}

export async function refrescarActividad() {
  let repos;
  try {
    repos = await getJSON("/repos");
  } catch (e) {
    toast(`No se pudo cargar la actividad: ${e.message}`, { type: "error" });
    return;
  }

  const filas = repos.length ? await traer(repos) : [];

  const firma = JSON.stringify([vista, filas.map((f) => [f.id, f.estado])]);
  if (firma !== ultimaFirma) {
    ultimaFirma = firma;
    pintar(filas);
  }

  clearTimeout(pollTimer);
  if (filas.some((f) => EN_CURSO.has(f.estado))) pollTimer = setTimeout(refrescarActividad, POLL_MS);
}

function pintar(filas) {
  const columnas = COLUMNAS[vista];
  const thead = document.querySelector("#actividadTable thead");
  thead.replaceChildren(
    el("tr", {}, columnas.map((c) => el("th", { text: c, attrs: { scope: "col" } })))
  );

  const tbody = document.querySelector("#actividadTable tbody");
  if (!filas.length) vacio(tbody, columnas.length);
  else tbody.replaceChildren(...filas.map(vista === "jobs" ? filaJob : filaRevision));
}

function cambiarVista(nueva) {
  if (vista === nueva) return;
  vista = nueva;
  ultimaFirma = "";
  $("actTabJobs").setAttribute("aria-pressed", String(nueva === "jobs"));
  $("actTabRevs").setAttribute("aria-pressed", String(nueva === "revisiones"));
  refrescarActividad();
}

export function initActividad() {
  $("actTabJobs").addEventListener("click", () => cambiarVista("jobs"));
  $("actTabRevs").addEventListener("click", () => cambiarVista("revisiones"));
  pintar([]); // cabecera + estado vacío antes de la primera respuesta
  refrescarActividad();
}

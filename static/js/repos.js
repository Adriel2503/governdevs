// Panel de administración de fuentes: registro, listado, borrado y métricas.
// El polling es adaptativo — solo sigue consultando mientras haya repos en
// tránsito (registrado/indexando); si todos están "listo"/"error", se frena.

import { getJSON, postJSON, del, el } from "./api.js";
import { toast } from "./toast.js";

const POLL_MS = 4000;
let pollTimer = null;
let lastSignature = "";

const $ = (id) => document.getElementById(id);

function badge(status) {
  return el("span", { class: `badge ${status}`, text: status });
}

function skeletonRows(tbody) {
  tbody.replaceChildren();
  for (let i = 0; i < 2; i++) {
    const tr = el("tr", { class: "skeleton-row" });
    for (let c = 0; c < 5; c++) tr.append(el("td", {}, [el("span", { class: "skeleton" })]));
    tbody.append(tr);
  }
}

function emptyRow(tbody) {
  const td = el("td", { attrs: { colspan: "5" } }, [
    el("div", { class: "empty-state" }, [
      el("div", { class: "es-icon", text: "◇" }),
      el("p", { text: "Aún no hay fuentes registradas. Registra un repositorio por URL Git o ruta local para construir su grafo." }),
    ]),
  ]);
  tbody.replaceChildren(el("tr", {}, [td]));
}

function renderRow(repo) {
  const nodes = repo._nodes ?? "—";
  const edges = repo._edges ?? "—";

  const graphBtn = el("button", {
    class: "secondary btn-sm",
    text: "Ver grafo",
    attrs: { type: "button", "aria-label": `Ver grafo de ${repo.name}` },
    on: { click: () => window.open(`/repos/${encodeURIComponent(repo.name)}/graph-ui`, "_blank", "noopener") },
  });
  graphBtn.disabled = repo.status !== "listo";

  const removeBtn = el("button", {
    class: "ghost btn-sm",
    text: "Quitar",
    attrs: { type: "button", "aria-label": `Quitar ${repo.name}` },
    on: { click: () => removeRepo(repo.name) },
  });

  return el("tr", {}, [
    el("td", { class: "repo-name", text: repo.name }),
    el("td", {}, [badge(repo.status)]),
    el("td", { class: "num", text: nodes }),
    el("td", { class: "num", text: edges }),
    el("td", { class: "actions" }, [graphBtn, removeBtn]),
  ]);
}

async function withGraphCounts(repos) {
  // Enriquecemos en paralelo solo los repos "listo" con nodos/aristas del grafo.
  const ready = repos.filter((r) => r.status === "listo");
  await Promise.all(
    ready.map(async (r) => {
      try {
        const st = await getJSON(`/repos/${encodeURIComponent(r.name)}/status`);
        r._nodes = st.cbm_status?.nodes ?? st.cbm_status?.node_count;
        r._edges = st.cbm_status?.edges ?? st.cbm_status?.edge_count;
      } catch { /* status todavía no disponible */ }
    })
  );
  return repos;
}

export async function refreshRepos() {
  let repos;
  try {
    repos = await getJSON("/repos");
  } catch (e) {
    toast(`No se pudieron cargar los repositorios: ${e.message}`, { type: "error" });
    return;
  }

  $("repoCount").textContent = repos.length;
  $("readyCount").textContent = repos.filter((r) => r.status === "listo").length;
  $("indexingCount").textContent = repos.filter((r) => r.status === "indexando").length;

  await withGraphCounts(repos);

  // Evitamos re-render (y parpadeo) si nada cambió respecto al último ciclo.
  const signature = JSON.stringify(repos.map((r) => [r.name, r.status, r._nodes, r._edges]));
  const tbody = document.querySelector("#reposTable tbody");
  if (signature !== lastSignature) {
    lastSignature = signature;
    if (!repos.length) emptyRow(tbody);
    else tbody.replaceChildren(...repos.map(renderRow));
  }

  schedulePoll(repos);
}

function schedulePoll(repos) {
  const inFlight = repos.some((r) => r.status === "indexando" || r.status === "registrado");
  clearTimeout(pollTimer);
  if (inFlight) pollTimer = setTimeout(refreshRepos, POLL_MS);
}

async function removeRepo(name) {
  if (!window.confirm(`¿Quitar "${name}"? Se elimina su registro y su grafo indexado.`)) return;
  try {
    await del(`/repos/${encodeURIComponent(name)}`);
    toast(`"${name}" fue quitado.`);
    lastSignature = "";
    refreshRepos();
  } catch (e) {
    toast(`No se pudo quitar: ${e.message}`, { type: "error" });
  }
}

function bindForm() {
  $("registerForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const source = $("source").value.trim();
    const name = $("name").value.trim() || undefined;
    if (!source) return;
    const btn = e.submitter;
    if (btn) btn.disabled = true;
    try {
      const r = await postJSON("/repos", { source, name });
      toast(`"${r.name}" registrado. Indexando su grafo…`);
      $("source").value = "";
      $("name").value = "";
      lastSignature = "";
      refreshRepos();
    } catch (err) {
      toast(`No se pudo registrar: ${err.message}`, { type: "error" });
    } finally {
      if (btn) btn.disabled = false;
    }
  });
}

export function initRepos() {
  skeletonRows(document.querySelector("#reposTable tbody"));
  bindForm();
  refreshRepos();
}

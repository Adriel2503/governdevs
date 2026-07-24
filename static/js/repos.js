// Panel de administración de fuentes: registro, listado, borrado y métricas.
// El polling es adaptativo — solo sigue consultando mientras haya repos en
// tránsito (registrado/indexando); si todos están "listo"/"error", se frena.

import { getJSON, postJSON, del, el } from "./api.js";
import { toast } from "./toast.js";
import { alCambiarCredenciales } from "./credenciales.js";
import { refrescarActividad } from "./actividad.js";

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
    for (let c = 0; c < 7; c++) tr.append(el("td", {}, [el("span", { class: "skeleton" })]));
    tbody.append(tr);
  }
}

// Combina las dos señales que responden "¿esto está vivo?": si el push dispara
// solo (webhook) y hasta qué commit llegó el grafo.
function celdaSync(repo) {
  if (!repo.webhook_activo) {
    return el("td", {
      class: "muted",
      text: "⚠ sin webhook",
      attrs: { title: "Los cambios no se reindexan solos. Registrá el repo con una credencial que tenga permiso Webhooks: write." },
    });
  }
  const sha = repo.last_indexed_commit ? repo.last_indexed_commit.slice(0, 7) : "pendiente";
  return el("td", {
    class: "mono",
    text: `🔗 ${sha}`,
    attrs: { title: repo.last_synced_at ? `Última sincronización: ${repo.last_synced_at}` : "Webhook activo" },
  });
}

function emptyRow(tbody) {
  const td = el("td", { attrs: { colspan: "7" } }, [
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
    el("td", { text: repo.rama || "—" }),
    celdaSync(repo),
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
  const signature = JSON.stringify(
    repos.map((r) => [r.name, r.status, r._nodes, r._edges, r.rama, r.webhook_activo, r.last_indexed_commit])
  );
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

// El <select> se repuebla cada vez que cambia la lista de credenciales, para
// que registrar un repo justo después de cargar el token funcione sin recargar.
function poblarCredenciales(creds) {
  const sel = $("repoCredencial");
  if (!sel) return;
  const elegida = sel.value;

  const opciones = [
    el("option", { text: "Sin credencial (repo público, sin webhook)", attrs: { value: "" } }),
    ...creds.map((c) =>
      el("option", { text: c.github_login ? `${c.alias} (${c.github_login})` : c.alias, attrs: { value: c.id } })
    ),
  ];
  sel.replaceChildren(...opciones);

  if (creds.some((c) => c.id === elegida)) sel.value = elegida;
  else if (creds.length === 1) sel.value = creds[0].id; // si hay una sola, es la que va

  sel.disabled = creds.length === 0;
  if (!creds.length) sel.replaceChildren(el("option", { text: "Cargá una credencial arriba" }));
}

// El auto-registro del webhook degrada con gracia en el backend: el repo queda
// registrado aunque falle. Es la ÚNICA señal de si el CI/CD quedó activo, así
// que un fallo tiene que verse — no enterarse recién al hacer push en la demo.
function avisarWebhook(nombre, webhook) {
  if (!webhook || webhook.startsWith("registrado")) {
    toast(`"${nombre}" registrado. Indexando su grafo…`);
    return;
  }
  toast(`"${nombre}" registrado, pero SIN actualización automática — ${webhook}`, {
    type: "error",
    timeout: 9000,
  });
}

function bindForm() {
  $("registerForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const source = $("source").value.trim();
    const name = $("name").value.trim() || undefined;
    if (!source) return;

    const credential_id = $("repoCredencial").value || undefined;
    const rama = $("repoRama").value.trim() || "main";
    const watch_paths = $("repoWatch")
      .value.split(",")
      .map((p) => p.trim())
      .filter(Boolean);

    const btn = e.submitter;
    if (btn) btn.disabled = true;
    try {
      const r = await postJSON("/repos", { source, name, credential_id, rama, watch_paths });
      avisarWebhook(r.name, r.webhook);
      $("source").value = "";
      $("name").value = "";
      $("repoWatch").value = "";
      lastSignature = "";
      refreshRepos();
      refrescarActividad(); // el registro encola un job: que se vea al toque
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
  alCambiarCredenciales(poblarCredenciales);
  refreshRepos();
}

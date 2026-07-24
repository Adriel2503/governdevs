// Panel de auditoría de módulo (REST /audit). Requiere ANTHROPIC_API_KEY en el
// servidor; si está apagada (deploy por defecto), traducimos el 500 en un aviso
// claro en vez de un error críptico, y apuntamos al camino MCP.

import { postJSON, el } from "./api.js";
import { quitarPestana } from "./tabs.js";

const $ = (id) => document.getElementById(id);

function renderFinding(f) {
  const loc = f.linea ? `${f.archivo}:${f.linea}` : f.archivo;
  const sev = (f.severidad || "media").toLowerCase();
  const head = el("div", {}, [
    el("span", { class: `finding-sev ${sev}`, text: sev }),
    el("span", { class: "finding-loc", text: loc }),
    ...(f.regla_violada ? [document.createTextNode(" · "), el("span", { class: "finding-rule", text: f.regla_violada })] : []),
  ]);
  const item = el("div", { class: "finding" }, [head, el("p", { class: "finding-desc", text: f.descripcion || "" })]);
  if (f.fix_sugerido) item.append(el("span", { class: "finding-fix", text: `Fix sugerido: ${f.fix_sugerido}` }));
  return item;
}

function bindForm() {
  $("auditForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const repo = $("auditRepo").value.trim();
    const modulo = $("auditModulo").value.trim();
    const out = $("auditResult");
    if (!repo || !modulo) return;
    const btn = e.submitter;
    if (btn) btn.disabled = true;
    out.replaceChildren(el("p", { class: "muted", text: `Auditando el módulo “${modulo}”…` }));
    try {
      const r = await postJSON("/audit", { repo, modulo });
      const findings = r.findings || [];
      if (!findings.length) {
        out.replaceChildren(el("p", { class: "muted", text: "Sin hallazgos: el módulo cumple los lineamientos revisados." }));
        return;
      }
      out.replaceChildren(...findings.map(renderFinding));
    } catch (err) {
      const msg = String(err.message || "");
      if (err.status === 500 && /ANTHROPIC_API_KEY/i.test(msg)) {
        out.replaceChildren(
          el("div", { class: "notice" }, [
            el("strong", { text: "Auditoría por API deshabilitada en este entorno." }),
            document.createElement("br"),
            document.createTextNode("Este servidor corre sin ANTHROPIC_API_KEY. Ejecuta la auditoría desde tu agente (Claude Code) usando la herramienta MCP "),
            el("code", { text: "reunir_contexto_auditoria" }),
            document.createTextNode(", que entrega el mismo material (código + reglas) para que tu modelo razone."),
          ])
        );
      } else {
        out.replaceChildren(el("p", { class: "muted", text: `Error: ${msg}` }));
      }
    } finally {
      if (btn) btn.disabled = false;
    }
  });
}

// Antes esto dejaba la pestaña con el formulario deshabilitado y un aviso. En
// una demo eso es una pestaña que no cumple: se ve en la navegación, alguien la
// abre y se encuentra con nada. Sin ANTHROPIC_API_KEY la sacamos entera —
// vuelve sola en cuanto la variable esté puesta en el deploy. El camino MCP
// (`reunir_contexto_auditoria`) no depende de esto y sigue disponible.
export function aplicarCapacidades(caps) {
  if (caps?.auditoria !== false) return;
  quitarPestana("auditoria");
}

export function initAudit() {
  bindForm();
}

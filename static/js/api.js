// Cliente HTTP mínimo. Centraliza el fetch, parsea JSON y normaliza errores
// (extrae `detail` de las HTTPException de FastAPI para mensajes legibles).

export async function api(path, opts) {
  const res = await fetch(path, opts);
  const raw = await res.text();

  let data = null;
  if (raw) {
    try {
      data = JSON.parse(raw);
    } catch {
      data = raw; // respuesta no-JSON (raro, pero no reventamos)
    }
  }

  if (!res.ok) {
    const detail =
      data && typeof data === "object" && "detail" in data
        ? data.detail
        : typeof data === "string" && data
          ? data
          : res.statusText;
    const err = new Error(detail);
    err.status = res.status;
    throw err;
  }
  return data;
}

export const getJSON = (path) => api(path);

export const postJSON = (path, body) =>
  api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

export const del = (path) => api(path, { method: "DELETE" });

// Helper de creación de elementos (evita innerHTML → sin riesgo de XSS).
export function el(tag, opts = {}, children = []) {
  const node = document.createElement(tag);
  if (opts.class) node.className = opts.class;
  if (opts.text != null) node.textContent = String(opts.text);
  if (opts.attrs)
    for (const [k, v] of Object.entries(opts.attrs)) {
      // Ignoramos null/undefined/false para que atributos booleanos como
      // `disabled` no se seteen por accidente (setAttribute los crearía igual).
      if (v == null || v === false) continue;
      node.setAttribute(k, v === true ? "" : v);
    }
  if (opts.on) for (const [evt, fn] of Object.entries(opts.on)) node.addEventListener(evt, fn);
  for (const child of children) node.append(child);
  return node;
}

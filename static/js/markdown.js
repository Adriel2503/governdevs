// Render de Markdown a nodos del DOM.
//
// Por qué a mano y no una librería: el frontend no tiene build step, así que
// sumar marked/markdown-it significa vendorizar y versionar un archivo grande.
// Y sobre todo, acá NO se puede usar innerHTML: el contenido son .md que sube
// cualquiera por la interfaz. Un renderer que emite nodos no puede inyectar
// HTML *por construcción*, no porque alguien se acuerde de sanitizar.
//
// El alcance salió de medir la wiki real de Real Plaza, no de la especificación
// de CommonMark: 235 tramos de código inline, 164 delimitadores de bloque, 381
// items de lista, 134 encabezados con #, 76 encabezados subrayados (Setext),
// 118 negritas, 57 enlaces, 9 imágenes, 8 filas de tabla, 1 blockquote.
//
// Regla de oro: lo que no se reconoce cae a párrafo. Estos documentos son la
// norma de arquitectura — es preferible mostrarlos sin formato que perder una
// línea. En el modal queda además el botón "Ver original".

import { el } from "./api.js";

const RE_ITEM = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/;
const RE_FENCE = /^\s*(`{3,}|~{3,})\s*([\w+#.-]*)\s*$/;
const RE_ENCABEZADO = /^(#{1,6})\s+(.*)$/;
const RE_HR = /^\s*(-{3,}|_{3,}|\*{3,})\s*$/;
const RE_SEPARADOR_TABLA = /^\s*\|?[\s:|-]*-[\s:|-]*$/;
// Macros de Azure DevOps: `[[_TOC_]]` le pide a la wiki que inserte un índice.
// Acá no significa nada (13 apariciones), y mostrarlo crudo es ruido.
const RE_MACRO_WIKI = /^\s*\[\[_[A-Z]+_\]\]\s*$/;

// Inline. El código va PRIMERO en la alternancia para que un `*` o un `_`
// dentro de un fragmento de código no se lea como énfasis.
//
// Se soporta *cursiva* pero NO _cursiva_ a propósito: estos documentos están
// llenos de identificadores C# como `_repository` o `snake_case`, y tratar el
// guion bajo como énfasis los rompía. El costo es no reconocer una convención
// que la wiki no usa.
const RE_INLINE =
  /(`+)([\s\S]+?)\1|!\[([^\]]*)\]\(([^)]*)\)|\[([^\]]+)\]\(([^)]*)\)|\*\*([\s\S]+?)\*\*|\*([^*\n]+)\*/g;

const esHttp = (u) => /^https?:\/\//i.test(u);

// Solo http(s) se convierte en <a>. Todo lo demás — javascript:, data:, y las
// rutas relativas de la wiki — no se vuelve navegable.
function enlace(texto, href) {
  const u = (href || "").trim();
  if (esHttp(u)) {
    return el("a", {
      class: "md-a",
      text: texto,
      attrs: { href: u, target: "_blank", rel: "noopener noreferrer" },
    });
  }
  // Los enlaces internos apuntan a otras páginas de la wiki en Azure DevOps. El
  // hub indexa el contenido, no sirve la wiki, así que seguirlos daría un 404:
  // queda el texto, con el destino visible en el tooltip.
  return el("span", {
    class: "md-a-interno",
    text: texto,
    attrs: { title: u ? `Enlace interno de la wiki: ${u}` : "Enlace sin destino" },
  });
}

function imagen(alt, src) {
  const u = (src || "").trim();
  if (esHttp(u)) {
    return el("img", { class: "md-img", attrs: { src: u, alt: alt || "", loading: "lazy" } });
  }
  // Los adjuntos de Azure DevOps viven en /.attachments/ y la importación se
  // queda SOLO con los .md, así que el archivo no existe en el hub. Un <img>
  // roto no dice nada; esto dice qué falta y cómo se llamaba.
  const nombre = alt || u.split("/").pop() || "imagen";
  return el("span", {
    class: "md-img-falta",
    text: `Imagen no incluida en la importación: ${nombre}`,
  });
}

/** Escribe `texto` dentro de `nodo` resolviendo el formato inline.
 *
 * Usa matchAll y NO exec: esta función se llama a sí misma para el contenido de
 * **negrita** y *cursiva*, y `exec` sobre un regex /g compartido guarda el
 * avance en el propio objeto — la llamada interna le pisaba el lastIndex a la
 * externa y el bucle no terminaba nunca. matchAll clona el regex, así que cada
 * nivel de recursión lleva su propio recorrido. */
function inline(nodo, texto) {
  let ultimo = 0;
  for (const m of texto.matchAll(RE_INLINE)) {
    if (m.index > ultimo) nodo.append(document.createTextNode(texto.slice(ultimo, m.index)));

    if (m[2] != null) nodo.append(el("code", { class: "md-code-inline", text: m[2] }));
    else if (m[4] != null) nodo.append(imagen(m[3], m[4]));
    else if (m[6] != null) nodo.append(enlace(m[5], m[6]));
    else if (m[7] != null) inline(nodo.appendChild(el("strong")), m[7]);
    else if (m[8] != null) inline(nodo.appendChild(el("em")), m[8]);

    ultimo = m.index + m[0].length;
  }
  if (ultimo < texto.length) nodo.append(document.createTextNode(texto.slice(ultimo)));
  return nodo;
}


// --- Bloques ---------------------------------------------------------------
// Cada `consumir*` recibe el índice donde empieza su bloque y devuelve
// [nodo, indiceSiguiente]. Así el bucle principal nunca tiene que adivinar
// cuántas líneas consumió.

function consumirCodigo(lineas, i) {
  const [, cerca, lenguaje] = lineas[i].match(RE_FENCE);
  const cuerpo = [];
  let j = i + 1;
  // Cierra con el mismo carácter; si el documento nunca cierra el bloque, se
  // toma hasta el final en vez de descartarlo.
  while (j < lineas.length && !new RegExp(`^\\s*${cerca[0]}{${cerca.length},}\\s*$`).test(lineas[j])) {
    cuerpo.push(lineas[j]);
    j++;
  }
  const pre = el("pre", { class: "md-pre" }, [el("code", { text: cuerpo.join("\n") })]);
  if (lenguaje) pre.setAttribute("data-lenguaje", lenguaje);
  return [pre, j + 1];
}

function celdas(linea) {
  return linea.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
}

function consumirTabla(lineas, i) {
  const cabecera = celdas(lineas[i]);
  const thead = el("thead", {}, [
    el("tr", {}, cabecera.map((c) => inline(el("th", { attrs: { scope: "col" } }), c))),
  ]);
  const tbody = el("tbody");
  let j = i + 2; // salta la fila de separadores
  while (j < lineas.length && /^\s*\|/.test(lineas[j])) {
    tbody.append(el("tr", {}, celdas(lineas[j]).map((c) => inline(el("td"), c))));
    j++;
  }
  return [el("div", { class: "table-wrap" }, [el("table", { class: "md-tabla" }, [thead, tbody])]), j];
}

function consumirCita(lineas, i) {
  const dentro = [];
  let j = i;
  while (j < lineas.length && /^\s*>/.test(lineas[j])) {
    dentro.push(lineas[j].replace(/^\s*>\s?/, ""));
    j++;
  }
  return [el("blockquote", { class: "md-cita" }, [bloques(dentro)]), j];
}

function consumirLista(lineas, i) {
  const indentBase = lineas[i].match(RE_ITEM)[1].length;
  const ordenada = /\d/.test(lineas[i].match(RE_ITEM)[2]);
  const lista = el(ordenada ? "ol" : "ul", { class: "md-lista" });
  let j = i;
  let item = null;

  while (j < lineas.length) {
    const linea = lineas[j];

    if (!linea.trim()) {
      // Una línea vacía no cierra la lista si lo que sigue todavía le pertenece
      // (otro item, o texto/código indentado bajo el item actual).
      const sig = lineas[j + 1];
      if (!sig || (!RE_ITEM.test(sig) && !/^\s{2,}\S/.test(sig))) break;
      j++;
      continue;
    }

    const m = linea.match(RE_ITEM);
    if (m) {
      const indent = m[1].length;
      if (indent < indentBase) break; // pertenece a un nivel de más arriba
      if (indent > indentBase) {
        const [sub, siguiente] = consumirLista(lineas, j);
        (item || lista.appendChild(el("li"))).append(sub);
        j = siguiente;
        continue;
      }
      item = inline(el("li"), m[3]);
      lista.append(item);
      j++;
      continue;
    }

    // Un bloque de código indentado dentro de un item: hay que consumirlo acá,
    // porque tratarlo como texto de continuación destruiría el formato.
    if (item && RE_FENCE.test(linea)) {
      const [pre, siguiente] = consumirCodigo(lineas, j);
      item.append(pre);
      j = siguiente;
      continue;
    }

    // Texto de continuación del item.
    if (item && /^\s+\S/.test(linea)) {
      item.append(document.createTextNode(" "));
      inline(item, linea.trim());
      j++;
      continue;
    }

    break;
  }
  return [lista, j];
}

function consumirParrafo(lineas, i) {
  const partes = [];
  let j = i;
  // Corta en la línea vacía o en cualquier cosa que arranque otro bloque.
  while (
    j < lineas.length &&
    lineas[j].trim() &&
    !RE_ENCABEZADO.test(lineas[j]) &&
    !RE_FENCE.test(lineas[j]) &&
    !RE_HR.test(lineas[j]) &&
    !RE_ITEM.test(lineas[j]) &&
    !/^\s*>/.test(lineas[j])
  ) {
    partes.push(lineas[j].trim());
    j++;
  }
  return [inline(el("p", { class: "md-p" }), partes.join(" ")), j];
}

function bloques(lineas) {
  const frag = document.createDocumentFragment();
  let i = 0;

  while (i < lineas.length) {
    const linea = lineas[i];

    if (!linea.trim() || RE_MACRO_WIKI.test(linea)) { i++; continue; }

    if (RE_FENCE.test(linea)) {
      const [nodo, siguiente] = consumirCodigo(lineas, i);
      frag.append(nodo);
      i = siguiente;
      continue;
    }

    const h = linea.match(RE_ENCABEZADO);
    if (h) {
      // Se baja un nivel: el <h3> del modal ya es el título del documento, así
      // que un `#` del .md no puede volver a ser h1 sin romper la jerarquía.
      const nivel = Math.min(h[1].length + 3, 6);
      frag.append(inline(el(`h${nivel}`, { class: "md-h" }), h[2].trim()));
      i++;
      continue;
    }

    // Título estilo Setext: el texto va SUBRAYADO con === o ---. Hay que
    // mirarlo antes que la regla horizontal, porque comparten el guion: de las
    // 70 lineas de `---` de esta wiki, 68 son esto. Tratarlas como <hr> partia
    // 68 titulos de seccion en un parrafo suelto y una raya debajo.
    const subrayado = (lineas[i + 1] || "").match(/^\s*(=+|-+)\s*$/);
    if (subrayado && !RE_ITEM.test(linea) && !RE_HR.test(linea)) {
      frag.append(inline(el(subrayado[1][0] === "=" ? "h4" : "h5", { class: "md-h" }), linea.trim()));
      i += 2;
      continue;
    }

    if (RE_HR.test(linea)) { frag.append(el("hr", { class: "md-hr" })); i++; continue; }

    if (/^\s*\|/.test(linea) && RE_SEPARADOR_TABLA.test(lineas[i + 1] || "")) {
      const [nodo, siguiente] = consumirTabla(lineas, i);
      frag.append(nodo);
      i = siguiente;
      continue;
    }

    if (/^\s*>/.test(linea)) {
      const [nodo, siguiente] = consumirCita(lineas, i);
      frag.append(nodo);
      i = siguiente;
      continue;
    }

    if (RE_ITEM.test(linea)) {
      const [nodo, siguiente] = consumirLista(lineas, i);
      frag.append(nodo);
      i = siguiente;
      continue;
    }

    const [nodo, siguiente] = consumirParrafo(lineas, i);
    frag.append(nodo);
    // Blindaje: si un consumidor no avanzara, el bucle quedaría colgado y la
    // pestaña se congela. Preferimos perder una línea antes que eso.
    i = siguiente > i ? siguiente : i + 1;
  }

  return frag;
}

/** Markdown -> DocumentFragment. Nunca lanza: el peor caso es texto plano. */
export function renderMarkdown(texto) {
  return bloques(String(texto ?? "").replace(/\r\n?/g, "\n").split("\n"));
}

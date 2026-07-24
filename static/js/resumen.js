// Pestaña de entrada: los números que responden "¿qué está gobernando esto?".
//
// Es la pantalla que queda proyectada mientras uno habla, así que prioriza
// legibilidad sobre detalle: cuatro cifras grandes, cada una con una nota chica
// que da el contexto sin obligar a leer una tabla.

import { getJSON } from "./api.js";
import { EVENTO_CAMBIO } from "./tabs.js";

const $ = (id) => document.getElementById(id);

// Separador de miles: "17482" dicho de un vistazo es "diecisiete mil", no
// "uno siete cuatro ocho dos".
const num = (n) => (typeof n === "number" ? n.toLocaleString("es-PE") : "—");

const plural = (n, singular, plural_) => `${n} ${n === 1 ? singular : plural_}`;

export async function refrescarResumen() {
  let r;
  try {
    r = await getJSON("/resumen");
  } catch {
    return; // sin toast: es una pantalla de lectura, no vale interrumpir por esto
  }

  $("kpiRepos").textContent = num(r.repos);
  $("kpiReposNota").textContent = r.repos
    ? `${r.repos_listos} con grafo listo`
    : "Registrá el primero en «Fuentes»";

  $("kpiNodos").textContent = num(r.nodos);
  $("kpiAristas").textContent = r.aristas ? `${num(r.aristas)} relaciones entre ellos` : "";

  $("kpiReglas").textContent = num(r.lineamientos);
  $("kpiCapas").textContent = r.lineamientos
    ? `de ${plural(r.fuentes, "fuente importada", "fuentes importadas")}`
    : "Importalos en «Lineamientos»";

  $("kpiRevisiones").textContent = num(r.revisiones);
  // El número de reindexados es la prueba de que el ciclo automático corre.
  $("kpiReindexados").textContent = r.reindexados
    ? plural(r.reindexados, "reindexado automático", "reindexados automáticos")
    : "Aparecen al abrir un pull request";
}

export function initResumen() {
  // Al volver a la pestaña se recalcula: entre medio pudo entrar un push.
  document.addEventListener(EVENTO_CAMBIO, (e) => {
    if (e.detail.id === "resumen") refrescarResumen();
  });
  refrescarResumen();
}

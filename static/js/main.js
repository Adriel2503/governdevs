// Punto de entrada: cablea los paneles. El DOM ya está listo porque este
// módulo se carga con `defer` implícito (type="module") al final del <body>.
//
// Las capacidades se consultan UNA vez y se reparten: el deploy decide qué se
// puede ofrecer (la auditoría necesita ANTHROPIC_API_KEY, sincronizar la wiki
// necesita los .md montados). Mejor deshabilitar con un motivo que ofrecer un
// botón que revienta.

import { getJSON } from "./api.js";
import { initTabs } from "./tabs.js";
import { initResumen } from "./resumen.js";
import { initCredenciales } from "./credenciales.js";
import { initRepos } from "./repos.js";
import { initActividad } from "./actividad.js";
import { initReglas, aplicarCapacidades as reglasCapacidades } from "./reglas.js";
import { initAudit, aplicarCapacidades as auditCapacidades } from "./audit.js";
import { initImportar } from "./importar.js";
import { initFuentes } from "./fuentes.js";

// Las pestañas primero: definen qué panel está visible, y de eso dependen los
// módulos que hacen polling para decidir si arrancan o se quedan quietos.
initTabs();

initResumen();
initCredenciales();
initRepos();
initActividad();
initReglas();
initAudit();
initImportar();
initFuentes();

// Si /capacidades falla, se asume todo disponible: preferimos un botón que
// pueda fallar antes que esconder funciones por un problema de red.
getJSON("/capacidades")
  .then((caps) => {
    reglasCapacidades(caps);
    auditCapacidades(caps);
  })
  .catch(() => {});

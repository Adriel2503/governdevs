// Punto de entrada: cablea los paneles. El DOM ya está listo porque este
// módulo se carga con `defer` implícito (type="module") al final del <body>.
//
// Las capacidades se consultan UNA vez: el deploy decide qué se puede ofrecer
// (hoy solo la auditoría, que necesita ANTHROPIC_API_KEY). Mejor no mostrar la
// pestaña que ofrecer una que revienta.

import { getJSON } from "./api.js";
import { initTabs } from "./tabs.js";
import { initResumen } from "./resumen.js";
import { initCredenciales } from "./credenciales.js";
import { initRepos } from "./repos.js";
import { initActividad } from "./actividad.js";
import { initReglas } from "./reglas.js";
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

// Si /capacidades falla, se asume todo disponible: preferimos una pestaña que
// pueda fallar antes que esconder funciones por un problema de red.
getJSON("/capacidades")
  .then(auditCapacidades)
  .catch(() => {});

// Punto de entrada: cablea los paneles. El DOM ya está listo porque este
// módulo se carga con `defer` implícito (type="module") al final del <body>.
//
// Las capacidades se consultan UNA vez y se reparten: el deploy decide qué se
// puede ofrecer (la auditoría necesita ANTHROPIC_API_KEY, sincronizar la wiki
// necesita los .md montados). Mejor deshabilitar con un motivo que ofrecer un
// botón que revienta.

import { getJSON } from "./api.js";
import { initCredenciales } from "./credenciales.js";
import { initRepos } from "./repos.js";
import { initActividad } from "./actividad.js";
import { initReglas, aplicarCapacidades as reglasCapacidades } from "./reglas.js";
import { initAudit, aplicarCapacidades as auditCapacidades } from "./audit.js";
import { initImportar } from "./importar.js";

initCredenciales();
initRepos();
initActividad();
initReglas();
initAudit();
initImportar();

// Si /capacidades falla, se asume todo disponible: preferimos un botón que
// pueda fallar antes que esconder funciones por un problema de red.
getJSON("/capacidades")
  .then((caps) => {
    reglasCapacidades(caps);
    auditCapacidades(caps);
  })
  .catch(() => {});

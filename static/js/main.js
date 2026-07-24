// Punto de entrada: cablea los tres paneles. El DOM ya está listo porque este
// módulo se carga con `defer` implícito (type="module") al final del <body>.

import { initRepos } from "./repos.js";
import { initReglas } from "./reglas.js";
import { initAudit } from "./audit.js";
import { initImportar } from "./importar.js";

initRepos();
initReglas();
initAudit();
initImportar();

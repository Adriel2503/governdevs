// Navegación por pestañas.
//
// Antes la página era un scroll único de ~2000px con seis paneles apilados y
// CUATRO tablas vacías visibles a la vez. Para quien la ve por primera vez eso
// no dice "listo para configurar", dice "acá no hay nada".
//
// Cada pestaña es UNA tarea. Decisiones que importan:
//
//   - El estado vive en el hash (#actividad). Se puede compartir el link, sobrevive
//     un F5, y en una demo permite abrir directo la pantalla que uno quiere mostrar
//     sin hacer clics delante de nadie.
//   - Al cambiar de pestaña se avisa por evento. Los paneles que hacen polling lo
//     frenan mientras están ocultos y lo retoman al volver: no tiene sentido pedirle
//     al servidor cada 4 segundos algo que nadie está mirando.
//   - Teclado completo (flechas, Home, End) y roles ARIA, que es lo que separa unas
//     pestañas de verdad de unos botones que esconden divs.

const $$ = (sel) => [...document.querySelectorAll(sel)];

export const EVENTO_CAMBIO = "hub:pestana";

function paneles() {
  return $$('[role="tabpanel"]');
}

function pestanas() {
  return $$('[role="tab"]');
}

// "tab-actividad" -> "actividad"
const idCorto = (tab) => tab.id.replace(/^tab-/, "");

function activar(id, { foco = false, actualizarHash = true } = {}) {
  const tabs = pestanas();
  const destino = tabs.find((t) => idCorto(t) === id) || tabs[0];
  if (!destino) return;

  for (const t of tabs) {
    const activa = t === destino;
    t.setAttribute("aria-selected", String(activa));
    t.tabIndex = activa ? 0 : -1; // solo la activa entra en el orden de tabulación
  }
  for (const p of paneles()) {
    p.hidden = p.id !== destino.getAttribute("aria-controls");
  }

  if (foco) destino.focus();
  if (actualizarHash) history.replaceState(null, "", `#${idCorto(destino)}`);

  // Quien haga polling escucha esto para frenar o retomar.
  document.dispatchEvent(new CustomEvent(EVENTO_CAMBIO, { detail: { id: idCorto(destino) } }));
}

export const pestanaActiva = () =>
  idCorto(pestanas().find((t) => t.getAttribute("aria-selected") === "true") || pestanas()[0]);

// Para que un panel sepa si vale la pena seguir refrescándose.
export function panelVisible(idPanel) {
  const p = document.getElementById(idPanel);
  return !!p && !p.hidden;
}

// Una pestaña que lleva a un formulario deshabilitado es peor que no tenerla:
// ocupa un lugar en la navegación y no cumple. Si el deploy no soporta esa
// función, la sacamos del DOM (vuelve sola cuando la capacidad se enciende).
export function quitarPestana(id) {
  const tab = document.getElementById(`tab-${id}`);
  if (!tab) return;
  const panel = document.getElementById(tab.getAttribute("aria-controls"));
  const eraLaActiva = tab.getAttribute("aria-selected") === "true";

  tab.remove();
  panel?.remove();
  if (eraLaActiva) activar(pestanaActiva()); // si no, quedaría sin ninguna visible
}

function bindTeclado(tabs) {
  const MOVIMIENTOS = { ArrowRight: 1, ArrowLeft: -1 };
  for (const t of tabs) {
    t.addEventListener("keydown", (e) => {
      // Se releen en cada tecla: una capacidad apagada puede haber quitado una
      // pestaña después del bind, y navegar hacia una que ya no existe no anda.
      const vivas = pestanas();
      if (e.key === "Home" || e.key === "End") {
        e.preventDefault();
        activar(idCorto(e.key === "Home" ? vivas[0] : vivas[vivas.length - 1]), { foco: true });
        return;
      }
      const paso = MOVIMIENTOS[e.key];
      if (!paso) return;
      e.preventDefault();
      const i = vivas.indexOf(t);
      const siguiente = vivas[(i + paso + vivas.length) % vivas.length]; // circular
      activar(idCorto(siguiente), { foco: true });
    });
  }
}

export function initTabs() {
  const tabs = pestanas();
  if (!tabs.length) return;

  for (const t of tabs) t.addEventListener("click", () => activar(idCorto(t)));
  bindTeclado(tabs);

  // Permite volver con el botón atrás del navegador.
  window.addEventListener("hashchange", () =>
    activar(location.hash.slice(1), { actualizarHash: false })
  );

  // Un hash inválido cae en la primera pestaña, no en una página en blanco.
  activar(location.hash.slice(1) || idCorto(tabs[0]), { actualizarHash: false });
}

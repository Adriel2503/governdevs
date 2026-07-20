#!/bin/sh
# Entrypoint del contenedor.
#
# El binario cbm sirve su UI de grafo 3D atada a 127.0.0.1 (loopback) y no
# admite bindear a otra interfaz. Dentro de un contenedor eso la vuelve
# inalcanzable: Docker/Dokploy publican el puerto hacia eth0, no hacia loopback.
#
# Solución: un relay TCP crudo. socat escucha en todas las interfaces
# (0.0.0.0:${CBM_UI_EXTERNAL_PORT}) y reenvía a la UI de cbm en loopback
# (127.0.0.1:${CBM_UI_PORT}). Al ser TCP crudo, retransmite HTTP, SSE y
# WebSockets de forma transparente. cbm lo arranca el lifespan de la app en un
# puerto interno distinto (9750) para no chocar con el listener del relay.
set -e

EXT="${CBM_UI_EXTERNAL_PORT:-9749}"
INT="${CBM_UI_PORT:-9750}"

socat "TCP-LISTEN:${EXT},fork,reuseaddr" "TCP:127.0.0.1:${INT}" &

exec "$@"

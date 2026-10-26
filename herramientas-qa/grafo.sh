#!/usr/bin/env bash
# Grafo de código y dependencias (graphify) en un contenedor: no requiere Python
# en el equipo ni claves de API (solo AST, costo 0). Salida en graphify-out/
# (graph.json, graph.html, GRAPH_REPORT.md); no se versiona.
# Uso (desde la raíz del repositorio):
#   bash herramientas-qa/grafo.sh                         # construir o actualizar el grafo
#   bash herramientas-qa/grafo.sh query "flujo de login"  # subgrafo para una pregunta
#   bash herramientas-qa/grafo.sh path "SSOService" "Usuario"
#   bash herramientas-qa/grafo.sh explain "install_rls"
# Qué se excluye (secretos, respaldos, node_modules…): .graphifyignore
set -euo pipefail
export MSYS_NO_PATHCONV=1
AQUI="$(cd "$(dirname "$0")" && pwd)"
RAIZ="$(cd "$AQUI/.." && (pwd -W 2>/dev/null || pwd))"
IMAGEN=inventario-graphify:0.9.96

docker image inspect "$IMAGEN" >/dev/null 2>&1 \
  || docker build -q -t "$IMAGEN" "$(cd "$AQUI/graphify" && (pwd -W 2>/dev/null || pwd))" >/dev/null
if [ $# -eq 0 ]; then
  set -- update .
fi
docker run --rm -v "$RAIZ:/repo" "$IMAGEN" "$@"

#!/usr/bin/env bash
# Suite del backend de la versión PostgreSQL contra un PostgreSQL 16 desechable,
# todo en contenedores (no requiere Python en el equipo).
# Uso:
#   bash herramientas-qa/pruebas_backend.sh                 # toda la suite
#   bash herramientas-qa/pruebas_backend.sh -k alcance      # argumentos extra para pytest
#   docker rm -f inv_pgtest && docker network rm inv_pgtest # descartar la base de pruebas
set -euo pipefail
export MSYS_NO_PATHCONV=1
AQUI="$(cd "$(dirname "$0")" && pwd)"
BACKEND="$(cd "$AQUI/../inventarioTI-backend" && (pwd -W 2>/dev/null || pwd))"
NOMBRE=inv_pgtest
RED=inv_pgtest

docker network inspect "$RED" >/dev/null 2>&1 || docker network create "$RED" >/dev/null
if [ -z "$(docker ps -q -f "name=^${NOMBRE}$")" ]; then
  echo "Levantando PostgreSQL 16 de pruebas ($NOMBRE)…"
  docker run -d --rm --name "$NOMBRE" --network "$RED" -e POSTGRES_USER=t -e POSTGRES_PASSWORD=t \
    -e POSTGRES_DB=t postgres:16-alpine >/dev/null
fi
for _ in $(seq 60); do
  docker exec "$NOMBRE" pg_isready -U t -d t >/dev/null 2>&1 && break
  sleep 1
done

echo "Construyendo la imagen de pruebas…"
docker build -q --target runtime -t inventario-backend-pg-test "$BACKEND" >/dev/null
docker run --rm --network "$RED" --entrypoint python \
  -e TEST_DATABASE_URL="postgresql+psycopg://t:t@${NOMBRE}:5432/t" -e PYTHONIOENCODING=utf-8 \
  inventario-backend-pg-test -m pytest tests/ -q -p no:cacheprovider -p no:warnings -p no:logging "$@"

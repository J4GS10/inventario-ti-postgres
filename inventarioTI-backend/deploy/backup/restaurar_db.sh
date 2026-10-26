#!/bin/sh
# Restaura un respaldo de PostgreSQL. DESTRUCTIVO: reemplaza la base actual.
# Uso (desde deploy/):
#   docker compose run --rm --no-deps --entrypoint /bin/sh -e CONFIRMAR=SI backup_db \
#     /backup/restaurar_db.sh /backups/db/inventario_AAAAMMDD_HHMMSS.dump
# Detenga antes el backend (docker compose stop backend) para que nadie escriba.
# --entrypoint es obligatorio: el de backup_db es respaldo_db.sh y, sin él, se
# ejecutaría un respaldo en vez de la restauración. En Git Bash: MSYS_NO_PATHCONV=1.
set -eu
archivo="${1:?Indique el archivo .dump a restaurar}"
[ "${CONFIRMAR:-}" = "SI" ] || { echo "Defina CONFIRMAR=SI para restaurar (reemplaza la base actual)."; exit 1; }
pg_restore --list "$archivo" > /dev/null
echo "Restaurando $archivo en $PGDATABASE…"
pg_restore -h "$PGHOST" -U "$PGUSER" -d "$PGDATABASE" --clean --if-exists --no-owner "$archivo"
echo "Restauración terminada. Reinicie el backend: docker compose up -d backend"

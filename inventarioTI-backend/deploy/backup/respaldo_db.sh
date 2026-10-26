#!/bin/sh
# Respaldo periódico de PostgreSQL (servicio `backup_db` del compose).
# - pg_dump en formato custom (restaurable con pg_restore, comprimido).
# - Un archivo por ejecución: /backups/db/inventario_AAAAMMDD_HHMMSS.dump
# - Borra los respaldos con más de BACKUP_RETENTION_DAYS días.
# - Tras cada respaldo verifica que el archivo sea legible (pg_restore --list).
set -eu
DEST=/backups/db
INTERVALO_HORAS="${BACKUP_INTERVAL_HOURS:-24}"
RETENCION_DIAS="${BACKUP_RETENTION_DAYS:-14}"
mkdir -p "$DEST"

respaldar() {
  archivo="$DEST/inventario_$(date -u +%Y%m%d_%H%M%S).dump"
  echo "[backup_db] $(date -u +%FT%TZ) iniciando -> $archivo"
  if pg_dump -h "$PGHOST" -U "$PGUSER" -d "$PGDATABASE" -Fc -Z 6 -f "$archivo.tmp"; then
    mv "$archivo.tmp" "$archivo"
    if pg_restore --list "$archivo" > /dev/null; then
      echo "[backup_db] OK $(du -h "$archivo" | cut -f1)"
    else
      echo "[backup_db] ERROR: el respaldo no es legible" >&2
    fi
  else
    rm -f "$archivo.tmp"
    echo "[backup_db] ERROR: pg_dump falló" >&2
  fi
  find "$DEST" -name 'inventario_*.dump' -mtime +"$RETENCION_DIAS" -delete
}

if [ "${1:-}" = "--una-vez" ]; then
  respaldar
  exit 0
fi
while true; do
  respaldar
  sleep $((INTERVALO_HORAS * 3600))
done

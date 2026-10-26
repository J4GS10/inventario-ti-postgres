#!/bin/sh
# Respaldo periódico de los adjuntos (servicio `backup_minio` del compose).
# Espeja el bucket en /backups/minio/<bucket>. Con --remove no se borra nada
# del respaldo: un archivo eliminado por error en el sistema sigue aquí.
set -eu
INTERVALO_HORAS="${BACKUP_INTERVAL_HOURS:-24}"
BUCKET="${S3_BUCKET:-lombardi-adjuntos}"
mc alias set origen http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" > /dev/null
mkdir -p "/backups/minio/$BUCKET"

respaldar() {
  echo "[backup_minio] $(date -u +%FT%TZ) espejando $BUCKET"
  if mc mirror --overwrite --preserve "origen/$BUCKET" "/backups/minio/$BUCKET"; then
    echo "[backup_minio] OK"
  else
    echo "[backup_minio] ERROR: mc mirror falló" >&2
  fi
}

if [ "${1:-}" = "--una-vez" ]; then
  respaldar
  exit 0
fi
while true; do
  respaldar
  sleep $((INTERVALO_HORAS * 3600))
done

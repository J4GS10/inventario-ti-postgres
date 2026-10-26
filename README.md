# Inventario-TI — versión PostgreSQL 16

Sistema de gestión de activos de TI para Lombardi sobre **PostgreSQL 16**.

Repositorio oficial: **https://github.com/J4GS10/inventario-ti-postgres**
(rama `main`). Es exclusivo de PostgreSQL: `inventarioTI-backend/tests/test_aislamiento_version.py`
impide introducir drivers, migraciones o SQL de otro motor.

| Carpeta | Contenido |
|---|---|
| `inventarioTI-backend/` | API FastAPI + SQLAlchemy async + Alembic sobre PostgreSQL 16. Kit de despliegue en `deploy/` |
| `inventarioTI-frontend/` | SPA React 19 + TypeScript + MUI, i18n ES/EN/IT |
| `herramientas-qa/` | Suite del backend en contenedores, flujos E2E y grafo de código (graphify) |

Todo el stack es software libre y corre **localmente**: sin servicios en la nube ni
telemetría. Guía detallada de variables: [`inventarioTI-backend/DEPLOYMENT.md`](inventarioTI-backend/DEPLOYMENT.md).

---

## Arquitectura de despliegue

```
            80/443 (único puerto publicado)
   red «salida»     Caddy ──► Nginx (SPA) ──► Backend ──► SMTP / AD / SSO
                                                 │
   red «internal» ───────────────────────────────┼──────────── (internal: true, sin salida)
        PostgreSQL primario ──streaming──► db_report (réplica en caliente, lecturas)
        Redis · MinIO · backup_db (pg_dump) · backup_minio (mc mirror)
                                                 │
                                            BACKUP_DIR ──► backup_replica (rclone) ──► disco / NAS / remoto
```

PostgreSQL (5432), Redis y MinIO **no publican puertos** en el host.

---

## 1. Requisitos previos de infraestructura

| Recurso | Mínimo | Recomendado |
|---|---|---|
| CPU | 2 núcleos | 4 núcleos |
| RAM | 4 GB libres | 8 GB |
| Disco | 20 GB SSD | 40 GB SSD + otro disco físico o NAS para la réplica |
| Energía | — | UPS con apagado ordenado |

**Docker Engine + Compose v2 (Linux):**

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER" && newgrp docker
sudo systemctl enable --now docker containerd
docker compose version
```

**Windows 11:** Docker Desktop (WSL 2) con *Start Docker Desktop when you sign in*.

**rclone:** corre en el contenedor `backup_replica` (`rclone/rclone:1.68.2`); no hace
falta instalarlo. Para crear un remoto (SFTP, SMB, S3 compatible):

```bash
cd inventarioTI-backend/deploy
docker run --rm -it -v "$PWD/backup:/config/rclone" rclone/rclone:1.68.2 config   # crea backup/rclone.conf (no versionado)
```

---

## 2. Configuración del entorno

```bash
cd inventarioTI-backend/deploy
cp .env.example .env && chmod 600 .env       # .env nunca se versiona
```

Secretos (sin Python en el host):

```bash
openssl rand -hex 32                                  # SECRET_KEY (firma HS256 de los JWT, PyJWT)
openssl rand -base64 24                               # POSTGRES_PASSWORD, POSTGRES_REPLICATION_PASSWORD, REDIS_PASSWORD, MINIO_*_PASSWORD
echo "inventario-kms:$(openssl rand -base64 32)"      # MINIO_KMS_SECRET_KEY
docker run --rm python:3.12-slim sh -c \
  "pip -q install cryptography && python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
                                                      # FIELD_ENCRYPTION_KEY (si se pierde, las licencias cifradas no se recuperan)
```

- Tokens con **PyJWT** (`HS256`, `SECRET_KEY` ≥ 32 bytes); cambiar la clave cierra todas
  las sesiones. Los `id_token` de SSO solo se aceptan con firma asimétrica
  (`RS*`, `PS256`, `ES256`) verificada contra el JWKS del proveedor.
- Sesiones de producción: inactividad máxima **120 min** (el arranque falla si se
  configura más), cookie de refresh `HttpOnly`, `SameSite=Strict`, de sesión.
- Guarde una copia cifrada del `.env` en un gestor de secretos: la necesita para §6.

Primer arranque:

```bash
docker compose up -d --build
docker compose ps                 # todo healthy; migrator, minio_init y db_replication_init «exited (0)»
curl -k https://localhost/health
```

---

## 3. Estrategia de respaldo, RPO y RTO

| Capa | Mecanismo | Frecuencia | Protege de |
|---|---|---|---|
| Réplica en caliente | `db_report`: *streaming replication* del primario (`synchronous_commit=on`) | Continua | Lecturas pesadas; base para conmutación manual |
| `backup_db` | `pg_dump -Fc` verificado con `pg_restore --list`; se escribe como `.tmp` y se renombra al terminar | `BACKUP_INTERVAL_HOURS` (24; puede bajarse a 1) | Errores lógicos, borrados, corrupción |
| `backup_minio` | Espejo del bucket de adjuntos | `BACKUP_INTERVAL_HOURS` | Pérdida de adjuntos |
| `backup_replica` | rclone `copy` de `BACKUP_DIR` a otro disco / NAS / remoto (excluye `*.tmp` y archivos de menos de 2 min) | 60 s | Pérdida del servidor o del disco, ransomware en el servidor |

> `db_report` vive en el mismo servidor: protege del fallo del proceso, no del equipo.
> La protección ante pérdida total del servidor es `backup_replica`.

**RPO:** `BACKUP_INTERVAL_HOURS` para la pérdida total del servidor (24 h por defecto:
para copias horarias, `BACKUP_INTERVAL_HOURS=1`; la retención es por días).
**RTO** de la restauración: minutos (ver §6).

### 3.1 Réplica a un disco secundario o NAS

```dotenv
# deploy/.env
COMPOSE_PROFILES=replica
BACKUP_REPLICA_DIR=D:/inventario-ti-replica          # Windows: otra unidad física
# BACKUP_REPLICA_DIR=/mnt/nas-inventario             # Linux: disco o NAS montado (cifs/nfs en /etc/fstab)
# BACKUP_REPLICA_REMOTE=nas:inventario               # y/o remoto definido en backup/rclone.conf
```

```bash
docker compose up -d backup_replica && docker compose ps backup_replica   # healthy tras la primera copia
```

Linux con NAS por SMB (montado al arranque, credenciales fuera del repositorio):

```bash
sudo apt install cifs-utils
sudo mkdir -p /mnt/nas-inventario
echo "username=svc_respaldos
password=<clave>" | sudo tee /root/.nas-inventario >/dev/null && sudo chmod 600 /root/.nas-inventario
echo "//nas01/respaldos/inventario /mnt/nas-inventario cifs credentials=/root/.nas-inventario,uid=0,gid=0,file_mode=0640,dir_mode=0750,_netdev,nofail 0 0" \
  | sudo tee -a /etc/fstab
sudo mount -a
# .env: BACKUP_REPLICA_DIR=/mnt/nas-inventario
```

---

## 4. Alta disponibilidad y persistencia

- **`restart: unless-stopped`** en todos los servicios de larga duración: vuelven solos
  tras una caída o un reinicio del equipo (salvo detención manual).
- Datos en volúmenes con nombre (`postgres_data`, `postgres_report_data`, `minio_data`,
  `adjuntos_data`, `caddy_data`). **Nunca** `docker compose down -v` en producción.
- Logs `json-file` rotados y comprimidos: `LOG_MAX_SIZE` (10m) × `LOG_MAX_FILE` (5).

Servicio systemd (`/etc/systemd/system/inventario-ti.service`):

```ini
[Unit]
Description=Inventario-TI PostgreSQL (docker compose)
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/inventario-ti/inventarioTI-backend/deploy
ExecStart=/usr/bin/docker compose up -d --remove-orphans
ExecStop=/usr/bin/docker compose stop --timeout 60
TimeoutStartSec=600

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now inventario-ti
```

---

## 5. Monitoreo y alertas

Uptime Kuma (local, gratuito):

```bash
docker run -d --name uptime-kuma --restart unless-stopped -p 127.0.0.1:3001:3001 \
  -v uptime-kuma:/app/data -v /var/run/docker.sock:/var/run/docker.sock:ro \
  louislam/uptime-kuma:1.23.16
```

| Monitor | Tipo | Alerta si |
|---|---|---|
| `https://<dominio>/health` con palabra `"status":"ok"` | HTTP(s) – Keyword | No responde o no contiene la palabra |
| `db`, `db_report`, `backend`, `redis`, `minio` | Docker Container | Dejan de estar *healthy* |
| `backup_replica` | Docker Container | *unhealthy* (sin copia correcta en 15 min) |
| Certificado TLS | HTTP(s) | Vence en < 14 días |

Logs:

```bash
docker compose logs -f --since 10m backend
docker compose logs backup_db backup_minio backup_replica | tail -50
docker compose exec db_report psql -U "$POSTGRES_USER" -c "select pg_last_wal_replay_lsn(), now() - pg_last_xact_replay_timestamp() as retraso;"
```

---

## 6. Recuperación ante desastres (servidor nuevo)

```bash
# 1. Docker (§1), el código y el .env original
git clone https://github.com/J4GS10/inventario-ti-postgres.git /opt/inventario-ti && cd /opt/inventario-ti
cd inventarioTI-backend/deploy
cp /ruta/segura/.env .env && chmod 600 .env
# 2. BACKUP_DIR apuntando a la réplica (se lee directamente) o copiada en local
#    BACKUP_DIR=/mnt/nas-inventario     (o)  rclone copy nas:inventario ./backups
# 3. Base vacía y restauración del respaldo más reciente
docker compose up -d --wait db
ls -1 "${BACKUP_DIR:-./backups}"/db/*.dump | tail -1
docker compose run --rm --no-deps --entrypoint /bin/sh -e CONFIRMAR=SI \
  backup_db /backup/restaurar_db.sh /backups/db/inventario_AAAAMMDD_HHMMSS.dump
# 4. Adjuntos
docker compose up -d --wait minio minio_init
docker compose run --rm --no-deps --entrypoint /bin/sh backup_minio /backup/restaurar_minio.sh
# 5. Resto del stack (migrador idempotente, réplica db_report se reconstruye sola)
docker compose up -d --wait
curl -k https://<dominio>/health
```

`--entrypoint` es obligatorio: sin él, `backup_db`/`backup_minio` ejecutarían su bucle
de respaldo en lugar de restaurar. En Git Bash anteponga `MSYS_NO_PATHCONV=1`.

Prueba medida (2026-10-26): `respaldo_db.sh` → `replicar.sh` hacia
`D:\inventario-ti-replica-pg-prueba` (otro disco físico) → `restaurar_db.sh` sobre una
base vacía montando la réplica **en solo lectura**: copia idéntica byte a byte y conteos
idénticos (500 000 / 5 / 5 000 filas y suma de control 12 502 500).

---

## 7. Pruebas y calidad

```bash
bash herramientas-qa/pruebas_backend.sh            # backend contra PostgreSQL 16 desechable, en contenedores
bash herramientas-qa/grafo.sh                      # grafo de dependencias del código (graphify, sin LLM)
cd inventarioTI-frontend && npx tsc -b && npx eslint src && npm run build
```

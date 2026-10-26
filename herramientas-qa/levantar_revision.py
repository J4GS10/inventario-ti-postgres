"""
Levanta un entorno de REVISIÓN aislado (proyecto docker `inv_ui`, https://localhost:8443)
con el stack de producción y el inventario real de `codigo/dataset`.

Uso (desde cualquier carpeta):  python levantar_revision.py [--sin-build]
La contraseña se toma de REVISION_ADMIN_PASSWORD o se genera aleatoriamente.
Apagar y borrar datos:  docker compose -p inv_ui --env-file herramientas-qa/revision.env down -v
"""
import base64
import os
import secrets
import subprocess
import sys

from cryptography.fernet import Fernet

AQUI = os.path.dirname(os.path.abspath(__file__))
DEPLOY = os.path.join(AQUI, "..", "inventarioTI-backend", "deploy")
ENV = os.path.join(AQUI, "revision.env")
PWD_SA = os.environ.get("REVISION_ADMIN_PASSWORD") or secrets.token_urlsafe(18)

if not os.path.exists(ENV):
    base = open(os.path.join(DEPLOY, ".env.example"), encoding="utf-8").read().splitlines()
    pg, rp = secrets.token_hex(12), secrets.token_hex(12)
    over = {
        "POSTGRES_PASSWORD": pg, "POSTGRES_REPLICATION_PASSWORD": secrets.token_hex(12),
        "POSTGRES_REPLICA_PASSWORD": pg, "MINIO_ROOT_PASSWORD": secrets.token_hex(12),
        "SECRET_KEY": secrets.token_hex(32), "FIELD_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "REDIS_PASSWORD": rp, "REDIS_URL": f"redis://:{rp}@redis:6379/0", "SUPER_ADMIN_PASSWORD": PWD_SA,
        "MINIO_APP_USER": "inventario-app", "MINIO_APP_PASSWORD": secrets.token_hex(12),
        "MINIO_KMS_SECRET_KEY": "inventario-kms:" + base64.b64encode(os.urandom(32)).decode(),
        "BACKUP_DIR": "./backups-revision",
        "HTTP_PORT": "8081", "HTTPS_PORT": "8443", "BACKEND_CORS_ORIGINS": '["https://localhost:8443"]',
        "SMTP_HOST": "", "RATE_LIMIT_LOGIN": "200/minute", "RATE_LIMIT_DEFAULT": "5000/minute",
        "RATE_LIMIT_REFRESH": "200/minute", "FRONTEND_CONTEXT": "../../inventarioTI-frontend",
    }
    out, seen = [], set()
    for line in base:
        if "=" in line and not line.lstrip().startswith("#"):
            k = line.split("=", 1)[0].strip()
            if k in over:
                line, _ = f"{k}={over[k]}", seen.add(k)
        out.append(line)
    out += [f"{k}={v}" for k, v in over.items() if k not in seen]
    open(ENV, "w", encoding="utf-8").write("\n".join(out) + "\n")
    print("revision.env generado (secretos aleatorios, solo para esta máquina)")

# Completar variables nuevas en un revision.env generado por una versión anterior.
_actual = open(ENV, encoding="utf-8").read()
_faltan = {
    "MINIO_APP_USER": "inventario-app", "MINIO_APP_PASSWORD": secrets.token_hex(12),
    "MINIO_KMS_SECRET_KEY": "inventario-kms:" + base64.b64encode(os.urandom(32)).decode(),
    "BACKUP_DIR": "./backups-revision",
}
_claves = {ln.split("=", 1)[0].strip() for ln in _actual.splitlines() if "=" in ln}
_nuevas = [f"{k}={v}" for k, v in _faltan.items() if k not in _claves]
if _nuevas:
    open(ENV, "a", encoding="utf-8").write("\n" + "\n".join(_nuevas) + "\n")

cmd = ["docker", "compose", "-p", "inv_ui", "--env-file", ENV, "up", "-d"]
if "--sin-build" not in sys.argv:
    cmd.append("--build")
subprocess.run(cmd, cwd=DEPLOY, check=True)
subprocess.run(["docker", "cp", os.path.join(AQUI, "..", "dataset"), "inv_ui-backend-1:/tmp/dataset"], check=True)
subprocess.run(["docker", "exec", "inv_ui-backend-1", "python", "-m", "app.seed_dataset_inventory",
                "--dataset", "/tmp/dataset"], check=False, env={**os.environ, "MSYS_NO_PATHCONV": "1"})
print(f"\nListo: https://localhost:8443  (sa / {PWD_SA})")

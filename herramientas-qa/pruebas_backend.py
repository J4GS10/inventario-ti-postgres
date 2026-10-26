"""
Ejecuta la suite del backend contra PostgreSQL 16 (el motor de producción,
con las políticas RLS reales). Levanta un contenedor desechable si no existe.

Uso (desde cualquier carpeta):
    python pruebas_backend.py                 # toda la suite
    python pruebas_backend.py -k alcance      # argumentos extra para pytest
"""
import os
import subprocess
import sys
import time

AQUI = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.join(AQUI, "..", "inventarioTI-backend")
NOMBRE, PUERTO = "inv_pgtest", 55433
URL = f"postgresql+psycopg://t:t@localhost:{PUERTO}/t"


def contenedor_activo() -> bool:
    r = subprocess.run(["docker", "ps", "-q", "-f", f"name=^{NOMBRE}$"], capture_output=True, text=True)
    return bool(r.stdout.strip())


if not contenedor_activo():
    print(f"Levantando PostgreSQL 16 de pruebas ({NOMBRE}, puerto {PUERTO})…")
    subprocess.run(["docker", "run", "-d", "--rm", "--name", NOMBRE, "-e", "POSTGRES_USER=t",
                    "-e", "POSTGRES_PASSWORD=t", "-e", "POSTGRES_DB=t", "-p", f"{PUERTO}:5432",
                    "postgres:16-alpine"], check=True, capture_output=True)
for _ in range(60):
    if subprocess.run(["docker", "exec", NOMBRE, "pg_isready", "-U", "t", "-d", "t"],
                      capture_output=True).returncode == 0:
        break
    time.sleep(1)
else:
    sys.exit("PostgreSQL de pruebas no respondió a tiempo.")

env = {**os.environ, "TEST_DATABASE_URL": URL, "PYTHONIOENCODING": "utf-8"}
cmd = [sys.executable, "-m", "pytest", "tests/", "-q", "-p", "no:warnings", "-p", "no:logging", *sys.argv[1:]]
sys.exit(subprocess.run(cmd, cwd=BACKEND, env=env).returncode)

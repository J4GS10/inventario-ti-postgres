"""
Aislamiento: este repositorio es solo PostgreSQL. Ningún driver, migración ni
SQL propio de otro motor (la lista de abajo) entra en el código, la imagen o el
despliegue.
"""
import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
REVISAR = ["app", "requirements.txt", "alembic.ini", "migrator.sh", "Dockerfile", "docker-compose.yml"]
EXTENSIONES = {".py", ".txt", ".ini", ".sh", ".yml", ".yaml", ".sql", ""}
PROHIBIDO = re.compile(
    r"oracledb|oracle\+|alembic_oracle|dialects(\.| import )oracle|== ?[\"']oracle[\"']|\bFROM DUAL\b|DBMS_",
    re.IGNORECASE,
)


def _archivos():
    for nombre in REVISAR:
        ruta = RAIZ / nombre
        if ruta.is_file():
            yield ruta
        elif ruta.is_dir():
            yield from (p for p in ruta.rglob("*") if p.is_file() and p.suffix in EXTENSIONES
                        and "__pycache__" not in p.parts)


def test_sin_codigo_ni_dependencias_de_oracle():
    hallazgos = [
        f"{p.relative_to(RAIZ)}:{n}: {linea.strip()[:100]}"
        for p in _archivos()
        for n, linea in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), start=1)
        if PROHIBIDO.search(linea)
    ]
    assert not hallazgos, "Código de Oracle en la versión PostgreSQL:\n" + "\n".join(hallazgos)


def test_config_no_acepta_oracle():
    import pytest
    from pydantic import ValidationError

    from app.core.config import Settings
    with pytest.raises(ValidationError):
        Settings(DB_ENGINE="oracle", SECRET_KEY="x" * 32)

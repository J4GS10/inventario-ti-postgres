"""
Crea los índices únicos sin distinguir mayúsculas (app/db/unicidad.py) en una
base existente. Un índice cuya tabla ya tiene duplicados se omite y se informa.

Uso: python -m app.db.unicidad_instalar
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.schema import CreateIndex

from app.db.base import Base
from app.db.unicidad import INDICES_CI, registrar_indices


def instalar_indices(conn: Connection) -> list[tuple[str, int]]:
    """Crea los índices que falten. Devuelve [(índice, duplicados)] de los omitidos."""
    registrar_indices()
    omitidos: list[tuple[str, int]] = []
    existentes = set(conn.execute(text("SELECT indexname FROM pg_indexes")).scalars().all())
    # Una migración intermedia instala los índices antes de que existan las
    # tablas de migraciones posteriores: esas se omiten (las crea su migración).
    tablas = set(conn.execute(text(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()"
    )).scalars().all())
    for nombre, tabla, texto, exactas, _codigo in INDICES_CI:
        if nombre in existentes or tabla not in tablas:
            continue
        claves = [f'"{c}"' for c in exactas] + [f'lower(trim("{c}"))' for c in texto]
        if tabla == "INV_SOFTWARE":
            claves.append('coalesce(lower(trim("SOF_Version")), \'\')')
        grupo = ", ".join(claves)
        duplicados = conn.execute(text(
            f'SELECT count(*) FROM (SELECT {grupo} FROM "{tabla}" GROUP BY {grupo} HAVING count(*) > 1) d'
        )).scalar() or 0
        if duplicados:
            omitidos.append((nombre, int(duplicados)))
            continue
        indice = next(ix for ix in Base.metadata.tables[tabla].indexes if ix.name == nombre)
        conn.execute(CreateIndex(indice))
    return omitidos


async def main() -> int:
    from app.db.session import engine
    if engine.dialect.name != "postgresql":
        print("Solo aplica a PostgreSQL.")
        return 0
    async with engine.begin() as conn:
        omitidos = await conn.run_sync(instalar_indices)
    for nombre, n in omitidos:
        print(f"Omitido {nombre}: {n} valores duplicados")
    print("Listo." if not omitidos else "Corrija los duplicados y vuelva a ejecutar.")
    return 1 if omitidos else 0


if __name__ == "__main__":
    import asyncio
    import sys
    sys.exit(asyncio.run(main()))

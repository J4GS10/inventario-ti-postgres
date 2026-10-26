"""Unicidad sin distinguir mayúsculas/espacios y permisos de identidad (Fases 1–2).

- Correos de personas normalizados a minúsculas (si no generan duplicados).
- Índices únicos funcionales lower(trim(col)) de app/db/unicidad.py. Si una
  tabla YA tiene duplicados, ese índice no se crea y se informa: corrija los
  duplicados (python -m app.diagnostico_inventario los lista) y vuelva a
  ejecutar `python -m app.db.unicidad_instalar`.
- PostgreSQL: se reinstala la capa RLS, que ahora impide al rol restringido
  escribir rol, alcance global, estado y sedes de las cuentas.

Revision ID: d1e2f3a4b5c6
Revises: c0d1e2f3a4b5
Create Date: 2026-09-29
"""
from typing import Sequence, Union

from alembic import op

revision: str = "d1e2f3a4b5c6"
down_revision: Union[str, Sequence[str], None] = "c0d1e2f3a4b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute(
        """
        UPDATE "INV_PERSONA" p
           SET "PER_Email_Corporativo" = lower(trim(p."PER_Email_Corporativo"))
         WHERE p."PER_Email_Corporativo" <> lower(trim(p."PER_Email_Corporativo"))
           AND NOT EXISTS (
               SELECT 1 FROM "INV_PERSONA" o
                WHERE o."PER_Persona" <> p."PER_Persona"
                  AND lower(trim(o."PER_Email_Corporativo")) = lower(trim(p."PER_Email_Corporativo")))
        """
    )
    from app.db.unicidad_instalar import instalar_indices
    omitidos = instalar_indices(bind)
    for nombre, n in omitidos:
        print(f"AVISO: índice {nombre} no creado: {n} valores duplicados. Corríjalos y ejecute "
              "python -m app.db.unicidad_instalar")

    from app.db.rls import install_rls
    if not install_rls(bind):
        print("AVISO: no se pudo reinstalar la capa RLS; ejecute python -m app.db.install_rls")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    from app.db.unicidad import INDICES_CI
    for nombre, *_ in INDICES_CI:
        op.execute(f'DROP INDEX IF EXISTS "{nombre}"')

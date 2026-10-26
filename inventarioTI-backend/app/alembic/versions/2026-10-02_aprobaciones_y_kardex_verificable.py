"""Control interno: aprobación del Jefe de TI, costo promedio ponderado y kardex verificable.

- INV_USUARIO: rol JEFE_TI (opera como ADMIN_TI y aprueba bajas y sobrantes).
- INV_APROBACION: solicitudes pendientes de aprobación (otra persona aprueba).
- INV_KARDEX:
  - tipos CONSUMIBLE_TRASLADO (cambio de sede de un consumible) y
    LICENCIA_BAJA (eliminación de una licencia);
  - KDX_Saldo_Valor (valor del saldo al costo promedio ponderado);
  - saldo inicial reconstruido de los consumibles creados con existencias,
    para que su kardex cuadre desde cero;
  - sello encadenado (KDX_Secuencia, KDX_Hash_Anterior, KDX_Hash) y bloqueo
    de TRUNCATE (ver app/db/kardex_integridad.py). Se sella el histórico.

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-10-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f3a4b5c6d7e8"
down_revision: Union[str, Sequence[str], None] = "e2f3a4b5c6d7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ROLES_NEW = ("\"USU_Rol\" IN ('SUPER_ADMIN', 'ADMIN_SEGURIDAD', 'JEFE_TI', 'ADMIN_TI', 'TECNICO', "
              "'AUDITOR', 'CONSULTA')")
_ROLES_OLD = "\"USU_Rol\" IN ('SUPER_ADMIN', 'ADMIN_SEGURIDAD', 'ADMIN_TI', 'TECNICO', 'AUDITOR', 'CONSULTA')"


def _tipos_kardex(con_nuevos: bool) -> str:
    from app.models.kardex import TIPOS_EVENTO
    tipos = TIPOS_EVENTO if con_nuevos else tuple(
        t for t in TIPOS_EVENTO if t not in ("CONSUMIBLE_TRASLADO", "LICENCIA_BAJA"))
    return '"KDX_Tipo" IN (' + ", ".join(f"'{t}'" for t in tipos) + ")"


# Consumibles cuyo kardex no parte de cero (se crearon con existencias o el
# historial empezó con stock): se registra el saldo inicial como entrada.
_SALDO_INICIAL = r'''
INSERT INTO "INV_KARDEX" ("KDX_Fecha", "KDX_Tipo", "KDX_Objeto", "CON_Consumible", "KDX_Referencia",
                          "KDX_Destino_Sede", "SED_Sede", "KDX_Cantidad", "KDX_Saldo", "KDX_Costo", "KDX_Moneda",
                          "KDX_Motivo", "KDX_Migrado")
SELECT coalesce(p.fecha - interval '1 second', now() AT TIME ZONE 'UTC'), 'CONSUMIBLE_ENTRADA', 'CONSUMIBLE',
       c."CON_Consumible", c."CON_Nombre", c."SED_Sede", c."SED_Sede", s.inicial, s.inicial,
       c."CON_Costo_Unitario", c."CON_Moneda", 'Saldo inicial (reconstruido)', true
  FROM "INV_CONSUMIBLE" c
  LEFT JOIN LATERAL (
        SELECT k."KDX_Fecha" AS fecha, k."KDX_Saldo" AS saldo,
               CASE k."KDX_Tipo" WHEN 'CONSUMIBLE_SALIDA' THEN -k."KDX_Cantidad"
                                 WHEN 'CONSUMIBLE_TRASLADO' THEN 0
                                 ELSE coalesce(k."KDX_Cantidad", 0) END AS delta
          FROM "INV_KARDEX" k
         WHERE k."CON_Consumible" = c."CON_Consumible"
         ORDER BY k."KDX_Fecha", k."KDX_Evento" LIMIT 1) p ON true
  CROSS JOIN LATERAL (SELECT CASE WHEN p.fecha IS NULL THEN c."CON_Stock_Actual"
                                  ELSE p.saldo - p.delta END AS inicial) s
 WHERE s.inicial > 0
'''


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    with op.batch_alter_table("INV_USUARIO") as batch:
        batch.drop_constraint("ck_usuario_rol_valido", type_="check")
        batch.create_check_constraint("ck_usuario_rol_valido", _ROLES_NEW)

    op.create_table(
        "INV_APROBACION",
        sa.Column("APR_Aprobacion", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("APR_Tipo", sa.String(30), nullable=False),
        sa.Column("APR_Estado", sa.String(12), nullable=False, server_default="PENDIENTE"),
        sa.Column("APR_Payload", sa.JSON(), nullable=False),
        sa.Column("APR_Resumen", sa.String(500), nullable=False),
        sa.Column("APR_Motivo", sa.String(500), nullable=True),
        sa.Column("ACT_Activo", sa.Uuid(), nullable=True),
        sa.Column("OCO_Orden", sa.Integer(), nullable=True),
        sa.Column("SED_Sede", sa.Integer(), nullable=True),
        sa.Column("APR_Solicitante", sa.Uuid(), nullable=False),
        sa.Column("APR_Fecha_Solicitud", sa.DateTime(), nullable=False),
        sa.Column("APR_Aprobador", sa.Uuid(), nullable=True),
        sa.Column("APR_Fecha_Resolucion", sa.DateTime(), nullable=True),
        sa.Column("APR_Comentario", sa.String(500), nullable=True),
        sa.CheckConstraint("\"APR_Tipo\" IN ('BAJA', 'RECEPCION_SOBRANTE')", name="ck_aprobacion_tipo"),
        sa.CheckConstraint("\"APR_Estado\" IN ('PENDIENTE', 'APROBADA', 'RECHAZADA', 'ANULADA')",
                           name="ck_aprobacion_estado"),
        sa.CheckConstraint("\"APR_Aprobador\" IS NULL OR \"APR_Aprobador\" <> \"APR_Solicitante\"",
                           name="ck_aprobacion_otra_persona"),
    )
    op.create_index("ix_INV_APROBACION_ACT_Activo", "INV_APROBACION", ["ACT_Activo"])
    op.create_index("ix_INV_APROBACION_OCO_Orden", "INV_APROBACION", ["OCO_Orden"])
    op.create_index("ix_INV_APROBACION_SED_Sede", "INV_APROBACION", ["SED_Sede"])
    op.create_index("ix_aprobacion_estado_fecha", "INV_APROBACION", ["APR_Estado", "APR_Fecha_Solicitud"])

    # Costo promedio ponderado con 4 decimales (evita acumular redondeos).
    with op.batch_alter_table("INV_CONSUMIBLE") as batch:
        batch.alter_column("CON_Costo_Unitario", type_=sa.Numeric(16, 4), existing_nullable=True)
    op.add_column("INV_KARDEX", sa.Column("KDX_Saldo_Valor", sa.Numeric(16, 2), nullable=True))
    op.add_column("INV_KARDEX", sa.Column("KDX_Secuencia", sa.BigInteger(), nullable=True))
    op.add_column("INV_KARDEX", sa.Column("KDX_Hash_Anterior", sa.String(64), nullable=True))
    op.add_column("INV_KARDEX", sa.Column("KDX_Hash", sa.String(64), nullable=True))
    with op.batch_alter_table("INV_KARDEX") as batch:
        batch.drop_constraint("ck_kardex_tipo", type_="check")
        batch.create_check_constraint("ck_kardex_tipo", _tipos_kardex(True))
        batch.alter_column("KDX_Costo", type_=sa.Numeric(16, 4), existing_nullable=True)

    if not is_pg:
        op.create_unique_constraint("uq_kardex_secuencia", "INV_KARDEX", ["KDX_Secuencia"])
        return

    op.execute(_SALDO_INICIAL)
    from app.db.kardex_integridad import instalar
    instalar(bind, sellar_existentes=True)
    op.create_unique_constraint("uq_kardex_secuencia", "INV_KARDEX", ["KDX_Secuencia"])

    from app.db.rls import install_rls
    if not install_rls(bind):
        print("AVISO: no se pudo reinstalar la capa RLS; ejecute python -m app.db.install_rls")


def downgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"
    if is_pg:
        op.execute('DROP TRIGGER IF EXISTS trg_kardex_sellar ON "INV_KARDEX"')
        op.execute('DROP TRIGGER IF EXISTS trg_kardex_sin_vaciar ON "INV_KARDEX"')
        op.execute("DROP FUNCTION IF EXISTS inv_kardex_sellar()")
        op.execute("DROP FUNCTION IF EXISTS inv_kardex_sin_vaciar()")
        op.execute("DROP FUNCTION IF EXISTS inv_kardex_hash(text, \"INV_KARDEX\")")
    op.drop_constraint("uq_kardex_secuencia", "INV_KARDEX", type_="unique")
    with op.batch_alter_table("INV_KARDEX") as batch:
        batch.drop_constraint("ck_kardex_tipo", type_="check")
        batch.create_check_constraint("ck_kardex_tipo", _tipos_kardex(False))
    for col in ("KDX_Hash", "KDX_Hash_Anterior", "KDX_Secuencia", "KDX_Saldo_Valor"):
        op.drop_column("INV_KARDEX", col)
    op.drop_table("INV_APROBACION")
    with op.batch_alter_table("INV_USUARIO") as batch:
        batch.drop_constraint("ck_usuario_rol_valido", type_="check")
        batch.create_check_constraint("ck_usuario_rol_valido", _ROLES_OLD)

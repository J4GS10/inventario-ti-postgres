"""Fases 3–4: recepción controlada, kardex unificado, componentes, bajas formales,
historial de licencias, costo de consumibles y toma de inventario físico.

- INV_ORDEN_COMPRA: estados PARCIAL y CERRADA_CON_FALTANTE, motivo de cierre.
- INV_ORDEN_COMPRA_LINEA: tipo de línea y cantidad recibida acumulada (las
  órdenes ya recibidas quedan con su cantidad completa recibida).
- INV_RECEPCION / INV_RECEPCION_LINEA: cada entrega como documento.
- INV_KARDEX: registro de solo inserción (trigger) de todo evento de
  inventario; se reconstruye el historial existente (KDX_Migrado).
- INV_MOVIMIENTO: quién lo registró, motivo y devolución prevista (préstamos).
- INV_ACTIVO: ubicación física y datos de la baja formal.
- INV_LICENCIA: costo y moneda. INV_INSTALACION: fecha y motivo del retiro.
- INV_CONSUMIBLE: último costo unitario y moneda.
- INV_TOMA_INVENTARIO / INV_TOMA_LECTURA: toma de inventario físico.
- PostgreSQL: reinstala la capa RLS (políticas de las tablas nuevas).

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-09-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e2f3a4b5c6d7"
down_revision: Union[str, Sequence[str], None] = "d1e2f3a4b5c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ESTADOS_OC = "'BORRADOR', 'PARCIAL', 'RECIBIDA', 'CERRADA_CON_FALTANTE', 'CANCELADA'"


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    # ---------------- Compras ----------------
    with op.batch_alter_table("INV_ORDEN_COMPRA") as b:
        b.alter_column("OCO_Estado", type_=sa.String(25), existing_type=sa.String(15), existing_nullable=False)
        b.add_column(sa.Column("OCO_Motivo_Cierre", sa.String(500), nullable=True))
        b.drop_constraint("ck_orden_estado_valido", type_="check")
        b.create_check_constraint("ck_orden_estado_valido", f'"OCO_Estado" IN ({_ESTADOS_OC})')
    with op.batch_alter_table("INV_ORDEN_COMPRA_LINEA") as b:
        b.add_column(sa.Column("OCL_Tipo", sa.String(12), nullable=True))
        b.add_column(sa.Column("OCL_Cantidad_Recibida", sa.Integer(), nullable=False, server_default="0"))
        b.create_check_constraint("ck_linea_recibida_no_negativa", '"OCL_Cantidad_Recibida" >= 0')
        b.create_check_constraint(
            "ck_linea_tipo_valido",
            "\"OCL_Tipo\" IS NULL OR \"OCL_Tipo\" IN ('ACTIVO', 'CONSUMIBLE', 'LICENCIA', 'OTRO')",
        )
    # Las órdenes ya recibidas se recibieron completas (el sistema anterior no
    # permitía recibir parcialmente de forma registrada).
    op.execute(
        """
        UPDATE "INV_ORDEN_COMPRA_LINEA" l SET "OCL_Cantidad_Recibida" = l."OCL_Cantidad"
          FROM "INV_ORDEN_COMPRA" o
         WHERE o."OCO_Orden" = l."OCO_Orden" AND o."OCO_Estado" = 'RECIBIDA'
        """
    )
    op.execute(
        """
        UPDATE "INV_ORDEN_COMPRA_LINEA" SET "OCL_Tipo" = CASE
            WHEN "CON_Consumible" IS NOT NULL THEN 'CONSUMIBLE'
            WHEN "LIC_Licencia" IS NOT NULL THEN 'LICENCIA'
            WHEN "ACT_Activo" IS NOT NULL THEN 'ACTIVO' END
         WHERE "OCL_Tipo" IS NULL
        """
    )
    op.create_table(
        "INV_RECEPCION",
        sa.Column("REC_Recepcion", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("OCO_Orden", sa.Integer(), sa.ForeignKey("INV_ORDEN_COMPRA.OCO_Orden", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("REC_Fecha", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("REC_Documento", sa.String(100), nullable=True),
        sa.Column("REC_Observacion", sa.String(500), nullable=True),
        sa.Column("REC_Resultado", sa.String(10), nullable=False),
        sa.Column("USU_Usuario", sa.Uuid(), sa.ForeignKey("INV_USUARIO.USU_Usuario", ondelete="SET NULL"),
                  nullable=True),
        sa.CheckConstraint("\"REC_Resultado\" IN ('COMPLETA', 'PARCIAL')", name="ck_recepcion_resultado"),
    )
    op.create_index("ix_INV_RECEPCION_REC_Recepcion", "INV_RECEPCION", ["REC_Recepcion"])
    op.create_index("ix_INV_RECEPCION_OCO_Orden", "INV_RECEPCION", ["OCO_Orden"])
    op.create_table(
        "INV_RECEPCION_LINEA",
        sa.Column("RCL_Recepcion_Linea", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("REC_Recepcion", sa.Integer(), sa.ForeignKey("INV_RECEPCION.REC_Recepcion", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("OCL_Linea", sa.Integer(), sa.ForeignKey("INV_ORDEN_COMPRA_LINEA.OCL_Linea", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("RCL_Cantidad", sa.Integer(), nullable=False),
        sa.Column("RCL_Precio_Real", sa.Numeric(14, 2), nullable=True),
        sa.Column("RCL_Diferencia", sa.String(10), nullable=True),
        sa.Column("RCL_Motivo", sa.String(500), nullable=True),
        sa.CheckConstraint('"RCL_Cantidad" > 0', name="ck_recepcion_linea_cantidad"),
    )
    op.create_index("ix_INV_RECEPCION_LINEA_RCL_Recepcion_Linea", "INV_RECEPCION_LINEA", ["RCL_Recepcion_Linea"])
    op.create_index("ix_INV_RECEPCION_LINEA_REC_Recepcion", "INV_RECEPCION_LINEA", ["REC_Recepcion"])
    op.create_index("ix_INV_RECEPCION_LINEA_OCL_Linea", "INV_RECEPCION_LINEA", ["OCL_Linea"])

    # ---------------- Movimientos, activos, licencias, consumibles ----------------
    with op.batch_alter_table("INV_MOVIMIENTO") as b:
        b.add_column(sa.Column("MOV_Usuario_Registra", sa.Uuid(), nullable=True))
        b.add_column(sa.Column("MOV_Motivo", sa.String(255), nullable=True))
        b.add_column(sa.Column("MOV_Fecha_Prevista_Devolucion", sa.Date(), nullable=True))
    with op.batch_alter_table("INV_ACTIVO") as b:
        b.add_column(sa.Column("ARE_Area_Ubicacion", sa.Integer(), nullable=True))
        b.create_foreign_key("fk_activo_area_ubicacion", "INV_AREA", ["ARE_Area_Ubicacion"], ["ARE_Area"],
                             ondelete="SET NULL")
        b.add_column(sa.Column("ACT_Baja_Motivo", sa.String(30), nullable=True))
        b.add_column(sa.Column("ACT_Baja_Destino", sa.String(30), nullable=True))
        b.add_column(sa.Column("ACT_Baja_Fecha", sa.Date(), nullable=True))
        b.add_column(sa.Column("ACT_Baja_Documento", sa.String(120), nullable=True))
        b.add_column(sa.Column("ACT_Baja_Observacion", sa.String(500), nullable=True))
    op.create_index("ix_INV_ACTIVO_ACT_Activo_Padre", "INV_ACTIVO", ["ACT_Activo_Padre"])
    # Ubicación actual: el área de la custodia vigente.
    op.execute(
        """
        UPDATE "INV_ACTIVO" a SET "ARE_Area_Ubicacion" = m."ARE_Area"
          FROM "INV_MOVIMIENTO" m
         WHERE m."ACT_Activo" = a."ACT_Activo" AND m."MOV_Fecha_Devolucion" IS NULL
        """
    )
    with op.batch_alter_table("INV_LICENCIA") as b:
        b.add_column(sa.Column("LIC_Costo", sa.Numeric(14, 2), nullable=True))
        b.add_column(sa.Column("LIC_Moneda", sa.String(3), nullable=True))
    with op.batch_alter_table("INV_INSTALACION") as b:
        b.add_column(sa.Column("INS_Fecha_Retiro", sa.DateTime(), nullable=True))
        b.add_column(sa.Column("INS_Motivo_Retiro", sa.String(255), nullable=True))
    with op.batch_alter_table("INV_CONSUMIBLE") as b:
        b.add_column(sa.Column("CON_Costo_Unitario", sa.Numeric(14, 2), nullable=True))
        b.add_column(sa.Column("CON_Moneda", sa.String(3), nullable=True))

    # ---------------- Kardex ----------------
    from app.models.kardex import TIPOS_EVENTO
    tipos = ", ".join(f"'{t}'" for t in TIPOS_EVENTO)
    op.create_table(
        "INV_KARDEX",
        sa.Column("KDX_Evento", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("KDX_Fecha", sa.DateTime(), nullable=False),
        sa.Column("KDX_Tipo", sa.String(30), nullable=False),
        sa.Column("KDX_Objeto", sa.String(12), nullable=False),
        sa.Column("ACT_Activo", sa.Uuid(), nullable=True),
        sa.Column("CON_Consumible", sa.Integer(), nullable=True),
        sa.Column("LIC_Licencia", sa.Integer(), nullable=True),
        sa.Column("LCL_Licencia_Clave", sa.Integer(), nullable=True),
        sa.Column("KDX_Referencia", sa.String(200), nullable=True),
        sa.Column("KDX_Origen_Persona", sa.Uuid(), nullable=True),
        sa.Column("KDX_Origen_Area", sa.Integer(), nullable=True),
        sa.Column("KDX_Origen_Sede", sa.Integer(), nullable=True),
        sa.Column("KDX_Origen_Activo", sa.Uuid(), nullable=True),
        sa.Column("KDX_Destino_Persona", sa.Uuid(), nullable=True),
        sa.Column("KDX_Destino_Area", sa.Integer(), nullable=True),
        sa.Column("KDX_Destino_Sede", sa.Integer(), nullable=True),
        sa.Column("KDX_Destino_Activo", sa.Uuid(), nullable=True),
        sa.Column("KDX_Cantidad", sa.Integer(), nullable=True),
        sa.Column("KDX_Saldo", sa.Integer(), nullable=True),
        sa.Column("KDX_Costo", sa.Numeric(14, 2), nullable=True),
        sa.Column("KDX_Moneda", sa.String(3), nullable=True),
        sa.Column("KDX_Documento_Tipo", sa.String(30), nullable=True),
        sa.Column("KDX_Documento_Ref", sa.String(120), nullable=True),
        sa.Column("KDX_Motivo", sa.String(500), nullable=True),
        sa.Column("KDX_Detalle", sa.JSON(), nullable=True),
        sa.Column("USU_Usuario", sa.Uuid(), nullable=True),
        sa.Column("SED_Sede", sa.Integer(), nullable=True),
        sa.Column("KDX_Migrado", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.CheckConstraint(f'"KDX_Tipo" IN ({tipos})', name="ck_kardex_tipo"),
        sa.CheckConstraint("\"KDX_Objeto\" IN ('ACTIVO', 'CONSUMIBLE', 'LICENCIA')", name="ck_kardex_objeto"),
    )
    for nombre, cols in (
        ("ix_INV_KARDEX_KDX_Fecha", ["KDX_Fecha"]), ("ix_INV_KARDEX_KDX_Tipo", ["KDX_Tipo"]),
        ("ix_INV_KARDEX_ACT_Activo", ["ACT_Activo"]), ("ix_INV_KARDEX_CON_Consumible", ["CON_Consumible"]),
        ("ix_INV_KARDEX_LIC_Licencia", ["LIC_Licencia"]), ("ix_INV_KARDEX_SED_Sede", ["SED_Sede"]),
        ("ix_INV_KARDEX_KDX_Origen_Persona", ["KDX_Origen_Persona"]),
        ("ix_INV_KARDEX_KDX_Destino_Persona", ["KDX_Destino_Persona"]),
        ("ix_kardex_activo_fecha", ["ACT_Activo", "KDX_Fecha"]), ("ix_kardex_tipo_fecha", ["KDX_Tipo", "KDX_Fecha"]),
    ):
        op.create_index(nombre, "INV_KARDEX", cols)

    # ---------------- Toma de inventario ----------------
    op.create_table(
        "INV_TOMA_INVENTARIO",
        sa.Column("TOM_Toma", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("SED_Sede", sa.Integer(), sa.ForeignKey("INV_SEDE.SED_Sede", ondelete="RESTRICT"), nullable=False),
        sa.Column("ARE_Area", sa.Integer(), sa.ForeignKey("INV_AREA.ARE_Area", ondelete="RESTRICT"), nullable=True),
        sa.Column("TOM_Estado", sa.String(10), nullable=False, server_default="ABIERTA"),
        sa.Column("TOM_Fecha_Inicio", sa.DateTime(), nullable=False),
        sa.Column("TOM_Fecha_Cierre", sa.DateTime(), nullable=True),
        sa.Column("TOM_Observacion", sa.String(500), nullable=True),
        sa.Column("USU_Usuario", sa.Uuid(), sa.ForeignKey("INV_USUARIO.USU_Usuario", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("TOM_Resumen", sa.JSON(), nullable=True),
        sa.CheckConstraint("\"TOM_Estado\" IN ('ABIERTA', 'CERRADA', 'ANULADA')", name="ck_toma_estado"),
    )
    op.create_index("ix_INV_TOMA_INVENTARIO_SED_Sede", "INV_TOMA_INVENTARIO", ["SED_Sede"])
    op.create_table(
        "INV_TOMA_LECTURA",
        sa.Column("TLE_Lectura", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("TOM_Toma", sa.Integer(), sa.ForeignKey("INV_TOMA_INVENTARIO.TOM_Toma", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("TLE_Codigo_Leido", sa.String(100), nullable=False),
        sa.Column("ACT_Activo", sa.Uuid(), nullable=True),
        sa.Column("TLE_Resultado", sa.String(20), nullable=False),
        sa.Column("ARE_Area", sa.Integer(), nullable=True),
        sa.Column("TLE_Fecha", sa.DateTime(), nullable=False),
        sa.Column("TLE_Observacion", sa.String(255), nullable=True),
        sa.Column("USU_Usuario", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "\"TLE_Resultado\" IN ('ENCONTRADO', 'OTRA_UBICACION', 'OTRA_SEDE', 'DE_BAJA', 'NO_REGISTRADO', 'REPETIDO')",
            name="ck_toma_lectura_resultado",
        ),
    )
    op.create_index("ix_INV_TOMA_LECTURA_TOM_Toma", "INV_TOMA_LECTURA", ["TOM_Toma"])

    if not is_pg:
        return
    # El kardex es forense: ni el dueño de las tablas lo modifica sin
    # desactivar antes el trigger (igual que la bitácora).
    op.execute(
        """
        CREATE OR REPLACE FUNCTION inv_kardex_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'INV_KARDEX es de solo inserción: % no permitido', TG_OP;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute('DROP TRIGGER IF EXISTS trg_kardex_append_only ON "INV_KARDEX"')
    op.execute(
        'CREATE TRIGGER trg_kardex_append_only BEFORE UPDATE OR DELETE ON "INV_KARDEX" '
        "FOR EACH ROW EXECUTE FUNCTION inv_kardex_append_only()"
    )
    from app.services.kardex_migracion import reconstruir
    creados = reconstruir(bind)
    print(f"Kardex reconstruido: {creados} eventos del historial existente.")

    from app.db.rls import install_rls
    if not install_rls(bind):
        print("AVISO: no se pudo reinstalar la capa RLS; ejecute python -m app.db.install_rls")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute('DROP TRIGGER IF EXISTS trg_kardex_append_only ON "INV_KARDEX"')
        op.execute("DROP FUNCTION IF EXISTS inv_kardex_append_only()")
    op.drop_table("INV_TOMA_LECTURA")
    op.drop_table("INV_TOMA_INVENTARIO")
    op.drop_table("INV_KARDEX")
    with op.batch_alter_table("INV_CONSUMIBLE") as b:
        b.drop_column("CON_Moneda")
        b.drop_column("CON_Costo_Unitario")
    with op.batch_alter_table("INV_INSTALACION") as b:
        b.drop_column("INS_Motivo_Retiro")
        b.drop_column("INS_Fecha_Retiro")
    with op.batch_alter_table("INV_LICENCIA") as b:
        b.drop_column("LIC_Moneda")
        b.drop_column("LIC_Costo")
    op.drop_index("ix_INV_ACTIVO_ACT_Activo_Padre", table_name="INV_ACTIVO")
    with op.batch_alter_table("INV_ACTIVO") as b:
        b.drop_constraint("fk_activo_area_ubicacion", type_="foreignkey")
        for col in ("ACT_Baja_Observacion", "ACT_Baja_Documento", "ACT_Baja_Fecha", "ACT_Baja_Destino",
                    "ACT_Baja_Motivo", "ARE_Area_Ubicacion"):
            b.drop_column(col)
    with op.batch_alter_table("INV_MOVIMIENTO") as b:
        for col in ("MOV_Fecha_Prevista_Devolucion", "MOV_Motivo", "MOV_Usuario_Registra"):
            b.drop_column(col)
    op.drop_table("INV_RECEPCION_LINEA")
    op.drop_table("INV_RECEPCION")
    op.execute("""UPDATE "INV_ORDEN_COMPRA" SET "OCO_Estado" = 'RECIBIDA'
                  WHERE "OCO_Estado" IN ('PARCIAL', 'CERRADA_CON_FALTANTE')""")
    with op.batch_alter_table("INV_ORDEN_COMPRA_LINEA") as b:
        b.drop_constraint("ck_linea_tipo_valido", type_="check")
        b.drop_constraint("ck_linea_recibida_no_negativa", type_="check")
        b.drop_column("OCL_Cantidad_Recibida")
        b.drop_column("OCL_Tipo")
    with op.batch_alter_table("INV_ORDEN_COMPRA") as b:
        b.drop_constraint("ck_orden_estado_valido", type_="check")
        b.create_check_constraint("ck_orden_estado_valido", "\"OCO_Estado\" IN ('BORRADOR', 'RECIBIDA', 'CANCELADA')")
        b.drop_column("OCO_Motivo_Cierre")
        b.alter_column("OCO_Estado", type_=sa.String(15), existing_type=sa.String(25), existing_nullable=False)

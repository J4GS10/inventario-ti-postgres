"""Clases de activo, equipos compartidos, faltantes y aprobaciones completas.

- INV_TIPO_ACTIVO.TAC_Clase (cómputo, impresión, red, componente, insumo…),
  clasificada por nombre, e INV_TIPO_COMPONENTE (componentes que admite cada
  tipo) con plantillas de especificaciones por clase.
- INV_ACTIVO.ACT_Instalado y ACT_Responsable: equipos compartidos instalados en
  un área; estado operativo «Instalado».
- INV_MOVIMIENTO_CONSUMIBLE.ACT_Activo (tóner instalado en una impresora) e
  INV_CONSUMIBLE_COMPATIBLE (consumible ↔ modelo).
- INV_FALTANTE: seguimiento de lo que no apareció en una toma.
- INV_APROBACION: tipos AJUSTE_CONSUMIBLE, CIERRE_FALTANTE, CANCELACION_ORDEN y
  RECLASIFICACION, y CON_Consumible.
- INV_KARDEX: eventos INSTALACION_AREA, RETIRO_AREA y RECLASIFICACION.

Revision ID: e5a6b7c8d9f0
Revises: d4f5a6b7c8e9
Create Date: 2026-10-09
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e5a6b7c8d9f0"
down_revision: Union[str, Sequence[str], None] = "d4f5a6b7c8e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CLASES = ("'COMPUTO', 'MOVIL', 'PERIFERICO', 'IMPRESION', 'RED', 'ENERGIA', 'AUDIOVISUAL', 'COMPONENTE', "
           "'HERRAMIENTA', 'INSUMO', 'OTRO'")
_APR_NEW = ("\"APR_Tipo\" IN ('BAJA', 'RECEPCION_SOBRANTE', 'AJUSTE_CONSUMIBLE', 'CIERRE_FALTANTE', "
            "'CANCELACION_ORDEN', 'RECLASIFICACION')")
_APR_OLD = "\"APR_Tipo\" IN ('BAJA', 'RECEPCION_SOBRANTE')"
_NUEVOS_EVENTOS = ("INSTALACION_AREA", "RETIRO_AREA", "RECLASIFICACION")


def _tipos_kardex(con_nuevos: bool) -> str:
    from app.models.kardex import TIPOS_EVENTO
    tipos = TIPOS_EVENTO if con_nuevos else tuple(t for t in TIPOS_EVENTO if t not in _NUEVOS_EVENTOS)
    return '"KDX_Tipo" IN (' + ", ".join(f"'{t}'" for t in tipos) + ")"


def _reinstalar_rls(bind) -> None:
    from app.db.rls import install_rls
    install_rls(bind)


def upgrade() -> None:
    bind = op.get_bind()

    # --- Clase del tipo de activo y componentes permitidos ---
    op.add_column("INV_TIPO_ACTIVO", sa.Column("TAC_Clase", sa.String(20), nullable=False, server_default="OTRO"))
    op.create_check_constraint("ck_tipo_activo_clase", "INV_TIPO_ACTIVO", f'"TAC_Clase" IN ({_CLASES})')
    from app.core.clases_activo import clase_por_nombre
    for tipo_id, nombre in bind.execute(sa.text('SELECT "TAC_Tipo_Activo", "TAC_Nombre" FROM "INV_TIPO_ACTIVO"')).all():
        bind.execute(sa.text('UPDATE "INV_TIPO_ACTIVO" SET "TAC_Clase" = :c WHERE "TAC_Tipo_Activo" = :t'),
                     {"c": clase_por_nombre(nombre), "t": tipo_id})
    op.create_table(
        "INV_TIPO_COMPONENTE",
        sa.Column("TCP_Tipo_Componente", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("TAC_Padre", sa.Integer(), sa.ForeignKey("INV_TIPO_ACTIVO.TAC_Tipo_Activo", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("TAC_Componente", sa.Integer(),
                  sa.ForeignKey("INV_TIPO_ACTIVO.TAC_Tipo_Activo", ondelete="CASCADE"), nullable=False),
        sa.UniqueConstraint("TAC_Padre", "TAC_Componente", name="uq_tipo_componente"),
        sa.CheckConstraint('"TAC_Padre" <> "TAC_Componente"', name="ck_tipo_componente_distinto"),
    )

    # --- Equipos compartidos ---
    op.add_column("INV_ACTIVO", sa.Column("ACT_Instalado", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("INV_ACTIVO", sa.Column("ACT_Responsable", sa.Uuid(), nullable=True))
    op.create_foreign_key("fk_activo_responsable", "INV_ACTIVO", "INV_PERSONA", ["ACT_Responsable"], ["PER_Persona"],
                          ondelete="SET NULL")
    op.create_index("ix_INV_ACTIVO_ACT_Responsable", "INV_ACTIVO", ["ACT_Responsable"])
    bind.execute(sa.text(
        'INSERT INTO "INV_ESTADO_OPERATIVO" ("EOP_Nombre", "EOP_Descripcion") '
        "SELECT 'Instalado', 'Equipo compartido en uso en un área' "
        'WHERE NOT EXISTS (SELECT 1 FROM "INV_ESTADO_OPERATIVO" WHERE lower("EOP_Nombre") = \'instalado\')'))

    # --- Consumibles por equipo y compatibilidad ---
    op.add_column("INV_MOVIMIENTO_CONSUMIBLE", sa.Column("ACT_Activo", sa.Uuid(), nullable=True))
    op.create_foreign_key("fk_moc_activo", "INV_MOVIMIENTO_CONSUMIBLE", "INV_ACTIVO", ["ACT_Activo"], ["ACT_Activo"],
                          ondelete="SET NULL")
    op.create_index("ix_INV_MOVIMIENTO_CONSUMIBLE_ACT_Activo", "INV_MOVIMIENTO_CONSUMIBLE", ["ACT_Activo"])
    op.create_table(
        "INV_CONSUMIBLE_COMPATIBLE",
        sa.Column("CCM_Compatible", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("CON_Consumible", sa.Integer(), sa.ForeignKey("INV_CONSUMIBLE.CON_Consumible", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("MOD_Modelo", sa.Integer(), sa.ForeignKey("INV_MODELO.MOD_Modelo", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.UniqueConstraint("CON_Consumible", "MOD_Modelo", name="uq_consumible_compatible"),
    )

    # --- Faltantes de la toma ---
    op.create_table(
        "INV_FALTANTE",
        sa.Column("FAL_Faltante", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("TOM_Toma", sa.Integer(), sa.ForeignKey("INV_TOMA_INVENTARIO.TOM_Toma", ondelete="SET NULL"),
                  nullable=True, index=True),
        sa.Column("ACT_Activo", sa.Uuid(), sa.ForeignKey("INV_ACTIVO.ACT_Activo", ondelete="CASCADE"), nullable=False,
                  index=True),
        sa.Column("SED_Sede", sa.Integer(), sa.ForeignKey("INV_SEDE.SED_Sede", ondelete="RESTRICT"), nullable=True,
                  index=True),
        sa.Column("FAL_Estado", sa.String(20), nullable=False, server_default="PENDIENTE"),
        sa.Column("FAL_Fecha", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("FAL_Seguimiento", sa.JSON(), nullable=True),
        sa.Column("FAL_Resolucion", sa.String(500), nullable=True),
        sa.Column("FAL_Fecha_Resolucion", sa.DateTime(), nullable=True),
        sa.Column("APR_Aprobacion", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "\"FAL_Estado\" IN ('PENDIENTE', 'EN_INVESTIGACION', 'LOCALIZADO', 'BAJA_SOLICITADA', 'DADO_DE_BAJA')",
            name="ck_faltante_estado"),
    )
    # Faltantes de tomas ya cerradas: quedan pendientes de resolver.
    bind.execute(sa.text('''
        INSERT INTO "INV_FALTANTE" ("TOM_Toma", "ACT_Activo", "SED_Sede", "FAL_Fecha")
        SELECT t."TOM_Toma", CAST(f->>'activo' AS uuid), t."SED_Sede", coalesce(t."TOM_Fecha_Cierre", now())
          FROM "INV_TOMA_INVENTARIO" t
          CROSS JOIN LATERAL json_array_elements(coalesce(t."TOM_Resumen"->'faltantes', '[]'::json)) f
         WHERE t."TOM_Estado" = 'CERRADA' AND f->>'activo' IS NOT NULL
           AND EXISTS (SELECT 1 FROM "INV_ACTIVO" a WHERE a."ACT_Activo" = CAST(f->>'activo' AS uuid))
    '''))

    # --- Aprobaciones ---
    op.add_column("INV_APROBACION", sa.Column("CON_Consumible", sa.Integer(), nullable=True))
    op.create_index("ix_INV_APROBACION_CON_Consumible", "INV_APROBACION", ["CON_Consumible"])
    op.drop_constraint("ck_aprobacion_tipo", "INV_APROBACION", type_="check")
    op.create_check_constraint("ck_aprobacion_tipo", "INV_APROBACION", _APR_NEW)

    # --- Kardex ---
    op.drop_constraint("ck_kardex_tipo", "INV_KARDEX", type_="check")
    op.create_check_constraint("ck_kardex_tipo", "INV_KARDEX", _tipos_kardex(True))

    # --- Catálogos: motivos nuevos, especificaciones por clase y componentes ---
    from app.db.catalogos_semilla import sembrar, sembrar_componentes, sembrar_especificaciones
    sembrar(bind)
    sembrar_especificaciones(bind)
    sembrar_componentes(bind)

    _reinstalar_rls(bind)


def downgrade() -> None:
    bind = op.get_bind()
    from app.db.rls import uninstall_rls
    uninstall_rls(bind)

    # Eventos nuevos: el kardex es de solo inserción, así que no se borran; si
    # existen, la bajada se detiene (bajar perdería historia).
    usados = bind.execute(sa.text(
        'SELECT count(*) FROM "INV_KARDEX" WHERE "KDX_Tipo" IN (' + ", ".join(f"'{t}'" for t in _NUEVOS_EVENTOS) + ")"
    )).scalar()
    if usados:
        raise RuntimeError(f"El kardex ya tiene {usados} eventos nuevos: no se puede bajar sin perder historia")
    op.drop_constraint("ck_kardex_tipo", "INV_KARDEX", type_="check")
    op.create_check_constraint("ck_kardex_tipo", "INV_KARDEX", _tipos_kardex(False))

    bind.execute(sa.text(
        "UPDATE \"INV_APROBACION\" SET \"APR_Estado\" = 'ANULADA' "
        "WHERE \"APR_Tipo\" NOT IN ('BAJA', 'RECEPCION_SOBRANTE') AND \"APR_Estado\" = 'PENDIENTE'"))
    bind.execute(sa.text("DELETE FROM \"INV_APROBACION\" WHERE \"APR_Tipo\" NOT IN ('BAJA', 'RECEPCION_SOBRANTE')"))
    op.drop_constraint("ck_aprobacion_tipo", "INV_APROBACION", type_="check")
    op.create_check_constraint("ck_aprobacion_tipo", "INV_APROBACION", _APR_OLD)
    op.drop_index("ix_INV_APROBACION_CON_Consumible", table_name="INV_APROBACION")
    op.drop_column("INV_APROBACION", "CON_Consumible")

    op.drop_table("INV_FALTANTE")
    op.drop_table("INV_CONSUMIBLE_COMPATIBLE")
    op.drop_index("ix_INV_MOVIMIENTO_CONSUMIBLE_ACT_Activo", table_name="INV_MOVIMIENTO_CONSUMIBLE")
    op.drop_constraint("fk_moc_activo", "INV_MOVIMIENTO_CONSUMIBLE", type_="foreignkey")
    op.drop_column("INV_MOVIMIENTO_CONSUMIBLE", "ACT_Activo")

    # Los instalados vuelven a bodega (el estado «Instalado» desaparece).
    bind.execute(sa.text('''
        UPDATE "INV_ACTIVO" SET "EOP_Estado_Operativo" =
               (SELECT "EOP_Estado_Operativo" FROM "INV_ESTADO_OPERATIVO" WHERE "EOP_Nombre" ILIKE '%bodega%' LIMIT 1)
         WHERE "EOP_Estado_Operativo" IN
               (SELECT "EOP_Estado_Operativo" FROM "INV_ESTADO_OPERATIVO" WHERE lower("EOP_Nombre") = 'instalado')
    '''))
    bind.execute(sa.text('DELETE FROM "INV_ESTADO_OPERATIVO" WHERE lower("EOP_Nombre") = \'instalado\''))
    op.drop_index("ix_INV_ACTIVO_ACT_Responsable", table_name="INV_ACTIVO")
    op.drop_constraint("fk_activo_responsable", "INV_ACTIVO", type_="foreignkey")
    op.drop_column("INV_ACTIVO", "ACT_Responsable")
    op.drop_column("INV_ACTIVO", "ACT_Instalado")

    op.drop_table("INV_TIPO_COMPONENTE")
    op.drop_constraint("ck_tipo_activo_clase", "INV_TIPO_ACTIVO", type_="check")
    op.drop_column("INV_TIPO_ACTIVO", "TAC_Clase")

    from app.db.rls import install_rls
    install_rls(bind)

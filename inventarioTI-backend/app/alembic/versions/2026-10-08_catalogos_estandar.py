"""Catálogos estandarizados (plan de licencias y catálogos, bloque B, migración B1).

- Catálogos nuevos con la forma estándar (nombre único sin distinguir
  mayúsculas, código interno, orden, activo y referencia de fusión):
  motivos de movimiento, motivos y destinos de baja, unidades de medida,
  categorías de consumible, tipos de área, tipos de falla, acciones de
  mantenimiento y tipos de documento.
- Especificaciones que pide cada tipo de activo y kit estándar de puesto.
- Columnas de gestión (orden, activo, fusión) en marcas, tipos de conexión,
  de especificación, de mantenimiento y de evidencia (este último con código,
  unificado con las categorías de adjunto, cuyo CHECK fijo se elimina).
- Llaves hacia los catálogos en movimientos (+ detalle del motivo),
  mantenimientos, detalles de mantenimiento, consumibles, áreas, tipos de
  especificación y software. Los textos anteriores se conservan.
- Conversión de los datos existentes (texto libre → catálogo).

Revision ID: a7c1e2d3b4f5
Revises: f3a4b5c6d7e8
Create Date: 2026-10-08
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a7c1e2d3b4f5"
down_revision: Union[str, Sequence[str], None] = "f3a4b5c6d7e8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (tabla, prefijo, pk, columnas propias)
_CATALOGOS = [
    ("INV_MOTIVO_MOVIMIENTO", "MMO", "MMO_Motivo", [
        sa.Column("MMO_Aplica_A", sa.JSON(), nullable=True),
        sa.Column("MMO_Pide_Detalle", sa.Boolean(), nullable=False, server_default=sa.false()),
    ]),
    ("INV_MOTIVO_BAJA", "MBA", "MBA_Motivo", [
        sa.Column("MBA_Pide_Detalle", sa.Boolean(), nullable=False, server_default=sa.false()),
    ]),
    ("INV_DESTINO_BAJA", "DBA", "DBA_Destino", [
        sa.Column("DBA_Pide_Detalle", sa.Boolean(), nullable=False, server_default=sa.false()),
    ]),
    ("INV_UNIDAD_MEDIDA", "UME", "UME_Unidad", [sa.Column("UME_Simbolo", sa.String(20), nullable=True)]),
    ("INV_CATEGORIA_CONSUMIBLE", "CCO", "CCO_Categoria", []),
    ("INV_TIPO_AREA", "TAR", "TAR_Tipo_Area", []),
    ("INV_TIPO_FALLA", "TFA", "TFA_Tipo_Falla", [
        sa.Column("TFA_Pide_Detalle", sa.Boolean(), nullable=False, server_default=sa.false()),
    ]),
    ("INV_ACCION_MANTENIMIENTO", "AMA", "AMA_Accion", [
        sa.Column("AMA_Pide_Detalle", sa.Boolean(), nullable=False, server_default=sa.false()),
    ]),
    ("INV_TIPO_DOCUMENTO", "TDO", "TDO_Tipo_Documento", []),
]

# Catálogos existentes que reciben las columnas de gestión: (tabla, prefijo, pk, columnas nuevas extra)
_EXISTENTES = [
    ("INV_MARCA", "MAR", "MAR_Marca", [sa.Column("MAR_Descripcion", sa.String(255), nullable=True)]),
    ("INV_TIPO_CONEXION", "TCN", "TCN_Tipo_Conexion", []),
    ("INV_TIPO_ESPECIFICACION", "TES", "TES_Tipo_Especificacion", [
        sa.Column("TES_Descripcion", sa.String(255), nullable=True),
    ]),
    ("INV_TIPO_MANTENIMIENTO", "TMA", "TMA_Tipo_Mantenimiento", [
        sa.Column("TMA_Descripcion", sa.String(255), nullable=True),
    ]),
    ("INV_TIPO_EVIDENCIA", "TEV", "TEV_Tipo_Evidencia", [
        sa.Column("TEV_Codigo", sa.String(40), nullable=True),
        sa.Column("TEV_Descripcion", sa.String(255), nullable=True),
    ]),
]

# Llaves nuevas hacia los catálogos: (tabla, columna, tabla destino, columna destino)
_LLAVES = [
    ("INV_MOVIMIENTO", "MMO_Motivo", "INV_MOTIVO_MOVIMIENTO", "MMO_Motivo"),
    ("INV_MANTENIMIENTO", "TFA_Tipo_Falla", "INV_TIPO_FALLA", "TFA_Tipo_Falla"),
    ("INV_DETALLE_MANT", "AMA_Accion", "INV_ACCION_MANTENIMIENTO", "AMA_Accion"),
    ("INV_CONSUMIBLE", "CCO_Categoria", "INV_CATEGORIA_CONSUMIBLE", "CCO_Categoria"),
    ("INV_CONSUMIBLE", "UME_Unidad", "INV_UNIDAD_MEDIDA", "UME_Unidad"),
    ("INV_AREA", "TAR_Tipo_Area", "INV_TIPO_AREA", "TAR_Tipo_Area"),
    ("INV_SOFTWARE", "MAR_Marca", "INV_MARCA", "MAR_Marca"),
]


def _gestion(tabla: str, prefijo: str, pk: str) -> list[sa.Column]:
    return [
        sa.Column(f"{prefijo}_Orden", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(f"{prefijo}_Activo", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(f"{prefijo}_Fusionado_En", sa.Integer(),
                  sa.ForeignKey(f"{tabla}.{pk}", ondelete="SET NULL", name=f"fk_{prefijo.lower()}_fusionado_en"),
                  nullable=True),
        sa.Column(f"{prefijo}_Fusionado_Fecha", sa.DateTime(), nullable=True),
    ]


# Conversión del motivo de los movimientos (texto libre → catálogo).
_MOTIVOS_MOVIMIENTO = r'''
WITH clasificado AS (
    SELECT m."MOV_Movimiento" AS id, trim(m."MOV_Motivo") AS texto,
           CASE
             WHEN lower(trim(m."MOV_Motivo")) IN ('asignacion', 'asignación') THEN 'INGRESO_PERSONAL'
             WHEN lower(trim(m."MOV_Motivo")) LIKE 'renovaci%' THEN 'RENOVACION'
             WHEN lower(trim(m."MOV_Motivo")) = 'visita presencial' THEN 'VISITA_PRESENCIAL'
             ELSE 'OTRO'
           END AS codigo,
           -- Sin detalle si el texto era exactamente el motivo; si no, el texto original.
           lower(trim(m."MOV_Motivo")) IN ('asignacion', 'asignación', 'renovacion', 'renovación',
                                           'visita presencial') AS exacto
      FROM "INV_MOVIMIENTO" m
     WHERE m."MOV_Motivo" IS NOT NULL AND trim(m."MOV_Motivo") <> ''
)
UPDATE "INV_MOVIMIENTO" mv
   SET "MMO_Motivo" = mm."MMO_Motivo",
       "MOV_Motivo_Detalle" = CASE WHEN c.exacto THEN NULL ELSE left(c.texto, 255) END
  FROM clasificado c
  JOIN "INV_MOTIVO_MOVIMIENTO" mm ON mm."MMO_Codigo" = c.codigo
 WHERE mv."MOV_Movimiento" = c.id
'''

# Tipo de área por su nombre (el nivel de acceso ARE_Tipo_Acceso no cambia).
_TIPOS_AREA = r'''
UPDATE "INV_AREA" a
   SET "TAR_Tipo_Area" = t."TAR_Tipo_Area"
  FROM "INV_TIPO_AREA" t
 WHERE t."TAR_Codigo" = CASE
         WHEN lower(a."ARE_Nombre") LIKE '%bodega%' OR lower(a."ARE_Nombre") LIKE '%almac%' THEN 'BODEGA'
         WHEN lower(a."ARE_Nombre") LIKE '%home office%' OR lower(a."ARE_Nombre") LIKE '%remot%' THEN 'REMOTO'
         WHEN lower(a."ARE_Nombre") LIKE '%servicio t%cnico%' THEN 'SERVICIO_TECNICO'
         ELSE 'OFICINA'
       END
'''


def _texto_a_catalogo(bind, tabla: str, columna_texto: str, columna_fk: str,
                      catalogo: str, prefijo: str, pk: str, por_simbolo: bool = False) -> None:
    """
    Enlaza cada texto distinto con el registro del catálogo del mismo nombre
    (o símbolo); si no existe, lo crea como registro de usuario.
    """
    textos = bind.execute(sa.text(
        f'SELECT DISTINCT trim("{columna_texto}") FROM "{tabla}" '
        f'WHERE "{columna_texto}" IS NOT NULL AND trim("{columna_texto}") <> \'\' AND "{columna_fk}" IS NULL'
    )).scalars().all()
    for texto in textos:
        condicion = f'lower(trim("{prefijo}_Nombre")) = lower(:t)'
        if por_simbolo:
            condicion += f' OR "{prefijo}_Simbolo" = :t OR lower("{prefijo}_Codigo") = lower(:t)'
        destino = bind.execute(sa.text(
            f'SELECT "{pk}" FROM "{catalogo}" WHERE {condicion} ORDER BY "{pk}" LIMIT 1'), {"t": texto}).scalar()
        if destino is None:
            destino = bind.execute(sa.text(
                f'INSERT INTO "{catalogo}" ("{prefijo}_Nombre", "{prefijo}_Orden") '
                f'VALUES (:t, 1000) RETURNING "{pk}"'), {"t": texto[:100]}).scalar()
        bind.execute(sa.text(
            f'UPDATE "{tabla}" SET "{columna_fk}" = :d WHERE trim("{columna_texto}") = :t AND "{columna_fk}" IS NULL'
        ), {"d": destino, "t": texto})


def upgrade() -> None:
    bind = op.get_bind()

    for tabla, prefijo, pk, propias in _CATALOGOS:
        op.create_table(
            tabla,
            sa.Column(pk, sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(f"{prefijo}_Nombre", sa.String(100), nullable=False),
            sa.Column(f"{prefijo}_Codigo", sa.String(40), nullable=True, unique=True),
            sa.Column(f"{prefijo}_Descripcion", sa.String(255), nullable=True),
            *_gestion(tabla, prefijo, pk),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            *propias,
        )
        op.create_index(f"ix_{tabla}_{pk}", tabla, [pk])

    for tabla, prefijo, pk, propias in _EXISTENTES:
        for col in (*propias, *_gestion(tabla, prefijo, pk)):
            op.add_column(tabla, col)
    op.create_unique_constraint("uq_tipo_evidencia_codigo", "INV_TIPO_EVIDENCIA", ["TEV_Codigo"])
    op.add_column("INV_TIPO_ESPECIFICACION", sa.Column(
        "UME_Unidad", sa.Integer(),
        sa.ForeignKey("INV_UNIDAD_MEDIDA.UME_Unidad", ondelete="SET NULL", name="fk_tes_unidad"), nullable=True))

    op.create_table(
        "INV_ESPECIFICACION_TIPO_ACTIVO",
        sa.Column("ETA_Especificacion_Tipo", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("TAC_Tipo_Activo", sa.Integer(),
                  sa.ForeignKey("INV_TIPO_ACTIVO.TAC_Tipo_Activo", ondelete="CASCADE"), nullable=False),
        sa.Column("TES_Tipo_Especificacion", sa.Integer(),
                  sa.ForeignKey("INV_TIPO_ESPECIFICACION.TES_Tipo_Especificacion", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("ETA_Obligatoria", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("ETA_Valores", sa.JSON(), nullable=True),
        sa.Column("ETA_Orden", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("TAC_Tipo_Activo", "TES_Tipo_Especificacion", name="uq_especificacion_tipo"),
    )
    op.create_index("ix_INV_ESPECIFICACION_TIPO_ACTIVO_ETA_Especificacion_Tipo", "INV_ESPECIFICACION_TIPO_ACTIVO",
                    ["ETA_Especificacion_Tipo"])
    op.create_index("ix_INV_ESPECIFICACION_TIPO_ACTIVO_TAC_Tipo_Activo", "INV_ESPECIFICACION_TIPO_ACTIVO",
                    ["TAC_Tipo_Activo"])

    op.create_table(
        "INV_KIT_ESTANDAR",
        sa.Column("KIT_Kit", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("KIT_Nombre", sa.String(100), nullable=False),
        sa.Column("CAR_Cargo", sa.Integer(), sa.ForeignKey("INV_CARGO.CAR_Cargo", ondelete="CASCADE"), nullable=True),
        sa.Column("DEP_Departamento", sa.Integer(),
                  sa.ForeignKey("INV_DEPARTAMENTO.DEP_Departamento", ondelete="CASCADE"), nullable=True),
        sa.Column("KIT_Activo", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint('("CAR_Cargo" IS NULL) <> ("DEP_Departamento" IS NULL)', name="ck_kit_cargo_o_departamento"),
    )
    for col in ("KIT_Kit", "CAR_Cargo", "DEP_Departamento"):
        op.create_index(f"ix_INV_KIT_ESTANDAR_{col}", "INV_KIT_ESTANDAR", [col])
    op.create_table(
        "INV_KIT_ESTANDAR_ITEM",
        sa.Column("KIT_Item", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("KIT_Kit", sa.Integer(), sa.ForeignKey("INV_KIT_ESTANDAR.KIT_Kit", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("TAC_Tipo_Activo", sa.Integer(),
                  sa.ForeignKey("INV_TIPO_ACTIVO.TAC_Tipo_Activo", ondelete="CASCADE"), nullable=True),
        sa.Column("KIT_Cantidad", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("LIC_Licencia", sa.Integer(), sa.ForeignKey("INV_LICENCIA.LIC_Licencia", ondelete="CASCADE"),
                  nullable=True),
        sa.CheckConstraint('"KIT_Cantidad" BETWEEN 1 AND 50', name="ck_kit_item_cantidad"),
        sa.CheckConstraint('"TAC_Tipo_Activo" IS NOT NULL OR "LIC_Licencia" IS NOT NULL', name="ck_kit_item_algo"),
    )
    for col in ("KIT_Item", "KIT_Kit"):
        op.create_index(f"ix_INV_KIT_ESTANDAR_ITEM_{col}", "INV_KIT_ESTANDAR_ITEM", [col])

    for tabla, columna, destino, columna_destino in _LLAVES:
        op.add_column(tabla, sa.Column(columna, sa.Integer(), nullable=True))
        op.create_foreign_key(f"fk_{tabla[4:].lower()}_{columna.lower()}", tabla, destino, [columna],
                              [columna_destino], ondelete="RESTRICT")
        op.create_index(f"ix_{tabla}_{columna}", tabla, [columna])
    op.add_column("INV_MOVIMIENTO", sa.Column("MOV_Motivo_Detalle", sa.String(255), nullable=True))

    # Las categorías de adjunto pasan a ser los códigos del catálogo de tipos de evidencia.
    op.drop_constraint("ck_adjunto_categoria_valida", "INV_ADJUNTO", type_="check")

    # ── Datos ────────────────────────────────────────────────────────────────
    from app.db.catalogos_semilla import sembrar, sembrar_especificaciones
    sembrar(bind)

    op.execute(_MOTIVOS_MOVIMIENTO)
    op.execute(_TIPOS_AREA)
    _texto_a_catalogo(bind, "INV_TIPO_ESPECIFICACION", "TES_Unidad_Medida", "UME_Unidad",
                      "INV_UNIDAD_MEDIDA", "UME", "UME_Unidad", por_simbolo=True)
    _texto_a_catalogo(bind, "INV_CONSUMIBLE", "CON_Unidad", "UME_Unidad",
                      "INV_UNIDAD_MEDIDA", "UME", "UME_Unidad", por_simbolo=True)
    _texto_a_catalogo(bind, "INV_CONSUMIBLE", "CON_Categoria", "CCO_Categoria",
                      "INV_CATEGORIA_CONSUMIBLE", "CCO", "CCO_Categoria")
    # Fabricante del software → marca (se crean las que falten).
    _texto_a_catalogo(bind, "INV_SOFTWARE", "SOF_Fabricante", "MAR_Marca", "INV_MARCA", "MAR", "MAR_Marca")
    sembrar_especificaciones(bind)

    if bind.dialect.name == "postgresql":
        from app.db.unicidad_instalar import instalar_indices
        for nombre, n in instalar_indices(bind):
            print(f"AVISO: índice {nombre} no creado: {n} valores duplicados. Corríjalos y ejecute "
                  "python -m app.db.unicidad_instalar")
        from app.db.rls import install_rls
        if not install_rls(bind):
            print("AVISO: no se pudo reinstalar la capa RLS; ejecute python -m app.db.install_rls")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        from app.db.unicidad import INDICES_CI
        for nombre, tabla, *_ in INDICES_CI:
            if tabla in {t for t, *_r in _CATALOGOS} or tabla == "INV_TIPO_EVIDENCIA":
                op.execute(f'DROP INDEX IF EXISTS "{nombre}"')

    # Los adjuntos con categorías nuevas vuelven a «otro» para respetar el CHECK anterior.
    op.execute("UPDATE \"INV_ADJUNTO\" SET \"ADJ_Categoria\" = 'otro' "
               "WHERE \"ADJ_Categoria\" NOT IN ('factura', 'foto', 'acta', 'otro')")
    op.create_check_constraint("ck_adjunto_categoria_valida", "INV_ADJUNTO",
                               "\"ADJ_Categoria\" IN ('factura', 'foto', 'acta', 'otro')")

    op.drop_column("INV_MOVIMIENTO", "MOV_Motivo_Detalle")
    for tabla, columna, _d, _c in reversed(_LLAVES):
        op.drop_index(f"ix_{tabla}_{columna}", table_name=tabla)
        op.drop_constraint(f"fk_{tabla[4:].lower()}_{columna.lower()}", tabla, type_="foreignkey")
        op.drop_column(tabla, columna)

    op.drop_table("INV_KIT_ESTANDAR_ITEM")
    op.drop_table("INV_KIT_ESTANDAR")
    op.drop_table("INV_ESPECIFICACION_TIPO_ACTIVO")

    op.drop_constraint("fk_tes_unidad", "INV_TIPO_ESPECIFICACION", type_="foreignkey")
    op.drop_column("INV_TIPO_ESPECIFICACION", "UME_Unidad")
    op.drop_constraint("uq_tipo_evidencia_codigo", "INV_TIPO_EVIDENCIA", type_="unique")
    for tabla, prefijo, pk, propias in reversed(_EXISTENTES):
        # Las marcas creadas por la conversión del fabricante se conservan (pueden tener modelos).
        op.drop_constraint(f"fk_{prefijo.lower()}_fusionado_en", tabla, type_="foreignkey")
        for col in (*_gestion(tabla, prefijo, pk), *propias):
            op.drop_column(tabla, col.name)

    for tabla, _p, _pk, _c in reversed(_CATALOGOS):
        op.drop_table(tabla)

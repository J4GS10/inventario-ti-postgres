"""Licencias con activación variable (plan de licencias y catálogos, bloque A, migración A1).

- Catálogos: modelos comerciales, métodos de activación (qué pide cada
  credencial) y formas de conteo (por equipo, persona, concurrente, sitio).
- INV_LICENCIA: modelo comercial, método, forma de conteo, proveedor, fecha
  de inicio, renovación automática y notas. TLI_Tipo_Licencia pasa a ser
  opcional (alias de lectura del modelo comercial).
- INV_LICENCIA_CLAVE pasa a ser credencial o cupo: usuario, secreto cifrado,
  serial, servidor, archivo, dispositivo, vencimiento propio, último cambio de
  clave y «requiere cambio»; estados SUSPENDIDA y BAJA.
- INV_INSTALACION: INS_Consume_Cupo (las licencias por sitio no descuentan).
- INV_ADJUNTO: la licencia puede ser dueña de un archivo (método «Archivo»).
- Kardex: LICENCIA_CREDENCIAL_ALTA, LICENCIA_SECRETO_CAMBIADO y LICENCIA_SECRETO_VISTO.
- Datos: método «Product key» si la licencia tiene claves, «Sin credencial» si
  no; forma de conteo «Por equipo»; modelo comercial por el nombre del tipo.

Revision ID: b8d2f3e4a5c6
Revises: a7c1e2d3b4f5
Create Date: 2026-10-08
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b8d2f3e4a5c6"
down_revision: Union[str, Sequence[str], None] = "a7c1e2d3b4f5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CAMPOS = ("usuario", "secreto", "serial", "clave", "servidor", "archivo", "dispositivo")
NUEVOS_EVENTOS = ("LICENCIA_CREDENCIAL_ALTA", "LICENCIA_SECRETO_CAMBIADO", "LICENCIA_SECRETO_VISTO")


def _tipos_kardex(con_nuevos: bool) -> str:
    from app.models.kardex import TIPOS_EVENTO
    tipos = TIPOS_EVENTO if con_nuevos else tuple(t for t in TIPOS_EVENTO if t not in NUEVOS_EVENTOS)
    return '"KDX_Tipo" IN (' + ", ".join(f"'{t}'" for t in tipos) + ")"


def _catalogo(tabla: str, prefijo: str, pk: str, *propias: sa.Column) -> None:
    op.create_table(
        tabla,
        sa.Column(pk, sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(f"{prefijo}_Nombre", sa.String(100), nullable=False),
        sa.Column(f"{prefijo}_Codigo", sa.String(40), nullable=True, unique=True),
        sa.Column(f"{prefijo}_Descripcion", sa.String(255), nullable=True),
        sa.Column(f"{prefijo}_Orden", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(f"{prefijo}_Activo", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(f"{prefijo}_Fusionado_En", sa.Integer(),
                  sa.ForeignKey(f"{tabla}.{pk}", ondelete="SET NULL"), nullable=True),
        sa.Column(f"{prefijo}_Fusionado_Fecha", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        *propias,
    )
    op.create_index(f"ix_{tabla}_{pk}", tabla, [pk])


# Modelo comercial a partir del nombre del tipo de licencia anterior.
_MODELO_COMERCIAL = r'''
UPDATE "INV_LICENCIA" l
   SET "MCO_Modelo_Comercial" = m."MCO_Modelo_Comercial"
  FROM "INV_TIPO_LICENCIA" t, "INV_MODELO_COMERCIAL" m
 WHERE t."TLI_Tipo_Licencia" = l."TLI_Tipo_Licencia"
   AND m."MCO_Codigo" = CASE
         WHEN lower(t."TLI_Nombre") LIKE '%perpetu%' THEN 'PERPETUA'
         WHEN lower(t."TLI_Nombre") LIKE '%suscrip%' OR lower(t."TLI_Nombre") LIKE '%subscri%' THEN 'SUSCRIPCION'
         WHEN lower(t."TLI_Nombre") LIKE '%oem%' THEN 'OEM'
         WHEN lower(t."TLI_Nombre") LIKE '%volumen%' OR lower(t."TLI_Nombre") LIKE '%volume%' THEN 'VOLUMEN'
         WHEN lower(t."TLI_Nombre") LIKE '%saas%' THEN 'SAAS'
         WHEN lower(t."TLI_Nombre") LIKE '%prueba%' OR lower(t."TLI_Nombre") LIKE '%trial%' THEN 'PRUEBA'
         WHEN lower(t."TLI_Nombre") LIKE '%gratu%' OR lower(t."TLI_Nombre") LIKE '%free%'
              OR lower(t."TLI_Nombre") LIKE '%open%' THEN 'GRATUITA'
       END
'''

_METODO_Y_CONTEO = r'''
UPDATE "INV_LICENCIA" l
   SET "MAC_Metodo_Activacion" = (
         SELECT m."MAC_Metodo" FROM "INV_METODO_ACTIVACION" m
          WHERE m."MAC_Codigo" = CASE
                  WHEN l."LIC_Clave_Activacion" IS NOT NULL
                       OR EXISTS (SELECT 1 FROM "INV_LICENCIA_CLAVE" c WHERE c."LIC_Licencia" = l."LIC_Licencia")
                  THEN 'PRODUCT_KEY' ELSE 'SIN_CREDENCIAL' END),
       "FCO_Forma_Conteo" = (SELECT f."FCO_Forma" FROM "INV_FORMA_CONTEO" f WHERE f."FCO_Codigo" = 'EQUIPO')
'''


def upgrade() -> None:
    bind = op.get_bind()

    _catalogo("INV_MODELO_COMERCIAL", "MCO", "MCO_Modelo_Comercial")
    _catalogo(
        "INV_METODO_ACTIVACION", "MAC", "MAC_Metodo",
        *[sa.Column(f"MAC_Pide_{c.capitalize()}", sa.Boolean(), nullable=False, server_default=sa.false())
          for c in CAMPOS],
        *[sa.Column(f"MAC_Etiqueta_{c.capitalize()}", sa.String(60), nullable=True) for c in CAMPOS],
    )
    _catalogo("INV_FORMA_CONTEO", "FCO", "FCO_Forma")

    with op.batch_alter_table("INV_LICENCIA") as batch:
        batch.add_column(sa.Column("MCO_Modelo_Comercial", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("MAC_Metodo_Activacion", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("FCO_Forma_Conteo", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("LIC_Proveedor", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("LIC_Fecha_Inicio", sa.Date(), nullable=True))
        batch.add_column(sa.Column("LIC_Renovacion_Automatica", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("LIC_Notas", sa.String(1000), nullable=True))
        batch.alter_column("TLI_Tipo_Licencia", existing_type=sa.Integer(), nullable=True)
        batch.create_foreign_key("fk_licencia_modelo_comercial", "INV_MODELO_COMERCIAL",
                                 ["MCO_Modelo_Comercial"], ["MCO_Modelo_Comercial"], ondelete="RESTRICT")
        batch.create_foreign_key("fk_licencia_metodo", "INV_METODO_ACTIVACION",
                                 ["MAC_Metodo_Activacion"], ["MAC_Metodo"], ondelete="RESTRICT")
        batch.create_foreign_key("fk_licencia_forma_conteo", "INV_FORMA_CONTEO",
                                 ["FCO_Forma_Conteo"], ["FCO_Forma"], ondelete="RESTRICT")
        batch.create_foreign_key("fk_licencia_proveedor", "INV_PROVEEDOR",
                                 ["LIC_Proveedor"], ["PRV_Proveedor"], ondelete="SET NULL")

    op.add_column("INV_ADJUNTO", sa.Column("LIC_Licencia", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_adjunto_licencia", "INV_ADJUNTO", "INV_LICENCIA", ["LIC_Licencia"], ["LIC_Licencia"],
                          ondelete="CASCADE")
    op.create_index("ix_INV_ADJUNTO_LIC_Licencia", "INV_ADJUNTO", ["LIC_Licencia"])
    op.drop_constraint("ck_adjunto_un_solo_dueno", "INV_ADJUNTO", type_="check")
    op.create_check_constraint(
        "ck_adjunto_un_solo_dueno", "INV_ADJUNTO",
        '(("ACT_Activo" IS NOT NULL)::int + ("OCO_Orden" IS NOT NULL)::int + ("LIC_Licencia" IS NOT NULL)::int) = 1')

    with op.batch_alter_table("INV_LICENCIA_CLAVE") as batch:
        batch.alter_column("LCL_Clave_Activacion", existing_type=sa.String(1000), nullable=True)
        batch.alter_column("LCL_Clave_Hash", existing_type=sa.String(64), nullable=True)
        batch.add_column(sa.Column("LCL_Usuario", sa.String(255), nullable=True))
        batch.add_column(sa.Column("LCL_Secreto_Enc", sa.String(1000), nullable=True))
        batch.add_column(sa.Column("LCL_Serial", sa.String(255), nullable=True))
        batch.add_column(sa.Column("LCL_Servidor", sa.String(255), nullable=True))
        batch.add_column(sa.Column("LCL_Archivo", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("LCL_Dispositivo", sa.String(120), nullable=True))
        batch.add_column(sa.Column("LCL_Vence", sa.Date(), nullable=True))
        batch.add_column(sa.Column("LCL_Ultimo_Cambio_Secreto", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("LCL_Requiere_Cambio", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.create_foreign_key("fk_licencia_clave_archivo", "INV_ADJUNTO", ["LCL_Archivo"], ["ADJ_Adjunto"],
                                 ondelete="SET NULL")
        batch.drop_constraint("ck_licencia_clave_estado_valido", type_="check")
        batch.create_check_constraint(
            "ck_licencia_clave_estado_valido",
            "\"LCL_Estado\" IN ('DISPONIBLE', 'ASIGNADA', 'SUSPENDIDA', 'BAJA', 'RETIRADA')")

    op.add_column("INV_INSTALACION", sa.Column("INS_Consume_Cupo", sa.Boolean(), nullable=False,
                                               server_default=sa.true()))

    with op.batch_alter_table("INV_KARDEX") as batch:
        batch.drop_constraint("ck_kardex_tipo", type_="check")
        batch.create_check_constraint("ck_kardex_tipo", _tipos_kardex(True))

    # ── Datos ────────────────────────────────────────────────────────────────
    from app.db.catalogos_semilla import sembrar
    sembrar(bind, {"INV_MODELO_COMERCIAL", "INV_METODO_ACTIVACION", "INV_FORMA_CONTEO"})
    op.execute(_MODELO_COMERCIAL)
    op.execute(_METODO_Y_CONTEO)

    if bind.dialect.name == "postgresql":
        from app.db.unicidad_instalar import instalar_indices
        for nombre, n in instalar_indices(bind):
            print(f"AVISO: índice {nombre} no creado: {n} valores duplicados.")
        from app.db.rls import install_rls
        if not install_rls(bind):
            print("AVISO: no se pudo reinstalar la capa RLS; ejecute python -m app.db.install_rls")


def downgrade() -> None:
    bind = op.get_bind()
    # Eventos nuevos del kardex: el kardex es de solo inserción, así que si ya
    # hay eventos de estos tipos no se puede volver atrás sin perder historia.
    usados = bind.execute(sa.text(
        'SELECT count(*) FROM "INV_KARDEX" WHERE "KDX_Tipo" IN (' + ", ".join(f"'{t}'" for t in NUEVOS_EVENTOS) + ")"
    )).scalar() or 0
    if usados:
        raise RuntimeError(f"El kardex ya tiene {usados} eventos de credenciales: no se puede bajar esta migración.")
    with op.batch_alter_table("INV_KARDEX") as batch:
        batch.drop_constraint("ck_kardex_tipo", type_="check")
        batch.create_check_constraint("ck_kardex_tipo", _tipos_kardex(False))

    if bind.dialect.name == "postgresql":
        for nombre in ("uq_ci_modelo_comercial_nombre", "uq_ci_metodo_activacion_nombre", "uq_ci_forma_conteo_nombre"):
            op.execute(f'DROP INDEX IF EXISTS "{nombre}"')

    op.drop_column("INV_INSTALACION", "INS_Consume_Cupo")

    # Las credenciales sin clave (cuentas, seriales…) no caben en el esquema anterior.
    op.execute('DELETE FROM "INV_LICENCIA_CLAVE" WHERE "LCL_Clave_Activacion" IS NULL OR "LCL_Clave_Hash" IS NULL')
    op.execute("UPDATE \"INV_LICENCIA_CLAVE\" SET \"LCL_Estado\" = 'RETIRADA' WHERE \"LCL_Estado\" IN ('SUSPENDIDA', 'BAJA')")
    with op.batch_alter_table("INV_LICENCIA_CLAVE") as batch:
        batch.drop_constraint("ck_licencia_clave_estado_valido", type_="check")
        batch.create_check_constraint("ck_licencia_clave_estado_valido",
                                      "\"LCL_Estado\" IN ('DISPONIBLE', 'ASIGNADA', 'RETIRADA')")
        batch.drop_constraint("fk_licencia_clave_archivo", type_="foreignkey")
        for col in ("LCL_Requiere_Cambio", "LCL_Ultimo_Cambio_Secreto", "LCL_Vence", "LCL_Dispositivo", "LCL_Archivo",
                    "LCL_Servidor", "LCL_Serial", "LCL_Secreto_Enc", "LCL_Usuario"):
            batch.drop_column(col)
        batch.alter_column("LCL_Clave_Hash", existing_type=sa.String(64), nullable=False)
        batch.alter_column("LCL_Clave_Activacion", existing_type=sa.String(1000), nullable=False)

    # La política RLS de los adjuntos usa LIC_Licencia: se quita y se vuelve a
    # crear con la expresión anterior (activo u orden visibles).
    if bind.dialect.name == "postgresql":
        from app.core.config import settings
        from app.db.rls import POLICY_PREFIX, _ACTIVO_VISIBLE, _exists
        op.execute(f'DROP POLICY IF EXISTS "{POLICY_PREFIX}todo" ON "INV_ADJUNTO"')
    op.execute('DELETE FROM "INV_ADJUNTO" WHERE "LIC_Licencia" IS NOT NULL')
    op.drop_constraint("ck_adjunto_un_solo_dueno", "INV_ADJUNTO", type_="check")
    op.create_check_constraint(
        "ck_adjunto_un_solo_dueno", "INV_ADJUNTO",
        '("ACT_Activo" IS NOT NULL AND "OCO_Orden" IS NULL) OR ("ACT_Activo" IS NULL AND "OCO_Orden" IS NOT NULL)')
    op.drop_index("ix_INV_ADJUNTO_LIC_Licencia", table_name="INV_ADJUNTO")
    op.drop_constraint("fk_adjunto_licencia", "INV_ADJUNTO", type_="foreignkey")
    op.drop_column("INV_ADJUNTO", "LIC_Licencia")
    if bind.dialect.name == "postgresql":
        anterior = "({} OR {})".format(
            _ACTIVO_VISIBLE("INV_ADJUNTO"),
            _exists("INV_ORDEN_COMPRA", "o", 'o."OCO_Orden" = "INV_ADJUNTO"."OCO_Orden"'))
        op.execute(f'CREATE POLICY "{POLICY_PREFIX}todo" ON "INV_ADJUNTO" FOR ALL TO "{settings.DB_RLS_ROLE}" '
                   f"USING ({anterior}) WITH CHECK ({anterior})")

    # Licencias sin tipo anterior: se les asigna uno para restaurar el NOT NULL.
    op.execute("""
        INSERT INTO "INV_TIPO_LICENCIA" ("TLI_Nombre")
        SELECT 'Sin tipo' WHERE EXISTS (SELECT 1 FROM "INV_LICENCIA" WHERE "TLI_Tipo_Licencia" IS NULL)
           AND NOT EXISTS (SELECT 1 FROM "INV_TIPO_LICENCIA" WHERE "TLI_Nombre" = 'Sin tipo')
    """)
    op.execute("""
        UPDATE "INV_LICENCIA" SET "TLI_Tipo_Licencia" =
               (SELECT "TLI_Tipo_Licencia" FROM "INV_TIPO_LICENCIA" WHERE "TLI_Nombre" = 'Sin tipo')
         WHERE "TLI_Tipo_Licencia" IS NULL
    """)
    with op.batch_alter_table("INV_LICENCIA") as batch:
        for fk in ("fk_licencia_proveedor", "fk_licencia_forma_conteo", "fk_licencia_metodo",
                   "fk_licencia_modelo_comercial"):
            batch.drop_constraint(fk, type_="foreignkey")
        for col in ("LIC_Notas", "LIC_Renovacion_Automatica", "LIC_Fecha_Inicio", "LIC_Proveedor", "FCO_Forma_Conteo",
                    "MAC_Metodo_Activacion", "MCO_Modelo_Comercial"):
            batch.drop_column(col)
        batch.alter_column("TLI_Tipo_Licencia", existing_type=sa.Integer(), nullable=False)

    op.drop_table("INV_FORMA_CONTEO")
    op.drop_table("INV_METODO_ACTIVACION")
    op.drop_table("INV_MODELO_COMERCIAL")

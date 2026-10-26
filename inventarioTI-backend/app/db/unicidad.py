"""
Unicidad insensible a mayúsculas y espacios (QA1 A9/M2).

Antes solo existía la unicidad exacta de la base de datos: «Dell», «dell» y
« Dell » eran tres marcas distintas, y el mismo buzón de correo podía
pertenecer a dos personas (rompía Active Directory y los avisos). Aquí se
declaran índices únicos funcionales sobre lower(trim(columna)).

Cada índice lleva el código de error que ve el usuario: el manejador de
IntegrityError (app/main.py) traduce el nombre del índice violado a ese
código, así un duplicado nunca termina en el mensaje genérico de integridad.
Las comprobaciones previas de los servicios siguen existiendo para dar el
error antes de escribir; el índice es la garantía final (también ante
peticiones simultáneas y ediciones).
"""
from __future__ import annotations

from sqlalchemy import Index, func

import app.models.attachment  # noqa: F401  (registra todas las tablas)
import app.models.catalogo_estandar  # noqa: F401
import app.models.consumable  # noqa: F401
import app.models.procurement  # noqa: F401
from app.db.base import Base

# (nombre del índice, tabla, columnas de texto normalizadas, columnas tal cual, código de error)
INDICES_CI: list[tuple[str, str, tuple[str, ...], tuple[str, ...], str]] = [
    ("uq_ci_marca_nombre", "INV_MARCA", ("MAR_Nombre",), (), "BRAND_ALREADY_EXISTS"),
    ("uq_ci_modelo_nombre", "INV_MODELO", ("MOD_Nombre",), ("MAR_Marca",), "MODEL_ALREADY_EXISTS"),
    ("uq_ci_tipo_activo_nombre", "INV_TIPO_ACTIVO", ("TAC_Nombre",), (), "ASSET_TYPE_ALREADY_EXISTS"),
    ("uq_ci_tipo_activo_prefijo", "INV_TIPO_ACTIVO", ("TAC_Prefijo",), (), "ASSET_TYPE_PREFIX_ALREADY_EXISTS"),
    ("uq_ci_tipo_conexion_nombre", "INV_TIPO_CONEXION", ("TCN_Nombre",), (), "CONNECTION_TYPE_ALREADY_EXISTS"),
    ("uq_ci_estado_operativo_nombre", "INV_ESTADO_OPERATIVO", ("EOP_Nombre",), (), "OPERATIONAL_STATUS_ALREADY_EXISTS"),
    ("uq_ci_tipo_especificacion_nombre", "INV_TIPO_ESPECIFICACION", ("TES_Nombre",), (),
     "SPECIFICATION_TYPE_ALREADY_EXISTS"),
    ("uq_ci_pais_nombre", "INV_PAIS", ("PAI_Nombre",), (), "COUNTRY_ALREADY_EXISTS"),
    ("uq_ci_pais_iso", "INV_PAIS", ("PAI_Codigo_ISO",), (), "COUNTRY_ISO_ALREADY_EXISTS"),
    ("uq_ci_departamento_nombre", "INV_DEPARTAMENTO", ("DEP_Nombre",), (), "DEPARTMENT_ALREADY_EXISTS"),
    ("uq_ci_cargo_nombre", "INV_CARGO", ("CAR_Nombre",), (), "POSITION_ALREADY_EXISTS"),
    ("uq_ci_persona_email", "INV_PERSONA", ("PER_Email_Corporativo",), (), "EMAIL_ALREADY_EXISTS"),
    ("uq_ci_activo_serie", "INV_ACTIVO", ("ACT_Serie_Fabricante",), (), "SERIAL_NUMBER_ALREADY_EXISTS"),
    ("uq_ci_activo_codigo", "INV_ACTIVO", ("ACT_Codigo_Interno",), (), "ASSET_CODE_ALREADY_EXISTS"),
    ("uq_ci_consumible_nombre", "INV_CONSUMIBLE", ("CON_Nombre",), (), "CONSUMABLE_ALREADY_EXISTS"),
    ("uq_ci_proveedor_nombre", "INV_PROVEEDOR", ("PRV_Nombre",), (), "SUPPLIER_ALREADY_EXISTS"),
    ("uq_ci_orden_numero", "INV_ORDEN_COMPRA", ("OCO_Numero",), (), "PURCHASE_ORDER_NUMBER_EXISTS"),
    ("uq_ci_tipo_licencia_nombre", "INV_TIPO_LICENCIA", ("TLI_Nombre",), (), "LICENSE_TYPE_ALREADY_EXISTS"),
    ("uq_ci_software", "INV_SOFTWARE", ("SOF_Nombre", "SOF_Fabricante"), (), "SOFTWARE_ALREADY_EXISTS"),
    ("uq_ci_tipo_mantenimiento_nombre", "INV_TIPO_MANTENIMIENTO", ("TMA_Nombre",), (),
     "MAINTENANCE_TYPE_ALREADY_EXISTS"),
    ("uq_ci_tipo_movimiento_nombre", "INV_TIPO_MOVIMIENTO", ("TMO_Nombre",), (), "MOVEMENT_TYPE_ALREADY_EXISTS"),
    # Catálogos estándar (app/models/catalogo_estandar.py).
    *[(f"uq_ci_{tabla[4:].lower()}_nombre", tabla, (f"{prefijo}_Nombre",), (), "CATALOG_ITEM_ALREADY_EXISTS")
      for tabla, prefijo in (
          ("INV_MOTIVO_MOVIMIENTO", "MMO"), ("INV_MOTIVO_BAJA", "MBA"), ("INV_DESTINO_BAJA", "DBA"),
          ("INV_UNIDAD_MEDIDA", "UME"), ("INV_CATEGORIA_CONSUMIBLE", "CCO"), ("INV_TIPO_AREA", "TAR"),
          ("INV_TIPO_FALLA", "TFA"), ("INV_ACCION_MANTENIMIENTO", "AMA"), ("INV_TIPO_DOCUMENTO", "TDO"),
          ("INV_MODELO_COMERCIAL", "MCO"), ("INV_METODO_ACTIVACION", "MAC"), ("INV_FORMA_CONTEO", "FCO"),
      )],
    ("uq_ci_tipo_evidencia_nombre", "INV_TIPO_EVIDENCIA", ("TEV_Nombre",), (), "CATALOG_ITEM_ALREADY_EXISTS"),
]

# Restricciones exactas ya existentes → mismo código de error.
CODIGO_POR_RESTRICCION: dict[str, str] = {nombre: codigo for nombre, _t, _c, _e, codigo in INDICES_CI}
CODIGO_POR_RESTRICCION.update({
    "uq_activo_serie_fabricante": "SERIAL_NUMBER_ALREADY_EXISTS",
    "uq_software_nombre_version_fabr": "SOFTWARE_ALREADY_EXISTS",
    "uq_licencia_clave_hash": "LICENSE_KEY_ALREADY_EXISTS",
})


def registrar_indices() -> None:
    """Añade los índices a las tablas del metadata (idempotente)."""
    for nombre, tabla, texto, exactas, _codigo in INDICES_CI:
        t = Base.metadata.tables[tabla]
        if any(ix.name == nombre for ix in t.indexes):
            continue
        # Software: la versión forma parte de la identidad (Office 2019 ≠ 2021).
        extra = [func.coalesce(func.lower(func.trim(t.c.SOF_Version)), "")] if tabla == "INV_SOFTWARE" else []
        Index(nombre, *[t.c[c] for c in exactas], *[func.lower(func.trim(t.c[c])) for c in texto], *extra,
              unique=True)



def codigo_de_violacion(error: Exception) -> str | None:
    """Código de error para una violación de unicidad conocida (o None)."""
    orig = getattr(error, "orig", None)
    diag = getattr(orig, "diag", None)
    nombre = getattr(diag, "constraint_name", None) if diag is not None else None
    if not nombre:
        texto = str(orig or error)
        nombre = next((n for n in CODIGO_POR_RESTRICCION if n in texto), None)
    return CODIGO_POR_RESTRICCION.get(nombre) if nombre else None

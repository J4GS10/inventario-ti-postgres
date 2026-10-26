"""
Reconstrucción del kardex a partir de los registros que ya existían (se
ejecuta una vez en la migración de la Fase 4; es idempotente: solo corre si
el kardex no tiene eventos migrados).

Fuentes:
  - Alta / recepción de cada activo (enlace a la orden de compra).
  - Movimientos (asignación, préstamo, transferencia) y sus devoluciones.
  - Mantenimientos (entrada y salida, con costo y moneda).
  - Movimientos de consumibles (entradas, salidas y ajustes con su saldo).
  - Licencias (alta) e instalaciones (asignación y retiro).
  - Bajas (fecha de la bitácora si existe).

Todos los eventos quedan con KDX_Migrado = true. No se inventan datos: cuando
una fecha no existe (p. ej. el retiro de una licencia antes de registrarse),
se usa la mejor aproximación disponible y se indica en el detalle.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Connection

_SENTENCIAS = [
    # --- Alta o recepción de cada activo ---
    """
    INSERT INTO "INV_KARDEX" ("KDX_Fecha", "KDX_Tipo", "KDX_Objeto", "ACT_Activo", "KDX_Referencia",
        "KDX_Destino_Sede", "KDX_Costo", "KDX_Moneda", "KDX_Documento_Tipo", "KDX_Documento_Ref",
        "SED_Sede", "KDX_Migrado", "KDX_Detalle")
    SELECT COALESCE(ola."created_at", a."ACT_Fecha_Compra"::timestamp),
           CASE WHEN ola."ACT_Activo" IS NULL THEN 'ALTA' ELSE 'RECEPCION' END, 'ACTIVO',
           a."ACT_Activo", left(a."ACT_Codigo_Interno" || ' · ' || a."ACT_Serie_Fabricante", 200),
           a."SED_Sede", a."ACT_Costo", a."ACT_Moneda",
           CASE WHEN o."OCO_Numero" IS NULL THEN NULL ELSE 'ORDEN_COMPRA' END, o."OCO_Numero",
           a."SED_Sede", true, '{"fecha_aproximada": true}'::json
      FROM "INV_ACTIVO" a
      LEFT JOIN "INV_ORDEN_COMPRA_LINEA_ACTIVO" ola ON ola."ACT_Activo" = a."ACT_Activo"
      LEFT JOIN "INV_ORDEN_COMPRA_LINEA" l ON l."OCL_Linea" = ola."OCL_Linea"
      LEFT JOIN "INV_ORDEN_COMPRA" o ON o."OCO_Orden" = l."OCO_Orden"
    """,
    # --- Asignaciones, préstamos y transferencias ---
    """
    INSERT INTO "INV_KARDEX" ("KDX_Fecha", "KDX_Tipo", "KDX_Objeto", "ACT_Activo", "KDX_Referencia",
        "KDX_Destino_Persona", "KDX_Destino_Area", "KDX_Destino_Sede", "KDX_Documento_Tipo",
        "KDX_Documento_Ref", "KDX_Motivo", "SED_Sede", "KDX_Migrado")
    SELECT m."MOV_Fecha_Asignacion",
           CASE WHEN lower(t."TMO_Nombre") LIKE 'pr%stamo' THEN 'PRESTAMO'
                WHEN lower(t."TMO_Nombre") LIKE 'transferencia%' OR m."MOV_Observacion" = 'Transferencia de custodia'
                     THEN 'TRANSFERENCIA'
                ELSE 'ASIGNACION' END,
           'ACTIVO', a."ACT_Activo", left(a."ACT_Codigo_Interno" || ' · ' || a."ACT_Serie_Fabricante", 200),
           m."PER_Persona", m."ARE_Area", a."SED_Sede", 'MOVIMIENTO', m."MOV_Movimiento"::text,
           left(m."MOV_Observacion", 500), a."SED_Sede", true
      FROM "INV_MOVIMIENTO" m
      JOIN "INV_ACTIVO" a ON a."ACT_Activo" = m."ACT_Activo"
      LEFT JOIN "INV_TIPO_MOVIMIENTO" t ON t."TMO_Tipo_Movimiento" = m."TMO_Tipo_Movimiento"
    """,
    # --- Devoluciones (cierre de cada custodia) ---
    """
    INSERT INTO "INV_KARDEX" ("KDX_Fecha", "KDX_Tipo", "KDX_Objeto", "ACT_Activo", "KDX_Referencia",
        "KDX_Origen_Persona", "KDX_Origen_Area", "KDX_Origen_Sede", "KDX_Documento_Tipo",
        "KDX_Documento_Ref", "SED_Sede", "KDX_Migrado")
    SELECT m."MOV_Fecha_Devolucion", 'DEVOLUCION', 'ACTIVO', a."ACT_Activo",
           left(a."ACT_Codigo_Interno" || ' · ' || a."ACT_Serie_Fabricante", 200),
           m."PER_Persona", m."ARE_Area", a."SED_Sede", 'MOVIMIENTO', m."MOV_Movimiento"::text, a."SED_Sede", true
      FROM "INV_MOVIMIENTO" m
      JOIN "INV_ACTIVO" a ON a."ACT_Activo" = m."ACT_Activo"
     WHERE m."MOV_Fecha_Devolucion" IS NOT NULL
    """,
    # --- Mantenimiento ---
    """
    INSERT INTO "INV_KARDEX" ("KDX_Fecha", "KDX_Tipo", "KDX_Objeto", "ACT_Activo", "KDX_Referencia",
        "KDX_Origen_Sede", "KDX_Documento_Tipo", "KDX_Documento_Ref", "KDX_Motivo", "SED_Sede", "KDX_Migrado")
    SELECT mt."MAN_Fecha_Ingreso", 'MANTENIMIENTO_ENTRADA', 'ACTIVO', a."ACT_Activo",
           left(a."ACT_Codigo_Interno" || ' · ' || a."ACT_Serie_Fabricante", 200), a."SED_Sede",
           'TICKET', mt."MAN_Mantenimiento"::text, left(mt."MAN_Descripcion_Falla", 500), a."SED_Sede", true
      FROM "INV_MANTENIMIENTO" mt JOIN "INV_ACTIVO" a ON a."ACT_Activo" = mt."ACT_Activo"
    """,
    """
    INSERT INTO "INV_KARDEX" ("KDX_Fecha", "KDX_Tipo", "KDX_Objeto", "ACT_Activo", "KDX_Referencia",
        "KDX_Origen_Sede", "KDX_Costo", "KDX_Moneda", "KDX_Documento_Tipo", "KDX_Documento_Ref",
        "SED_Sede", "KDX_Migrado")
    SELECT mt."MAN_Fecha_Cierre", 'MANTENIMIENTO_SALIDA', 'ACTIVO', a."ACT_Activo",
           left(a."ACT_Codigo_Interno" || ' · ' || a."ACT_Serie_Fabricante", 200), a."SED_Sede",
           mt."MAN_Costo_Total", mt."MAN_Moneda", 'TICKET', mt."MAN_Mantenimiento"::text, a."SED_Sede", true
      FROM "INV_MANTENIMIENTO" mt JOIN "INV_ACTIVO" a ON a."ACT_Activo" = mt."ACT_Activo"
     WHERE mt."MAN_Fecha_Cierre" IS NOT NULL
    """,
    # --- Consumibles ---
    """
    INSERT INTO "INV_KARDEX" ("KDX_Fecha", "KDX_Tipo", "KDX_Objeto", "CON_Consumible", "KDX_Referencia",
        "KDX_Destino_Persona", "KDX_Destino_Sede", "KDX_Cantidad", "KDX_Saldo", "KDX_Motivo",
        "USU_Usuario", "SED_Sede", "KDX_Migrado")
    SELECT mc."MOC_Fecha", 'CONSUMIBLE_' || mc."MOC_Tipo", 'CONSUMIBLE', c."CON_Consumible",
           left(c."CON_Nombre", 200), mc."PER_Persona", c."SED_Sede",
           CASE WHEN mc."MOC_Tipo" = 'SALIDA' THEN mc."MOC_Cantidad" ELSE mc."MOC_Cantidad" END,
           mc."MOC_Stock_Resultante", mc."MOC_Motivo", mc."USU_Usuario", c."SED_Sede", true
      FROM "INV_MOVIMIENTO_CONSUMIBLE" mc JOIN "INV_CONSUMIBLE" c ON c."CON_Consumible" = mc."CON_Consumible"
    """,
    # --- Licencias: alta ---
    """
    INSERT INTO "INV_KARDEX" ("KDX_Fecha", "KDX_Tipo", "KDX_Objeto", "LIC_Licencia", "KDX_Referencia",
        "KDX_Cantidad", "KDX_Saldo", "KDX_Migrado", "KDX_Detalle")
    SELECT COALESCE((SELECT min(i."INS_Fecha_Instalacion")::timestamp FROM "INV_INSTALACION" i
                      WHERE i."LIC_Licencia" = l."LIC_Licencia"), now()),
           'LICENCIA_ALTA', 'LICENCIA', l."LIC_Licencia",
           left(s."SOF_Nombre" || COALESCE(' ' || s."SOF_Version", '') || ' · licencia #' || l."LIC_Licencia", 200),
           l."LIC_Cantidad_Total", 0, true, '{"fecha_aproximada": true}'::json
      FROM "INV_LICENCIA" l JOIN "INV_SOFTWARE" s ON s."SOF_Software" = l."SOF_Software"
    """,
    # --- Licencias: asignaciones ---
    """
    INSERT INTO "INV_KARDEX" ("KDX_Fecha", "KDX_Tipo", "KDX_Objeto", "LIC_Licencia", "LCL_Licencia_Clave",
        "KDX_Referencia", "KDX_Destino_Persona", "KDX_Destino_Activo", "KDX_Destino_Sede", "KDX_Cantidad",
        "SED_Sede", "KDX_Migrado")
    SELECT i."INS_Fecha_Instalacion"::timestamp, 'LICENCIA_ASIGNADA', 'LICENCIA', i."LIC_Licencia",
           i."LCL_Licencia_Clave", 'Licencia #' || i."LIC_Licencia", i."PER_Persona", i."ACT_Activo",
           COALESCE(a."SED_Sede", p."SED_Sede"), 1, COALESCE(a."SED_Sede", p."SED_Sede"), true
      FROM "INV_INSTALACION" i
      LEFT JOIN "INV_ACTIVO" a ON a."ACT_Activo" = i."ACT_Activo"
      LEFT JOIN "INV_PERSONA" p ON p."PER_Persona" = i."PER_Persona"
    """,
    # --- Licencias: retiros (sin fecha de retiro previa: aproximada) ---
    """
    INSERT INTO "INV_KARDEX" ("KDX_Fecha", "KDX_Tipo", "KDX_Objeto", "LIC_Licencia", "LCL_Licencia_Clave",
        "KDX_Referencia", "KDX_Origen_Persona", "KDX_Origen_Activo", "KDX_Origen_Sede", "KDX_Cantidad",
        "KDX_Motivo", "SED_Sede", "KDX_Migrado", "KDX_Detalle")
    SELECT COALESCE(i."INS_Fecha_Retiro", i."INS_Fecha_Instalacion"::timestamp), 'LICENCIA_RETIRADA', 'LICENCIA',
           i."LIC_Licencia", i."LCL_Licencia_Clave", 'Licencia #' || i."LIC_Licencia", i."PER_Persona",
           i."ACT_Activo", COALESCE(a."SED_Sede", p."SED_Sede"), 1, i."INS_Motivo_Retiro",
           COALESCE(a."SED_Sede", p."SED_Sede"), true,
           CASE WHEN i."INS_Fecha_Retiro" IS NULL THEN '{"fecha_aproximada": true}'::json ELSE NULL END
      FROM "INV_INSTALACION" i
      LEFT JOIN "INV_ACTIVO" a ON a."ACT_Activo" = i."ACT_Activo"
      LEFT JOIN "INV_PERSONA" p ON p."PER_Persona" = i."PER_Persona"
     WHERE i."INS_Estado" = false
    """,
    # --- Bajas ---
    """
    INSERT INTO "INV_KARDEX" ("KDX_Fecha", "KDX_Tipo", "KDX_Objeto", "ACT_Activo", "KDX_Referencia",
        "KDX_Origen_Sede", "KDX_Costo", "KDX_Moneda", "SED_Sede", "KDX_Migrado", "KDX_Detalle")
    SELECT COALESCE((SELECT max(au."AUD_Fecha_Hora") FROM "INV_AUDITORIA_SISTEMA" au
                      WHERE au."AUD_Accion" = 'DELETE_LOGIC'
                        AND au."AUD_Snapshot_JSON"->>'activo_id' = a."ACT_Activo"::text), now()),
           'BAJA', 'ACTIVO', a."ACT_Activo", left(a."ACT_Codigo_Interno" || ' · ' || a."ACT_Serie_Fabricante", 200),
           a."SED_Sede", a."ACT_Costo", a."ACT_Moneda", a."SED_Sede", true,
           '{"motivo": "OTRO", "destino": "RESGUARDO", "fecha_aproximada": true}'::json
      FROM "INV_ACTIVO" a
      JOIN "INV_ESTADO_OPERATIVO" e ON e."EOP_Estado_Operativo" = a."EOP_Estado_Operativo"
     WHERE lower(e."EOP_Nombre") = 'baja'
    """,
]


def reconstruir(conn: Connection) -> int:
    """Carga el historial existente en el kardex. Devuelve cuántos eventos creó."""
    if conn.dialect.name != "postgresql":
        return 0
    ya = conn.execute(text('SELECT count(*) FROM "INV_KARDEX" WHERE "KDX_Migrado"')).scalar()
    if ya:
        return 0
    antes = conn.execute(text('SELECT count(*) FROM "INV_KARDEX"')).scalar()
    for sentencia in _SENTENCIAS:
        conn.execute(text(sentencia))
    return int(conn.execute(text('SELECT count(*) FROM "INV_KARDEX"')).scalar() - antes)

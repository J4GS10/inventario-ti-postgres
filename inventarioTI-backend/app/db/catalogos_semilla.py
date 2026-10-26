"""
Filas de sistema de los catálogos estándar (plan de licencias y catálogos §4.1).

Cada fila lleva un código estable del que depende la lógica (la UI traduce su
nombre por el código). Se insertan por código solo si faltan: nunca se
renombra, reactiva ni borra lo que el usuario cambió. Se llama desde la
migración, desde `app.seed_min` en cada arranque y desde las pruebas.

Se escribe SQL explícito (solo las columnas listadas) para que una migración
antigua no dependa de columnas que agreguen migraciones posteriores.
"""
from __future__ import annotations

import json

from sqlalchemy import text
from sqlalchemy.engine import Connection


def _columnas(conn: Connection, tabla: str) -> set[str]:
    """Columnas existentes de la tabla."""
    sql = "SELECT column_name FROM information_schema.columns WHERE table_name = :t"
    return set(conn.execute(text(sql), {"t": tabla}).scalars())


def _json(conn: Connection, parametro: str) -> str:
    """Parámetro de una columna JSON."""
    return f"CAST(:{parametro} AS JSON)"

# (tabla, prefijo, [(código, nombre, {columna extra: valor})])
SEMILLAS: list[tuple[str, str, list[tuple[str, str, dict]]]] = [
    ("INV_MOTIVO_MOVIMIENTO", "MMO", [
        ("INGRESO_PERSONAL", "Ingreso de personal", {"MMO_Aplica_A": ["ASIGNACION"]}),
        ("RENOVACION", "Renovación", {"MMO_Aplica_A": ["ASIGNACION", "TRANSFERENCIA"]}),
        ("REEMPLAZO_FALLA", "Reemplazo por falla", {"MMO_Aplica_A": ["ASIGNACION", "TRANSFERENCIA"]}),
        ("VISITA_PRESENCIAL", "Visita presencial", {"MMO_Aplica_A": ["ASIGNACION", "PRESTAMO"]}),
        ("PRESTAMO_TEMPORAL", "Préstamo temporal", {"MMO_Aplica_A": ["PRESTAMO"]}),
        ("CAMBIO_PUESTO", "Cambio de puesto", {"MMO_Aplica_A": ["ASIGNACION", "TRANSFERENCIA", "DEVOLUCION"]}),
        ("RENUNCIA", "Salida de la persona", {"MMO_Aplica_A": ["DEVOLUCION"]}),
        ("CAMBIO_EQUIPO", "Cambio de equipo", {"MMO_Aplica_A": ["DEVOLUCION"]}),
        ("FIN_PRESTAMO", "Fin del préstamo", {"MMO_Aplica_A": ["DEVOLUCION"]}),
        ("FALLA", "Falla del equipo", {"MMO_Aplica_A": ["DEVOLUCION"]}),
        ("REASIGNACION", "Reasignación", {"MMO_Aplica_A": ["DEVOLUCION", "TRANSFERENCIA"]}),
        ("REVERTIDO", "Revertido", {"MMO_Aplica_A": ["DEVOLUCION"]}),
        ("PERDIDA_ROBO", "Pérdida o robo", {"MMO_Aplica_A": ["DEVOLUCION"], "MMO_Pide_Detalle": True}),
        # Equipos compartidos (impresora, switch…): instalar, reubicar y retirar.
        ("INSTALACION_NUEVA", "Instalación nueva", {"MMO_Aplica_A": ["INSTALACION"]}),
        ("REUBICACION", "Reubicación", {"MMO_Aplica_A": ["INSTALACION"]}),
        ("RETIRO_USO", "Fin de uso en el área", {"MMO_Aplica_A": ["RETIRO"]}),
        ("RETIRO_FALLA", "Retiro por falla", {"MMO_Aplica_A": ["RETIRO"]}),
        ("OTRO", "Otro", {"MMO_Pide_Detalle": True}),
    ]),
    ("INV_MOTIVO_BAJA", "MBA", [
        ("OBSOLESCENCIA", "Obsolescencia", {}),
        ("DANO_IRREPARABLE", "Daño irreparable", {}),
        ("ROBO", "Robo", {}),
        ("PERDIDA", "Pérdida", {}),
        ("FIN_ARRENDAMIENTO", "Fin de arrendamiento", {}),
        ("GARANTIA_REEMPLAZO", "Reemplazo por garantía", {}),
        ("RECLASIFICACION", "Reclasificado como consumible", {}),
        ("OTRO", "Otro", {"MBA_Pide_Detalle": True}),
    ]),
    ("INV_DESTINO_BAJA", "DBA", [
        ("DESTRUCCION", "Destrucción", {}),
        ("RECICLAJE", "Reciclaje", {}),
        ("DONACION", "Donación", {}),
        ("VENTA", "Venta", {}),
        ("DEVOLUCION_PROVEEDOR", "Devolución al proveedor", {}),
        ("RESGUARDO", "Resguardo en bodega", {}),
        ("NO_RECUPERADO", "No recuperado (pérdida o robo)", {}),
        ("CONSUMIBLES", "Pasa a consumibles", {}),
        ("OTRO", "Otro", {"DBA_Pide_Detalle": True}),
    ]),
    ("INV_UNIDAD_MEDIDA", "UME", [
        ("UNIDAD", "Unidad", {"UME_Simbolo": "u"}),
        ("CAJA", "Caja", {}),
        ("PAQUETE", "Paquete", {}),
        ("ROLLO", "Rollo", {}),
        ("LITRO", "Litro", {"UME_Simbolo": "l"}),
        ("ML", "Mililitro", {"UME_Simbolo": "ml"}),
        ("METRO", "Metro", {"UME_Simbolo": "m"}),
        ("KG", "Kilogramo", {"UME_Simbolo": "kg"}),
        ("G", "Gramo", {"UME_Simbolo": "g"}),
        ("GB", "Gigabyte", {"UME_Simbolo": "GB"}),
        ("TB", "Terabyte", {"UME_Simbolo": "TB"}),
        ("MHZ", "Megahercio", {"UME_Simbolo": "MHz"}),
        ("GHZ", "Gigahercio", {"UME_Simbolo": "GHz"}),
        ("PULGADAS", "Pulgadas", {"UME_Simbolo": "\""}),
        ("W", "Vatio", {"UME_Simbolo": "W"}),
        ("VA", "Voltamperio", {"UME_Simbolo": "VA"}),
        ("MAH", "Miliamperio hora", {"UME_Simbolo": "mAh"}),
        ("NUCLEOS", "Núcleos", {}),
        ("PORCENTAJE", "Porcentaje", {"UME_Simbolo": "%"}),
        ("CICLOS", "Ciclos", {}),
    ]),
    ("INV_CATEGORIA_CONSUMIBLE", "CCO", [
        ("LIMPIEZA", "Limpieza", {}),
        ("CABLES", "Cables", {}),
        ("TINTAS_TONER", "Tintas y tóner", {}),
        ("BATERIAS", "Baterías", {}),
        ("HERRAMIENTA", "Herramienta", {}),
        ("OTRO", "Otro", {}),
    ]),
    ("INV_TIPO_AREA", "TAR", [
        ("OFICINA", "Oficina", {}),
        ("BODEGA", "Bodega", {}),
        ("REMOTO", "Trabajo remoto", {}),
        ("SERVICIO_TECNICO", "Servicio técnico", {}),
        ("OTRO", "Otro", {}),
    ]),
    ("INV_TIPO_FALLA", "TFA", [
        ("NO_ENCIENDE", "No enciende", {}),
        ("PANTALLA", "Pantalla", {}),
        ("BATERIA", "Batería", {}),
        ("DISCO", "Disco", {}),
        ("TECLADO_TOUCHPAD", "Teclado o touchpad", {}),
        ("RED", "Red", {}),
        ("SOFTWARE_SO", "Software o sistema operativo", {}),
        ("DANO_FISICO", "Daño físico", {}),
        ("OTRO", "Otro", {"TFA_Pide_Detalle": True}),
    ]),
    ("INV_ACCION_MANTENIMIENTO", "AMA", [
        ("LIMPIEZA", "Limpieza", {}),
        ("CAMBIO_PIEZA", "Cambio de pieza", {}),
        ("REINSTALACION_SO", "Reinstalación del sistema operativo", {}),
        ("ACTUALIZACION", "Actualización", {}),
        ("GARANTIA", "Garantía", {}),
        ("OTRO", "Otro", {"AMA_Pide_Detalle": True}),
    ]),
    ("INV_TIPO_DOCUMENTO", "TDO", [
        ("FACTURA", "Factura", {}),
        ("TICKET", "Ticket", {}),
        ("ACTA", "Acta", {}),
        ("ORDEN_COMPRA", "Orden de compra", {}),
        ("RECEPCION", "Recepción", {}),
        ("MOVIMIENTO", "Movimiento", {}),
        ("BAJA", "Baja", {}),
        ("OFFBOARDING", "Salida del empleado", {}),
        ("TOMA_INVENTARIO", "Toma de inventario", {}),
        ("OTRO", "Otro", {}),
    ]),
    # Tipos de evidencia: unificados con las categorías de adjunto, cuyos
    # códigos en minúscula se conservan (INV_ADJUNTO.ADJ_Categoria).
    ("INV_TIPO_EVIDENCIA", "TEV", [
        ("factura", "Factura", {}),
        ("foto", "Fotografía", {}),
        ("acta", "Acta firmada", {}),
        ("reporte", "Reporte técnico", {}),
        ("otro", "Otro", {}),
    ]),
    # ── Licencias (bloque A) ──
    ("INV_MODELO_COMERCIAL", "MCO", [
        ("PERPETUA", "Perpetua", {}),
        ("SUSCRIPCION", "Suscripción", {}),
        ("OEM", "OEM", {}),
        ("VOLUMEN", "Volumen", {}),
        ("SAAS", "SaaS", {}),
        ("PRUEBA", "Prueba", {}),
        ("GRATUITA", "Gratuita / código abierto", {}),
    ]),
    ("INV_METODO_ACTIVACION", "MAC", [
        ("SERIAL", "Serial", {"MAC_Pide_Serial": True}),
        ("PRODUCT_KEY", "Product key", {"MAC_Pide_Clave": True}),
        ("SERIAL_KEY", "Serial + key", {"MAC_Pide_Serial": True, "MAC_Pide_Clave": True}),
        ("CUENTA", "Cuenta (correo + clave)", {"MAC_Pide_Usuario": True, "MAC_Pide_Secreto": True,
                                               "MAC_Etiqueta_Usuario": "Correo", "MAC_Etiqueta_Secreto": "Contraseña"}),
        ("SERVIDOR", "Servidor de licencias", {"MAC_Pide_Servidor": True, "MAC_Etiqueta_Servidor": "host:puerto"}),
        ("ARCHIVO", "Archivo de licencia", {"MAC_Pide_Archivo": True}),
        ("DONGLE", "Llave USB (dongle)", {"MAC_Pide_Dispositivo": True, "MAC_Pide_Serial": True}),
        ("TOKEN", "Token / API key", {"MAC_Pide_Secreto": True, "MAC_Etiqueta_Secreto": "Token"}),
        ("SIN_CREDENCIAL", "Sin credencial", {}),
    ]),
    ("INV_FORMA_CONTEO", "FCO", [
        ("EQUIPO", "Por equipo", {}),
        ("PERSONA", "Por persona (usuario nominal)", {}),
        ("CONCURRENTE", "Concurrente", {}),
        ("SITIO", "Por sitio / ilimitada", {}),
    ]),
]

# Códigos de sistema por tabla: no se cambian ni se desactivan, y la UI los traduce.
CODIGOS_SISTEMA: dict[str, frozenset[str]] = {
    tabla: frozenset(codigo for codigo, _n, _e in filas) for tabla, _p, filas in SEMILLAS
}


def sembrar(conn: Connection, tablas: set[str] | None = None) -> int:
    """Inserta las filas de sistema que falten. Devuelve cuántas creó."""
    creadas = 0
    for tabla, prefijo, filas in SEMILLAS:
        if tablas is not None and tabla not in tablas:
            continue
        # Solo columnas que ya existen: una migración antigua puede sembrar
        # antes de que otra posterior agregue columnas propias.
        presentes = _columnas(conn, tabla)
        if not presentes:
            continue
        existentes = {
            (c or "") for c in conn.execute(text(f'SELECT "{prefijo}_Codigo" FROM "{tabla}"')).scalars().all()
        }
        nombres = {
            (n or "").strip().lower() for n in conn.execute(text(f'SELECT "{prefijo}_Nombre" FROM "{tabla}"')).scalars()
        }
        for orden, (codigo, nombre, extras) in enumerate(filas, start=1):
            if codigo in existentes:
                continue
            if nombre.strip().lower() in nombres:
                # Ya existe con ese nombre (creado a mano o antes de los
                # códigos): se le asigna el código en vez de duplicarlo.
                conn.execute(
                    text(f'UPDATE "{tabla}" SET "{prefijo}_Codigo" = :c '
                         f'WHERE lower(trim("{prefijo}_Nombre")) = :n AND "{prefijo}_Codigo" IS NULL'),
                    {"c": codigo, "n": nombre.strip().lower()},
                )
                continue
            columnas = {f"{prefijo}_Codigo": codigo, f"{prefijo}_Nombre": nombre, f"{prefijo}_Orden": orden * 10}
            columnas.update({k: (json.dumps(v) if isinstance(v, list) else v)
                             for k, v in extras.items() if k in presentes})
            lista = ", ".join(f'"{c}"' for c in columnas)
            valores = ", ".join(
                _json(conn, c) if isinstance(extras.get(c), list) else f":{c}" for c in columnas
            )
            conn.execute(text(f'INSERT INTO "{tabla}" ({lista}) VALUES ({valores})'), columnas)
            creadas += 1
    return creadas


# Especificaciones que pide cada tipo de activo: (tipo, [(especificación, unidad, obligatoria)]).
# Las de un tipo concreto ganan; si no, se usa la plantilla de su clase.
ESPECIFICACIONES_POR_TIPO: list[tuple[str, list[tuple[str, str | None, bool]]]] = [
    ("Laptop", [("RAM", "GB", True), ("Almacenamiento", "GB", True), ("Procesador", None, True),
                ("Sistema Operativo", None, False)]),
    ("Desktop", [("RAM", "GB", True), ("Almacenamiento", "GB", True), ("Procesador", None, True),
                 ("Sistema Operativo", None, False)]),
    ("Monitor", [("Tamaño Pantalla", "PULGADAS", False)]),
    ("UPS", [("Potencia", "VA", False)]),
    ("Memoria RAM", [("RAM", "GB", True)]),
    ("Disco", [("Almacenamiento", "GB", True)]),
    ("Batería", [("Salud de batería", "PORCENTAJE", False)]),
]

# Plantilla por clase: (especificación, unidad, obligatoria, valores permitidos).
SI_NO = ["Sí", "No"]
ESPECIFICACIONES_POR_CLASE: dict[str, list[tuple[str, str | None, bool, list[str] | None]]] = {
    "COMPUTO": [("Procesador", None, True, None), ("RAM", "GB", True, None), ("Almacenamiento", "GB", True, None),
                ("Sistema Operativo", None, False, None), ("Dirección MAC", None, False, None)],
    "MOVIL": [("Número de IMEI", None, True, None), ("Número de línea", None, False, None),
              ("Sistema Operativo", None, False, None), ("Almacenamiento", "GB", False, None)],
    "IMPRESION": [("Tecnología de impresión", None, True, ["Láser", "Inyección de tinta", "Térmica", "Matriz de puntos"]),
                  ("Impresión a color", None, True, SI_NO), ("Dirección IP", None, False, None),
                  ("Dirección MAC", None, False, None), ("Contador de páginas", None, False, None)],
    "RED": [("Dirección IP", None, False, None), ("Dirección MAC", None, False, None),
            ("Puertos", None, False, None), ("Firmware", None, False, None)],
    "ENERGIA": [("Potencia", "VA", True, None)],
    "AUDIOVISUAL": [("Tamaño Pantalla", "PULGADAS", False, None), ("Dirección IP", None, False, None)],
}


def sembrar_especificaciones(conn: Connection, solo_tipo: int | None = None) -> int:
    """
    Especificaciones por tipo de activo (solo para los tipos que existen y aún
    no tienen ninguna configurada: nunca pisa lo que el usuario armó). Sin
    plantilla propia del tipo, se usa la de su clase.
    """
    creadas = 0
    tiene_clase = "TAC_Clase" in _columnas(conn, "INV_TIPO_ACTIVO")
    filas = conn.execute(text(
        'SELECT "TAC_Tipo_Activo", "TAC_Nombre", ' + ('"TAC_Clase"' if tiene_clase else "NULL")
        + ' FROM "INV_TIPO_ACTIVO"')).all()
    configurados = set(conn.execute(
        text('SELECT DISTINCT "TAC_Tipo_Activo" FROM "INV_ESPECIFICACION_TIPO_ACTIVO"')).scalars())
    unidades = dict(conn.execute(text('SELECT "UME_Codigo", "UME_Unidad" FROM "INV_UNIDAD_MEDIDA"')).all())
    simbolos = dict(conn.execute(text('SELECT "UME_Codigo", "UME_Simbolo" FROM "INV_UNIDAD_MEDIDA"')).all())
    por_tipo = {t.lower(): [(n, u, o, None) for n, u, o in esp] for t, esp in ESPECIFICACIONES_POR_TIPO}
    for tipo_id, tipo_nombre, clase in filas:
        if tipo_id in configurados or (solo_tipo is not None and tipo_id != solo_tipo):
            continue
        especificaciones = por_tipo.get(tipo_nombre.strip().lower()) or ESPECIFICACIONES_POR_CLASE.get(clase or "", [])
        for orden, (nombre, unidad, obligatoria, valores) in enumerate(especificaciones, start=1):
            tes = conn.execute(text('SELECT "TES_Tipo_Especificacion" FROM "INV_TIPO_ESPECIFICACION" '
                                    'WHERE lower(trim("TES_Nombre")) = :n'), {"n": nombre.lower()}).scalar()
            if tes is None:
                params = {"n": nombre, "s": simbolos.get(unidad) if unidad else None,
                          "u": unidades.get(unidad) if unidad else None}
                insertar = ('INSERT INTO "INV_TIPO_ESPECIFICACION" ("TES_Nombre", "TES_Unidad_Medida", "UME_Unidad") '
                            'VALUES (:n, :s, :u)')
                tes = conn.execute(text(insertar + ' RETURNING "TES_Tipo_Especificacion"'), params).scalar()
            params = {"t": tipo_id, "e": tes, "o": obligatoria, "r": orden * 10,
                      "v": json.dumps(valores) if valores else None}
            insertar = ('INSERT INTO "INV_ESPECIFICACION_TIPO_ACTIVO" '
                        '("TAC_Tipo_Activo", "TES_Tipo_Especificacion", "ETA_Obligatoria", "ETA_Orden", "ETA_Valores") '
                        f'VALUES (:t, :e, :o, :r, {_json(conn, "v")})')
            conn.execute(text(insertar + " ON CONFLICT DO NOTHING"), params)
            creadas += 1
    return creadas


def sembrar_componentes(conn: Connection) -> int:
    """Componentes internos por tipo (Laptop → RAM, disco…), solo si el tipo no tiene ninguno."""
    from app.core.clases_activo import COMPONENTES_POR_TIPO, _normalizar
    tipos = {_normalizar(n): i for i, n in conn.execute(
        text('SELECT "TAC_Tipo_Activo", "TAC_Nombre" FROM "INV_TIPO_ACTIVO"')).all()}
    con_lista = set(conn.execute(text('SELECT DISTINCT "TAC_Padre" FROM "INV_TIPO_COMPONENTE"')).scalars())
    creadas = 0
    for padre, componentes in COMPONENTES_POR_TIPO.items():
        padre_id = tipos.get(padre)
        if padre_id is None or padre_id in con_lista:
            continue
        for nombre in componentes:
            comp_id = tipos.get(nombre)
            if comp_id is None:
                continue
            insertar = 'INSERT INTO "INV_TIPO_COMPONENTE" ("TAC_Padre", "TAC_Componente") VALUES (:p, :c)'
            conn.execute(text(insertar + " ON CONFLICT DO NOTHING"), {"p": padre_id, "c": comp_id})
            creadas += 1
    return creadas

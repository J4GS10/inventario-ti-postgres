"""
Row Level Security (RLS) por sede en PostgreSQL — segunda capa del alcance
de datos (la primera está en app/core/data_scope.py).

Modelo:
- Las políticas se definen SOLO para el rol `settings.DB_RLS_ROLE` (sin login,
  sin BYPASSRLS, no dueño de las tablas). Cada transacción de una petición de
  usuario hace `SET LOCAL ROLE` a ese rol y fija `app.usuario`.
- El dueño de las tablas (migraciones, tareas del sistema) no se ve afectado:
  RLS habilitado sin FORCE.
- Sin `app.usuario`, el rol no ve nada (seguro por defecto).
- El alcance se lee de la BD en cada consulta (INV_USUARIO / INV_USUARIO_SEDE),
  no de lo que diga la aplicación: un cambio de sedes rige de inmediato.
- La bitácora (INV_AUDITORIA_SISTEMA) es de solo inserción para el rol.

`install_rls()` es idempotente: la usan la migración, el arranque en
instalaciones nuevas y los tests contra PostgreSQL.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.core.config import settings

POLICY_PREFIX = "inv_rls_"

# Columnas de INV_USUARIO que deciden el acceso: solo se escriben en modo
# elevado (ver install_rls).
_COLUMNAS_IDENTIDAD = frozenset({
    "USU_Usuario", "USU_Username", "USU_Rol", "USU_Alcance_Global", "USU_Estado", "PER_Persona",
})


def _sede(col: str) -> str:
    # Subconsultas escalares no correlacionadas: PostgreSQL las evalúa una sola
    # vez por consulta (InitPlan), no una por fila.
    # El cast convierte la subconsulta en expresión escalar: sin él, ANY la
    # tomaría como conjunto de filas (integer[]) y no como el arreglo.
    return f"((SELECT inv_rls_global()) OR {col} = ANY ((SELECT inv_rls_sedes())::integer[]))"


def _exists(table: str, alias: str, cond: str) -> str:
    # La subconsulta sobre otra tabla con RLS aplica también sus políticas.
    return f'EXISTS (SELECT 1 FROM "{table}" {alias} WHERE {cond})'


_ACTIVO_VISIBLE = lambda t: _exists("INV_ACTIVO", "a", f'a."ACT_Activo" = "{t}"."ACT_Activo"')  # noqa: E731

_FUNCTIONS = [
    """
    CREATE OR REPLACE FUNCTION inv_rls_usuario() RETURNS uuid
    LANGUAGE sql STABLE AS $$
        SELECT NULLIF(current_setting('app.usuario', true), '')::uuid
    $$
    """,
    """
    CREATE OR REPLACE FUNCTION inv_rls_global() RETURNS boolean
    LANGUAGE sql STABLE AS $$
        SELECT EXISTS (
            SELECT 1 FROM "INV_USUARIO" u
            WHERE u."USU_Usuario" = inv_rls_usuario()
              AND u."USU_Estado"
              AND (u."USU_Alcance_Global" OR u."USU_Rol" IN ('SUPER_ADMIN', 'ADMIN_SEGURIDAD'))
        )
    $$
    """,
    """
    CREATE OR REPLACE FUNCTION inv_rls_sedes() RETURNS integer[]
    LANGUAGE sql STABLE AS $$
        SELECT COALESCE(array_agg(us."SED_Sede"), '{}'::integer[])
        FROM "INV_USUARIO_SEDE" us
        JOIN "INV_USUARIO" u ON u."USU_Usuario" = us."USU_Usuario" AND u."USU_Estado"
        WHERE us."USU_Usuario" = inv_rls_usuario()
    $$
    """,
    """
    CREATE OR REPLACE FUNCTION inv_rls_persona_propia() RETURNS uuid
    LANGUAGE sql STABLE AS $$
        SELECT u."PER_Persona" FROM "INV_USUARIO" u WHERE u."USU_Usuario" = inv_rls_usuario()
    $$
    """,
    """
    CREATE OR REPLACE FUNCTION inv_rls_persona_sede(persona uuid) RETURNS integer
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
        -- SECURITY DEFINER: lee la sede sin pasar por la política de
        -- INV_PERSONA, que a su vez consulta INV_INSTALACION (evita el ciclo).
        SELECT p."SED_Sede" FROM "INV_PERSONA" p WHERE p."PER_Persona" = persona
    $$
    """,
    """
    CREATE OR REPLACE FUNCTION inv_rls_area_sede(area integer) RETURNS integer
    LANGUAGE sql STABLE AS $$
        SELECT e."SED_Sede"
        FROM "INV_AREA" ar
        JOIN "INV_NIVEL" n ON n."NIV_Nivel" = ar."NIV_Nivel"
        JOIN "INV_EDIFICIO" e ON e."EDI_Edificio" = n."EDI_Edificio"
        WHERE ar."ARE_Area" = area
    $$
    """,
]


def _policies(adjunto_de_licencia: bool = True,
              responsable_de_activo: bool = True) -> dict[str, list[tuple[str, str, str | None, str | None]]]:
    """
    `adjunto_de_licencia`: INV_ADJUNTO ya tiene LIC_Licencia (migración A1).
    `responsable_de_activo`: INV_ACTIVO ya tiene ACT_Responsable (migración e5a6).
    Las migraciones anteriores reinstalan la capa sin esas columnas.
    """
    """tabla -> [(nombre, comando, USING, WITH CHECK)]"""
    persona_relacionada = " OR ".join([
        _sede('"INV_PERSONA"."SED_Sede"'),
        '"INV_PERSONA"."PER_Persona" = (SELECT inv_rls_persona_propia())',
        _exists("INV_MOVIMIENTO", "m", 'm."PER_Persona" = "INV_PERSONA"."PER_Persona"'),
        _exists("INV_MANTENIMIENTO", "mt", 'mt."PER_Persona_Solicita" = "INV_PERSONA"."PER_Persona"'),
        _exists("INV_INSTALACION", "i", 'i."PER_Persona" = "INV_PERSONA"."PER_Persona"'),
        _exists("INV_MOVIMIENTO_CONSUMIBLE", "mc", 'mc."PER_Persona" = "INV_PERSONA"."PER_Persona"'),
    ] + ([
        # Responsable de un equipo compartido visible (impresora de su área).
        _exists("INV_ACTIVO", "ar", 'ar."ACT_Responsable" = "INV_PERSONA"."PER_Persona"'),
    ] if responsable_de_activo else []))
    persona_sede = _sede('"INV_PERSONA"."SED_Sede"')
    mant_visible = _exists(
        "INV_MANTENIMIENTO", "mt", 'mt."MAN_Mantenimiento" = "INV_DETALLE_MANT"."MAN_Mantenimiento"'
    )
    orden_visible = lambda t: _exists("INV_ORDEN_COMPRA", "o", f'o."OCO_Orden" = "{t}"."OCO_Orden"')  # noqa: E731
    consumible_visible = _exists(
        "INV_CONSUMIBLE", "c", 'c."CON_Consumible" = "INV_MOVIMIENTO_CONSUMIBLE"."CON_Consumible"'
    )
    mov_visible = _ACTIVO_VISIBLE("INV_MOVIMIENTO")
    mov_area_en_alcance = _sede('inv_rls_area_sede("INV_MOVIMIENTO"."ARE_Area")')
    evidencia_visible = "({} OR {})".format(
        _exists("INV_MOVIMIENTO", "m", 'm."MOV_Movimiento" = "INV_EVIDENCIA"."MOV_Movimiento_Ref"'),
        _exists("INV_MANTENIMIENTO", "mt", 'mt."MAN_Mantenimiento" = "INV_EVIDENCIA"."MAN_Mantenimiento_Ref"'),
    )
    # Los archivos de licencia no tienen sede (las licencias son globales).
    adjunto_visible = "({} OR {}{})".format(
        _ACTIVO_VISIBLE("INV_ADJUNTO"), orden_visible("INV_ADJUNTO"),
        " OR \"INV_ADJUNTO\".\"LIC_Licencia\" IS NOT NULL" if adjunto_de_licencia else "")
    # Una instalación va a un activo o, sin activo, directamente a una persona.
    instalacion_visible = "({} OR (\"INV_INSTALACION\".\"ACT_Activo\" IS NULL AND {}))".format(
        _ACTIVO_VISIBLE("INV_INSTALACION"),
        _sede('inv_rls_persona_sede("INV_INSTALACION"."PER_Persona")'),
    )
    linea_visible = _exists(
        "INV_ORDEN_COMPRA_LINEA", "l", 'l."OCL_Linea" = "INV_ORDEN_COMPRA_LINEA_ACTIVO"."OCL_Linea"'
    )
    kardex_visible = "({} OR {} OR {} OR {})".format(
        _sede('"INV_KARDEX"."SED_Sede"'),
        _sede('"INV_KARDEX"."KDX_Origen_Sede"'),
        _sede('"INV_KARDEX"."KDX_Destino_Sede"'),
        '"INV_KARDEX"."USU_Usuario" = (SELECT inv_rls_usuario())',
    )
    recepcion_linea_visible = _exists(
        "INV_RECEPCION", "r", 'r."REC_Recepcion" = "INV_RECEPCION_LINEA"."REC_Recepcion"'
    )
    compatible_visible = _exists(
        "INV_CONSUMIBLE", "c", 'c."CON_Consumible" = "INV_CONSUMIBLE_COMPATIBLE"."CON_Consumible"'
    )
    toma_visible = _exists(
        "INV_TOMA_INVENTARIO", "t", 't."TOM_Toma" = "INV_TOMA_LECTURA"."TOM_Toma"'
    )
    auditoria_visible = "({} OR {})".format(
        _sede('"INV_AUDITORIA_SISTEMA"."AUD_Sede"'),
        '"INV_AUDITORIA_SISTEMA"."USU_Usuario" = (SELECT inv_rls_usuario())',
    )
    return {
        "INV_ACTIVO": [("todo", "ALL", _sede('"INV_ACTIVO"."SED_Sede"'), _sede('"INV_ACTIVO"."SED_Sede"'))],
        "INV_PERSONA": [
            ("leer", "SELECT", persona_relacionada, None),
            ("crear", "INSERT", None, persona_sede),
            ("editar", "UPDATE", persona_sede, persona_sede),
            ("borrar", "DELETE", persona_sede, None),
        ],
        "INV_ESPECIFICACION": [("todo", "ALL", _ACTIVO_VISIBLE("INV_ESPECIFICACION"), _ACTIVO_VISIBLE("INV_ESPECIFICACION"))],
        "INV_MOVIMIENTO": [
            ("leer", "SELECT", mov_visible, None),
            # Una asignación nueva exige que el área de destino esté en el alcance.
            ("crear", "INSERT", None, f"{mov_visible} AND {mov_area_en_alcance}"),
            ("editar", "UPDATE", mov_visible, mov_visible),
            ("borrar", "DELETE", mov_visible, None),
        ],
        "INV_MANTENIMIENTO": [("todo", "ALL", _ACTIVO_VISIBLE("INV_MANTENIMIENTO"), _ACTIVO_VISIBLE("INV_MANTENIMIENTO"))],
        "INV_DETALLE_MANT": [("todo", "ALL", mant_visible, mant_visible)],
        "INV_EVIDENCIA": [("todo", "ALL", evidencia_visible, evidencia_visible)],
        "INV_INSTALACION": [("todo", "ALL", instalacion_visible, instalacion_visible)],
        "INV_ADJUNTO": [("todo", "ALL", adjunto_visible, adjunto_visible)],
        "INV_ORDEN_COMPRA": [("todo", "ALL", _sede('"INV_ORDEN_COMPRA"."SED_Sede"'), _sede('"INV_ORDEN_COMPRA"."SED_Sede"'))],
        "INV_ORDEN_COMPRA_LINEA": [
            ("todo", "ALL", orden_visible("INV_ORDEN_COMPRA_LINEA"), orden_visible("INV_ORDEN_COMPRA_LINEA")),
        ],
        "INV_ORDEN_COMPRA_LINEA_ACTIVO": [("todo", "ALL", linea_visible, linea_visible)],
        "INV_CONSUMIBLE": [("todo", "ALL", _sede('"INV_CONSUMIBLE"."SED_Sede"'), _sede('"INV_CONSUMIBLE"."SED_Sede"'))],
        "INV_MOVIMIENTO_CONSUMIBLE": [("todo", "ALL", consumible_visible, consumible_visible)],
        "INV_RECEPCION": [("todo", "ALL", orden_visible("INV_RECEPCION"), orden_visible("INV_RECEPCION"))],
        "INV_RECEPCION_LINEA": [("todo", "ALL", recepcion_linea_visible, recepcion_linea_visible)],
        "INV_TOMA_INVENTARIO": [
            ("todo", "ALL", _sede('"INV_TOMA_INVENTARIO"."SED_Sede"'), _sede('"INV_TOMA_INVENTARIO"."SED_Sede"')),
        ],
        "INV_TOMA_LECTURA": [("todo", "ALL", toma_visible, toma_visible)],
        "INV_FALTANTE": [("todo", "ALL", _ACTIVO_VISIBLE("INV_FALTANTE"), _ACTIVO_VISIBLE("INV_FALTANTE"))],
        "INV_CONSUMIBLE_COMPATIBLE": [("todo", "ALL", compatible_visible, compatible_visible)],
        # Solicitudes de aprobación: las de las sedes del alcance y las propias.
        "INV_APROBACION": [(
            "todo", "ALL",
            "({} OR \"INV_APROBACION\".\"APR_Solicitante\" = (SELECT inv_rls_usuario()))".format(
                _sede('"INV_APROBACION"."SED_Sede"')),
            "({} OR \"INV_APROBACION\".\"APR_Solicitante\" = (SELECT inv_rls_usuario()))".format(
                _sede('"INV_APROBACION"."SED_Sede"')),
        )],
        # Kardex: igual que la bitácora, de solo inserción.
        "INV_KARDEX": [
            ("leer", "SELECT", kardex_visible, None),
            ("registrar", "INSERT", None, "true"),
        ],
        # Bitácora: se lee lo de las sedes del alcance y lo propio; se inserta
        # siempre (toda acción debe quedar registrada); nunca se modifica ni borra.
        "INV_AUDITORIA_SISTEMA": [
            ("leer", "SELECT", auditoria_visible, None),
            ("registrar", "INSERT", None, "true"),
        ],
    }


def rls_tables() -> list[str]:
    return list(_policies())


def install_rls(conn: Connection, *, strict: bool = False) -> bool:
    """
    Crea (o recrea) rol, permisos, funciones y políticas. Devuelve False si el
    usuario de la migración no puede crear el rol; con `strict` lanza el error.
    """
    role = settings.DB_RLS_ROLE
    try:
        conn.execute(text(f"""
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                    CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS NOINHERIT;
                END IF;
            END $$;
        """))
    except Exception:
        if strict:
            raise
        return False

    # El usuario de conexión debe poder hacer SET ROLE al rol restringido.
    conn.execute(text(f"""
        DO $$
        BEGIN
            IF NOT pg_has_role(current_user, '{role}', 'MEMBER') THEN
                EXECUTE format('GRANT %I TO %I', '{role}', current_user);
            END IF;
        END $$;
    """))

    # Una migración intermedia puede reinstalar la capa antes de que existan
    # las tablas de migraciones posteriores: se omite lo que aún no existe.
    existentes = set(conn.execute(text(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()"
    )).scalars().all())

    def _si_existe(sql: str) -> bool:
        import re
        tablas = re.findall(r'ON "([A-Z_]+)"', sql)
        return all(t in existentes for t in tablas)

    for statement in (
        f'GRANT USAGE ON SCHEMA public TO "{role}"',
        f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "{role}"',
        f'GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO "{role}"',
        f'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "{role}"',
        f'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO "{role}"',
        # La bitácora es inmutable para las peticiones de usuario.
        f'REVOKE UPDATE, DELETE ON "INV_AUDITORIA_SISTEMA" FROM "{role}"',
        f'REVOKE UPDATE, DELETE ON "INV_KARDEX" FROM "{role}"',
        # Las solicitudes de aprobación no se borran: se resuelven o anulan.
        f'REVOKE DELETE ON "INV_APROBACION" FROM "{role}"',
        # Identidades: las políticas leen el alcance de INV_USUARIO e
        # INV_USUARIO_SEDE, así que el rol restringido no puede alterarlas (una
        # inyección SQL no puede darse alcance global ni sedes). Las altas y
        # cambios de rol/alcance/estado los hace la aplicación en modo elevado,
        # tras validar la jerarquía de roles.
        f'REVOKE INSERT, UPDATE, DELETE ON "INV_USUARIO_SEDE" FROM "{role}"',
        f'REVOKE INSERT, UPDATE, DELETE ON "INV_USUARIO" FROM "{role}"',
    ):
        if _si_existe(statement):
            conn.execute(text(statement))
    # Autoservicio (contraseña, MFA, intentos, último acceso…): se permite
    # actualizar las columnas de la cuenta que no deciden el acceso.
    columnas = [c for c in conn.execute(text(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'INV_USUARIO'"
    )).scalars().all() if c not in _COLUMNAS_IDENTIDAD]
    if columnas:
        lista = ", ".join(f'"{c}"' for c in columnas)
        conn.execute(text(f'GRANT UPDATE ({lista}) ON "INV_USUARIO" TO "{role}"'))

    for ddl in _FUNCTIONS:
        conn.execute(text(ddl))

    adjunto_de_licencia = conn.execute(text(
        "SELECT 1 FROM information_schema.columns WHERE table_name = 'INV_ADJUNTO' AND column_name = 'LIC_Licencia'"
    )).first() is not None
    responsable_de_activo = conn.execute(text(
        "SELECT 1 FROM information_schema.columns WHERE table_name = 'INV_ACTIVO' AND column_name = 'ACT_Responsable'"
    )).first() is not None
    for table, policies in _policies(adjunto_de_licencia, responsable_de_activo).items():
        if table not in existentes:
            continue
        conn.execute(text(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY'))
        existing = conn.execute(
            text("SELECT policyname FROM pg_policies WHERE tablename = :t AND policyname LIKE :p"),
            {"t": table, "p": f"{POLICY_PREFIX}%"},
        ).scalars().all()
        for name in existing:
            conn.execute(text(f'DROP POLICY "{name}" ON "{table}"'))
        for name, command, using, check in policies:
            sql = f'CREATE POLICY "{POLICY_PREFIX}{name}" ON "{table}" FOR {command} TO "{role}"'
            if using:
                sql += f" USING ({using})"
            if check:
                sql += f" WITH CHECK ({check})"
            conn.execute(text(sql))
    return True


def uninstall_rls(conn: Connection) -> None:
    for table in _policies():
        existing = conn.execute(
            text("SELECT policyname FROM pg_policies WHERE tablename = :t AND policyname LIKE :p"),
            {"t": table, "p": f"{POLICY_PREFIX}%"},
        ).scalars().all()
        for name in existing:
            conn.execute(text(f'DROP POLICY "{name}" ON "{table}"'))
        conn.execute(text(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY'))
    for fn in ("inv_rls_area_sede(integer)", "inv_rls_persona_sede(uuid)", "inv_rls_persona_propia()", "inv_rls_sedes()",
               "inv_rls_global()", "inv_rls_usuario()"):
        conn.execute(text(f"DROP FUNCTION IF EXISTS {fn}"))


def check_rls(conn: Connection) -> tuple[bool, str]:
    """Verificación de arranque: rol existente, concedido y políticas instaladas."""
    role = settings.DB_RLS_ROLE
    exists = conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role}).first()
    if not exists:
        return False, "role_missing"
    member = conn.execute(text("SELECT pg_has_role(current_user, :r, 'MEMBER')"), {"r": role}).scalar()
    if not member:
        return False, "role_not_granted"
    installed = set(conn.execute(
        text("SELECT DISTINCT tablename FROM pg_policies WHERE policyname LIKE :p"),
        {"p": f"{POLICY_PREFIX}%"},
    ).scalars().all())
    missing = [t for t in _policies() if t not in installed]
    if missing:
        return False, "policies_missing:" + ",".join(missing)
    return True, "ok"

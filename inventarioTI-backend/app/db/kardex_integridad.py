"""
Integridad verificable del kardex (PostgreSQL).

Tres defensas complementarias, instaladas por la migración y por las pruebas:

1. Solo inserción: UPDATE y DELETE se rechazan con un disparador (además de
   que el rol de la aplicación no tiene esos permisos).
2. Vaciado: TRUNCATE también se rechaza, salvo en una sesión de mantenimiento
   explícita (`SET inventario.mantenimiento = 'on'`), que usan las pruebas.
3. Sello encadenado: al insertar, la base asigna KDX_Secuencia (consecutiva,
   sin huecos, bajo un candado) y KDX_Hash = SHA-256(hash anterior + datos del
   evento). Cambiar, quitar o intercalar un evento rompe la cadena y
   `verificar()` lo detecta. Quien tenga privilegios de superusuario podría
   desactivar los disparadores, pero no recalcular la cadena sin que el último
   hash publicado (`ultimo_sello()`) deje de coincidir: conviene anotarlo en
   cada cierre mensual o exportarlo fuera del sistema.

Las funciones son SECURITY DEFINER: leen el último eslabón aunque la petición
corra con el rol restringido por sede (RLS).
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Connection

# Datos del evento que entran al hash: todas las columnas no nulas salvo el
# propio hash (jsonb ordena las claves, así el texto es determinista). Las
# columnas que se agreguen en el futuro, mientras sean nulas en los eventos
# viejos, no alteran sus hashes.
_FUNCIONES = r'''
CREATE OR REPLACE FUNCTION inv_kardex_hash(anterior text, evento "INV_KARDEX") RETURNS text
LANGUAGE sql IMMUTABLE AS $$
    SELECT encode(sha256(convert_to(
        coalesce(anterior, '') || '|' || (jsonb_strip_nulls(to_jsonb(evento) - 'KDX_Hash'))::text,
        'UTF8')), 'hex')
$$;

CREATE OR REPLACE FUNCTION inv_kardex_sellar() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
    ultimo record;
BEGIN
    -- Un solo sellado a la vez: la secuencia queda consecutiva y sin bifurcaciones.
    PERFORM pg_advisory_xact_lock(hashtext('INV_KARDEX_SELLO'));
    SELECT "KDX_Secuencia", "KDX_Hash" INTO ultimo
      FROM "INV_KARDEX" WHERE "KDX_Secuencia" IS NOT NULL
     ORDER BY "KDX_Secuencia" DESC LIMIT 1;
    NEW."KDX_Secuencia" := coalesce(ultimo."KDX_Secuencia", 0) + 1;
    NEW."KDX_Hash_Anterior" := ultimo."KDX_Hash";
    NEW."KDX_Hash" := NULL;
    NEW."KDX_Hash" := inv_kardex_hash(NEW."KDX_Hash_Anterior", NEW);
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION inv_kardex_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'INV_KARDEX es de solo inserción: % no permitido', TG_OP;
END $$;

CREATE OR REPLACE FUNCTION inv_kardex_sin_vaciar() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF coalesce(current_setting('inventario.mantenimiento', true), '') <> 'on' THEN
        RAISE EXCEPTION 'INV_KARDEX no se puede vaciar (TRUNCATE)';
    END IF;
    RETURN NULL;
END $$;
'''

_DISPARADORES = [
    'DROP TRIGGER IF EXISTS trg_kardex_sellar ON "INV_KARDEX"',
    'CREATE TRIGGER trg_kardex_sellar BEFORE INSERT ON "INV_KARDEX" '
    'FOR EACH ROW EXECUTE FUNCTION inv_kardex_sellar()',
    'DROP TRIGGER IF EXISTS trg_kardex_append_only ON "INV_KARDEX"',
    'CREATE TRIGGER trg_kardex_append_only BEFORE UPDATE OR DELETE ON "INV_KARDEX" '
    'FOR EACH ROW EXECUTE FUNCTION inv_kardex_append_only()',
    'DROP TRIGGER IF EXISTS trg_kardex_sin_vaciar ON "INV_KARDEX"',
    'CREATE TRIGGER trg_kardex_sin_vaciar BEFORE TRUNCATE ON "INV_KARDEX" '
    'FOR EACH STATEMENT EXECUTE FUNCTION inv_kardex_sin_vaciar()',
]

# Sella los eventos existentes en orden cronológico (una sola vez, en la migración).
_SELLAR_EXISTENTES = r'''
DO $$
DECLARE
    ev "INV_KARDEX";
    anterior text := NULL;
    n bigint := 0;
BEGIN
    FOR ev IN SELECT * FROM "INV_KARDEX" ORDER BY "KDX_Fecha", "KDX_Evento" LOOP
        n := n + 1;
        ev."KDX_Secuencia" := n;
        ev."KDX_Hash_Anterior" := anterior;
        ev."KDX_Hash" := NULL;
        ev."KDX_Hash" := inv_kardex_hash(anterior, ev);
        UPDATE "INV_KARDEX" SET "KDX_Secuencia" = n, "KDX_Hash_Anterior" = anterior, "KDX_Hash" = ev."KDX_Hash"
         WHERE "KDX_Evento" = ev."KDX_Evento";
        anterior := ev."KDX_Hash";
    END LOOP;
END $$;
'''

_VERIFICAR = text(r'''
WITH c AS (
    SELECT k."KDX_Evento", k."KDX_Secuencia", k."KDX_Hash", k."KDX_Hash_Anterior",
           lag(k."KDX_Hash") OVER w AS hash_previo,
           lag(k."KDX_Secuencia") OVER w AS secuencia_previa,
           inv_kardex_hash(k."KDX_Hash_Anterior", k) AS recalculado
      FROM "INV_KARDEX" k
    WINDOW w AS (ORDER BY k."KDX_Secuencia")
)
SELECT "KDX_Evento", "KDX_Secuencia",
       CASE
         WHEN "KDX_Secuencia" IS NULL THEN 'SIN_SELLO'
         WHEN "KDX_Hash" IS DISTINCT FROM recalculado THEN 'EVENTO_ALTERADO'
         WHEN "KDX_Hash_Anterior" IS DISTINCT FROM hash_previo THEN 'CADENA_ROTA'
         WHEN "KDX_Secuencia" <> coalesce(secuencia_previa, 0) + 1 THEN 'EVENTO_FALTANTE'
       END AS problema
  FROM c
 WHERE "KDX_Secuencia" IS NULL
    OR "KDX_Hash" IS DISTINCT FROM recalculado
    OR "KDX_Hash_Anterior" IS DISTINCT FROM hash_previo
    OR "KDX_Secuencia" <> coalesce(secuencia_previa, 0) + 1
 ORDER BY "KDX_Secuencia" NULLS FIRST
 LIMIT 50
''')


def instalar(conn: Connection, *, sellar_existentes: bool = False) -> None:
    """Crea funciones y disparadores. Con `sellar_existentes`, sella el histórico."""
    conn.execute(text(_FUNCIONES))
    if sellar_existentes:
        conn.execute(text('ALTER TABLE "INV_KARDEX" DISABLE TRIGGER USER'))
        conn.execute(text(_SELLAR_EXISTENTES))
        conn.execute(text('ALTER TABLE "INV_KARDEX" ENABLE TRIGGER USER'))
    for sql in _DISPARADORES:
        conn.execute(text(sql))


async def verificar(db) -> dict:
    """Recalcula la cadena completa. Devuelve el total, si está íntegra y los problemas."""
    total = (await db.execute(text('SELECT count(*) FROM "INV_KARDEX"'))).scalar_one()
    problemas = [
        {"evento": e, "secuencia": s, "problema": p}
        for e, s, p in (await db.execute(_VERIFICAR)).all()
    ]
    sello = await ultimo_sello(db)
    return {"total": total, "integro": not problemas, "problemas": problemas, **sello}


async def ultimo_sello(db) -> dict:
    """Último eslabón de la cadena: anotarlo fuera del sistema permite detectar reescrituras."""
    fila = (await db.execute(text(
        'SELECT "KDX_Secuencia", "KDX_Hash", "KDX_Fecha" FROM "INV_KARDEX" '
        'WHERE "KDX_Secuencia" IS NOT NULL ORDER BY "KDX_Secuencia" DESC LIMIT 1'
    ))).first()
    if not fila:
        return {"ultima_secuencia": 0, "ultimo_hash": None, "ultima_fecha": None}
    return {"ultima_secuencia": fila[0], "ultimo_hash": fila[1], "ultima_fecha": fila[2].isoformat()}

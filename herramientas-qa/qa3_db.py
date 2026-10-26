"""
QA3 — Verificación de la capa 2 (RLS de PostgreSQL) en el stack inv_ui.

1. Rol inventario_rls: sin superusuario, sin BYPASSRLS, no dueño de tablas.
2. Tablas con RLS y políticas (primario y réplica db_report).
3. Simulación de una petición (SET LOCAL ROLE + app.usuario) con las cuentas
   QA3: qué sedes ve cada una; sin app.usuario no ve nada; INSERT fuera del
   alcance rechazado; bitácora de solo inserción. Todo dentro de BEGIN/ROLLBACK.
4. Muestreo de pg_stat_activity mientras se generan peticiones reales por la
   API (primario y réplica): se buscan SET LOCAL ROLE / app.usuario.
5. Log del backend: rls.db_layer_active y rls.denied.
Salida: salidas/qa3/db_*.txt

Uso: python qa3_db.py
"""
import json
import os
import subprocess
import threading
import time
import warnings

import httpx

warnings.filterwarnings("ignore")
from qa_login import BASE, login  # noqa: E402

AQUI = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(AQUI, "salidas", "qa3")
EST = json.load(open(os.path.join(OUT, "estado.json"), encoding="utf-8"))
ENV = dict(os.environ, MSYS_NO_PATHCONV="1")


def psql(sql, cont="inv_ui-db-1"):
    p = subprocess.run(["docker", "exec", "-i", cont, "psql", "-U", "invuser", "-d", "inventario", "-X",
                        "-v", "ON_ERROR_STOP=0", "-P", "pager=off"],
                       input=sql, capture_output=True, text=True, encoding="utf-8", env=ENV)
    return p.stdout + p.stderr


def escribir(nombre, contenido):
    with open(os.path.join(OUT, nombre), "w", encoding="utf-8") as f:
        f.write(contenido)
    print(f"--- {nombre}\n{contenido[:3000]}\n")


def main():
    # 1-2. Rol y políticas
    base = """
\\echo '== roles'
SELECT rolname, rolsuper, rolbypassrls, rolcanlogin, rolinherit FROM pg_roles WHERE rolname IN ('invuser','inventario_rls');
\\echo '== dueño de las tablas con RLS'
SELECT DISTINCT tableowner FROM pg_tables WHERE tablename LIKE 'INV_%';
\\echo '== tablas con RLS (relrowsecurity) y nº de políticas'
SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, count(p.policyname) AS politicas
FROM pg_class c LEFT JOIN pg_policies p ON p.tablename = c.relname
WHERE c.relkind='r' AND c.relname LIKE 'INV_%' GROUP BY 1,2,3 ORDER BY 2 DESC, 1;
\\echo '== privilegios de escritura del rol restringido sobre tablas SIN RLS (identidades)'
SELECT table_name, string_agg(privilege_type, ',' ORDER BY privilege_type) FROM information_schema.role_table_grants
WHERE grantee='inventario_rls' AND table_name IN ('INV_USUARIO','INV_USUARIO_SEDE','INV_LICENCIA_CLAVE') GROUP BY 1;
"""
    escribir("db_1_roles_politicas.txt", psql(base))
    escribir("db_1b_politicas_replica.txt", psql(
        "SELECT pg_is_in_recovery(); SELECT count(*) AS politicas_inv_rls FROM pg_policies WHERE policyname LIKE 'inv_rls_%';",
        "inv_ui-db_report-1"))

    # 3. Simulación por cuenta
    sedes = {k: v["sede"] for k, v in EST["sedes"].items()}
    salida = [f"Sedes QA3: {sedes}\n"]
    for clave in ("qa3_tec_n", "qa3_aud_ns", "qa3_con_g", "qa3_ti_n", "qa3_seg"):
        uid = EST["usuarios"][clave]["id"]
        sql = f"""
BEGIN;
SET LOCAL ROLE inventario_rls;
SELECT set_config('app.usuario', '{uid}', true);
SELECT current_user AS rol_efectivo, session_user;
SELECT 'activos' t, "SED_Sede", count(*) FROM "INV_ACTIVO" GROUP BY 2 ORDER BY 2;
SELECT 'personas_por_sede' t, "SED_Sede", count(*) FROM "INV_PERSONA" GROUP BY 2 ORDER BY 2;
SELECT 'ordenes' t, "SED_Sede", count(*) FROM "INV_ORDEN_COMPRA" GROUP BY 2 ORDER BY 2;
SELECT 'consumibles' t, "SED_Sede", count(*) FROM "INV_CONSUMIBLE" GROUP BY 2 ORDER BY 2;
SELECT 'auditoria' t, "AUD_Sede", count(*) FROM "INV_AUDITORIA_SISTEMA" GROUP BY 2 ORDER BY 2;
ROLLBACK;
"""
        salida.append(f"\n######## {clave} ({EST['usuarios'][clave]['rol']})\n" + psql(sql))
    escribir("db_2_simulacion_alcance.txt", "".join(salida))

    tec = EST["usuarios"]["qa3_tec_n"]["id"]
    neg = f"""
\\echo '== sin app.usuario: el rol no ve nada'
BEGIN; SET LOCAL ROLE inventario_rls;
SELECT count(*) AS activos_visibles FROM "INV_ACTIVO";
SELECT count(*) AS auditoria_visible FROM "INV_AUDITORIA_SISTEMA";
ROLLBACK;
\\echo '== INSERT de activo en sede fuera del alcance (qa3_tec_n -> sede E)'
BEGIN; SET LOCAL ROLE inventario_rls; SELECT set_config('app.usuario', '{tec}', true);
INSERT INTO "INV_ACTIVO" ("ACT_Activo","ACT_Codigo_Interno","ACT_Serie_Fabricante","ACT_Fecha_Compra","MOD_Modelo","TAC_Tipo_Activo","EOP_Estado_Operativo","SED_Sede","ACT_Moneda")
VALUES (gen_random_uuid(),'QA3-DB-X','QA3-DB-X','2025-01-01',3,1,1,{sedes['E']},'GTQ');
ROLLBACK;
\\echo '== UPDATE de activo de sede ajena (0 filas afectadas esperado)'
BEGIN; SET LOCAL ROLE inventario_rls; SELECT set_config('app.usuario', '{tec}', true);
UPDATE "INV_ACTIVO" SET "ACT_Hostname"='QA3-DB-HACK' WHERE "SED_Sede"={sedes['E']};
ROLLBACK;
\\echo '== mover activo propio a sede ajena por UPDATE directo (WITH CHECK)'
BEGIN; SET LOCAL ROLE inventario_rls; SELECT set_config('app.usuario', '{tec}', true);
UPDATE "INV_ACTIVO" SET "SED_Sede"={sedes['E']} WHERE "ACT_Activo"='{EST['sedes']['N']['activo2']}';
ROLLBACK;
\\echo '== bitácora: UPDATE / DELETE desde el rol restringido'
BEGIN; SET LOCAL ROLE inventario_rls; SELECT set_config('app.usuario', '{tec}', true);
UPDATE "INV_AUDITORIA_SISTEMA" SET "AUD_Accion"='X' WHERE false;
ROLLBACK;
BEGIN; SET LOCAL ROLE inventario_rls; SELECT set_config('app.usuario', '{tec}', true);
DELETE FROM "INV_AUDITORIA_SISTEMA" WHERE false;
ROLLBACK;
\\echo '== identidades sin RLS: el rol restringido puede otorgarse alcance global (se revierte con ROLLBACK)'
BEGIN; SET LOCAL ROLE inventario_rls; SELECT set_config('app.usuario', '{tec}', true);
SELECT count(*) AS activos_antes FROM "INV_ACTIVO";
UPDATE "INV_USUARIO" SET "USU_Alcance_Global" = true WHERE "USU_Usuario"='{tec}';
SELECT count(*) AS activos_despues FROM "INV_ACTIVO";
ROLLBACK;
SELECT "USU_Alcance_Global" AS global_tras_rollback FROM "INV_USUARIO" WHERE "USU_Usuario"='{tec}';
"""
    escribir("db_3_negativos.txt", psql(neg))

    # 4. Muestreo de pg_stat_activity durante peticiones reales
    muestras = {"inv_ui-db-1": [], "inv_ui-db_report-1": []}
    parar = threading.Event()

    def muestrear(cont):
        sql = ("SELECT usename, state, left(query,120) FROM pg_stat_activity "
               "WHERE datname='inventario' AND (query ILIKE '%inventario_rls%' OR query ILIKE '%app.usuario%') "
               "AND pid <> pg_backend_pid();\n\\watch i=0.05 c=300\n")
        muestras[cont].append(psql(sql, cont))

    hilos = [threading.Thread(target=muestrear, args=(k,)) for k in muestras]
    for h in hilos:
        h.start()
    u = EST["usuarios"]["qa3_aud_n"]
    c = httpx.Client(verify=False, timeout=60)
    c.headers["Authorization"] = "Bearer " + login(c, u["username"], u["clave"])["access_token"]
    fin = time.time() + 14
    n = 0
    while time.time() < fin:
        for ruta in ("/core/activos?limit=200", "/trazabilidad/movimientos?limit=200", "/stats/dashboard",
                     "/org/personas", "/consumibles"):
            c.get(BASE + ruta)
            n += 1
    for h in hilos:
        h.join(timeout=60)
    res = [f"Peticiones enviadas por qa3_aud_n: {n}\n"]
    for cont, txt in muestras.items():
        lineas = [ln for ln in "".join(txt).splitlines() if "inventario_rls" in ln or "app.usuario" in ln]
        res.append(f"\n##### {cont}: {len(lineas)} muestras con SET LOCAL ROLE/app.usuario\n")
        res.extend(ln + "\n" for ln in sorted(set(lineas))[:15])
    escribir("db_4_pg_stat_activity.txt", "".join(res))

    # 5. Log del backend
    p = subprocess.run(["docker", "logs", "inv_ui-backend-1"], capture_output=True, text=True, encoding="utf-8",
                       errors="ignore", env=ENV)
    log = (p.stdout + p.stderr).splitlines()
    activos = [ln for ln in log if "rls.db_layer_active" in ln]
    negados = [ln for ln in log if "rls.denied" in ln]
    no_disp = [ln for ln in log if "rls.db_layer_unavailable" in ln]
    escribir("db_5_log_backend.txt",
             f"rls.db_layer_active: {len(activos)}\n" + "\n".join(activos[:8]) +
             f"\n\nrls.db_layer_unavailable: {len(no_disp)}\n" + "\n".join(no_disp[:5]) +
             f"\n\nrls.denied: {len(negados)}\n" + "\n".join(negados[-10:]) + "\n")


if __name__ == "__main__":
    main()

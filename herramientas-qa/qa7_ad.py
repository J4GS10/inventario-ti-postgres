"""QA7 — Active Directory simulado (Samba AD DC `qa7_samba`, dominio empresa.local, LDAPS).
Prueba de conexión, simulación y sincronización (altas, vínculo por correo/GUID, jefe,
departamento/cargo, desactivación solo sin activos), nombres compuestos, dominio .local,
grupos como destinatarios y cortacircuitos del AD.

Uso (cwd=herramientas-qa): python qa7_ad.py
La base DN es OU=QA7-Empleados: la sincronización real solo afecta a personas del LDAP de prueba.
"""
import json
import subprocess
import time
import uuid

from qa7_comun import (BASE, esperar_correos, get, guardar, integ_base, mh_borrar, psql, req, cliente)

R: dict = {}
SUF = time.strftime("%H%M%S")
c = cliente()
SVC_PASS = "Qa7-Svc-Passw0rd!"
OU = "OU=QA7-Empleados,DC=empresa,DC=local"


def ok(nombre, r):
    try:
        b = r.json()
    except Exception:  # noqa: BLE001
        b = r.text[:300]
    R[nombre] = {"status": r.status_code, "body": b}
    print(nombre, r.status_code, json.dumps(b, ensure_ascii=False)[:700])
    return b


def samba(*cmd):
    return subprocess.run(["docker", "exec", "qa7_samba", "samba-tool", *cmd], capture_output=True, text=True).stdout.strip()


AD = dict(modo="CORREO_AD", cuenta_email="svc_qa7@empresa.local", cuenta_password=SVC_PASS,
          ad_servidor="ldaps://dc01.empresa.local", ad_base_dn=OU, ad_grupo_base_dn="OU=QA7-Grupos,DC=empresa,DC=local",
          ad_grupo_admin="QA7-GG-Inventario-TI", ad_verificar_cert=False, ad_usar_cuenta_servicio=True, ad_desactivar=True)
cfg = integ_base(**AD)

# ---------------------------------------------------------------- 0. Personas previas (vínculo por correo / activos)
pre = {}
for alias, email, nom in (("vinculo", "qa7.vinculo@empresa.local", "QA7-Vinculo"),
                          ("conactivos", "qa7.conactivos@empresa.local", "QA7-Con"),
                          ("sinactivos", "qa7.sinactivos@empresa.local", "QA7-Sin")):
    r = req(c, "POST", "/org/personas", json={"PER_Primer_Nombre": nom, "PER_Primer_Apellido": f"Manual{SUF}",
                                              "PER_Email_Corporativo": email.upper() if alias == "vinculo" else email,
                                              "DEP_Departamento": 2, "CAR_Cargo": 2, "SED_Sede": 1})
    pre[alias] = r.json().get("PER_Persona") if r.status_code == 201 else r.text[:200]
R["A0_personas_previas"] = pre
act = req(c, "POST", "/core/activos", json={"ACT_Serie_Fabricante": f"QA7-AD-{SUF}", "ACT_Fecha_Compra": "2024-01-15",
                                             "MOD_Modelo": 3, "TAC_Tipo_Activo": 1, "EOP_Estado_Operativo": 1, "SED_Sede": 1}).json()
R["A0_asignacion_conactivos"] = req(c, "POST", "/trazabilidad/movimientos", json={
    "ACT_Activo": act["ACT_Activo"], "PER_Persona": pre["conactivos"], "ARE_Area": 1, "TMO_Tipo_Movimiento": 2}).status_code
R["A0_activo"] = act.get("ACT_Codigo_Interno")

# ---------------------------------------------------------------- 1. Probar conexión (sin guardar)
ok("A1_probar_ad_verificando_cert", req(c, "POST", "/directorio/integracion/probar-ad", json={"config": {**cfg, "ad_verificar_cert": True}}))
ok("A2_probar_ad_clave_mala", req(c, "POST", "/directorio/integracion/probar-ad", json={"config": {**cfg, "cuenta_password": "Mala-Clave-1!"}}))
R["A2_bloqueo_tras_prueba"] = get(c, "/directorio/integracion").json()["bloqueo"]
ok("A3_probar_ad_ldap_plano", req(c, "POST", "/directorio/integracion/probar-ad", json={"config": {**cfg, "ad_servidor": "ldap://dc01.empresa.local"}}))
ok("A3b_probar_ad_host_inexistente", req(c, "POST", "/directorio/integracion/probar-ad", json={"config": {**cfg, "ad_servidor": "ldaps://qa7-dc-no-existe.invalid"}}))
ok("A4_probar_ad_ok", req(c, "POST", "/directorio/integracion/probar-ad", json={"config": cfg}))
ok("A4b_validacion_url", req(c, "PUT", "/directorio/integracion", json={**cfg, "ad_servidor": "dc01.empresa.local"}))

# ---------------------------------------------------------------- 2. Guardar y estado
ok("A5_guardar_correo_ad", req(c, "PUT", "/directorio/integracion", json=cfg))
ok("A6_estado", get(c, "/directorio/estado"))
ok("A7_probar_conexion_guardada", req(c, "POST", "/directorio/probar"))
ok("A8_grupos_qa7", get(c, "/directorio/grupos", params={"q": "QA7"}))
ok("A8b_grupos_inyeccion", get(c, "/directorio/grupos", params={"q": "*)(cn=*"}))

# ---------------------------------------------------------------- 3. Simulación
antes = psql('select count(*) from "INV_PERSONA" where "PER_AD_GUID" is not null')
antes_dep = psql('select count(*) from "INV_DEPARTAMENTO"')
ok("A9_simulacion", req(c, "POST", "/directorio/sincronizar", params={"dry_run": True}))
R["A9_sin_cambios_en_bd"] = {"vinculadas_antes": antes, "vinculadas_despues": psql('select count(*) from "INV_PERSONA" where "PER_AD_GUID" is not null'),
                             "deps_antes": antes_dep, "deps_despues": psql('select count(*) from "INV_DEPARTAMENTO"')}
R["A9_estado_tras_simulacion"] = get(c, "/directorio/estado").json().get("last_sync")
R["A9_sin_departamento_existe"] = psql('select count(*) from "INV_DEPARTAMENTO" where lower("DEP_Nombre")=\'sin departamento\'')

# Para que la sincronización real solo cree catálogos con prefijo QA7-, se da departamento a Íñigo.

subprocess.run(["docker", "exec", "qa7_samba", "bash", "-c",
                "printf 'dn: CN=Íñigo Núñez de Balboa,OU=QA7-Empleados,DC=empresa,DC=local\\nchangetype: modify\\n"
                "replace: department\\ndepartment: QA7-Logística\\n-\\nreplace: title\\ntitle: QA7-Auxiliar\\n' > /tmp/i.ldif && "
                "ldbmodify -H /usr/local/samba/private/sam.ldb /tmp/i.ldif"], capture_output=True, text=True)

# ---------------------------------------------------------------- 4. Sincronización real
mh_borrar()
ok("A10_sincronizar", req(c, "POST", "/directorio/sincronizar"))
per = psql('select p."PER_Email_Corporativo", p."PER_Primer_Nombre", coalesce(p."PER_Segundo_Nombre",\'\'), p."PER_Primer_Apellido", '
           'coalesce(p."PER_Segundo_Apellido",\'\'), d."DEP_Nombre", ca."CAR_Nombre", coalesce(j."PER_Email_Corporativo",\'\'), '
           'p."PER_Estado", coalesce(p."SED_Sede"::text,\'(sin sede)\'), coalesce(p."PER_Telefono",\'\'), left(p."PER_AD_GUID",8) '
           'from "INV_PERSONA" p join "INV_DEPARTAMENTO" d on d."DEP_Departamento"=p."DEP_Departamento" '
           'join "INV_CARGO" ca on ca."CAR_Cargo"=p."CAR_Cargo" left join "INV_PERSONA" j on j."PER_Persona"=p."PER_Jefe" '
           'where p."PER_Email_Corporativo" ilike \'qa7.%@empresa.local\' and p."PER_Email_Corporativo" not like \'qa7.ana.%\' '
           'and p."PER_Email_Corporativo" not like \'qa7.jefa.%\' and p."PER_Email_Corporativo" not like \'qa7.beto.%\' order by 1')
cols = ["email", "nombre1", "nombre2", "apellido1", "apellido2", "departamento", "cargo", "jefe", "activa", "sede", "telefono", "guid"]
R["A11_personas_tras_sync"] = [dict(zip(cols, l.split("|"))) for l in per.splitlines()]
R["A11_departamentos_qa7"] = psql('select "DEP_Nombre" from "INV_DEPARTAMENTO" where "DEP_Nombre" ilike \'qa7-%\'').splitlines()
R["A11_cargos_qa7"] = psql('select "CAR_Nombre" from "INV_CARGO" where "CAR_Nombre" ilike \'qa7-%\'').splitlines()
R["A11_vinculo_mismo_id"] = psql(f'select count(*) from "INV_PERSONA" where "PER_Persona"=\'{pre["vinculo"]}\' and "PER_AD_GUID" is not null')
for p in R["A11_personas_tras_sync"]:
    print("  ", p)
ok("A12_resincronizar_idempotente", req(c, "POST", "/directorio/sincronizar", params={"dry_run": True}))

# ---------------------------------------------------------------- 5. Grupos como destinatarios
jc = psql('select "PER_Persona" from "INV_PERSONA" where "PER_Email_Corporativo"=\'qa7.jcperez@empresa.local\'')
req(c, "PUT", "/directorio/notificaciones/reglas/mantenimiento_abierto", json={
    "activa": True, "notificar_afectado": True, "notificar_jefe": True, "copiar_admins": True,
    "grupos_ad": ["QA7 Notificaciones Mantenimiento", "QA7-Grupo-Inexistente"], "correos_extra": []})
ok("A13_preview_grupos", get(c, "/directorio/notificaciones/vista-previa/mantenimiento_abierto", params={"persona_id": jc}))

# ---------------------------------------------------------------- 6. Deshabilitar en AD (con y sin activos)
samba("user", "disable", "qa7.conactivos")
samba("user", "disable", "qa7.sinactivos")
ok("A14_simulacion_bajas", req(c, "POST", "/directorio/sincronizar", params={"dry_run": True}))
mh_borrar()
ok("A15_sincronizar_bajas", req(c, "POST", "/directorio/sincronizar"))
R["A15_estado_personas"] = psql('select "PER_Email_Corporativo","PER_Estado" from "INV_PERSONA" where "PER_Email_Corporativo" in '
                                '(\'qa7.conactivos@empresa.local\',\'qa7.sinactivos@empresa.local\')').splitlines()
m = esperar_correos(lambda x: "Active Directory" in x["asunto"], timeout=60)
R["A15_correo_pendientes"] = [{"para": x["para"], "asunto": x["asunto"]} for x in m]
if m:
    guardar("correo_ad_sync_pendientes.html", m[0]["html"])
samba("user", "enable", "qa7.sinactivos")
ok("A16_reactivacion_simulada", req(c, "POST", "/directorio/sincronizar", params={"dry_run": True}))

# ---------------------------------------------------------------- 7. Base DN que no devuelve usuarios (solo simulación)
req(c, "PUT", "/directorio/integracion", json={**cfg, "ad_base_dn": "OU=QA7-Grupos,DC=empresa,DC=local"})
ok("A17_simulacion_base_dn_vacia", req(c, "POST", "/directorio/sincronizar", params={"dry_run": True}))
req(c, "PUT", "/directorio/integracion", json=cfg)

# ---------------------------------------------------------------- 8. Cortacircuitos del AD
req(c, "PUT", "/directorio/integracion", json={**cfg, "cuenta_password": "Clave-Rota-Qa7!"})
ok("A18_sync_clave_mala", req(c, "POST", "/directorio/sincronizar", params={"dry_run": True}))
R["A18_bloqueo"] = get(c, "/directorio/integracion").json()["bloqueo"]
ok("A19_sync_en_pausa", req(c, "POST", "/directorio/sincronizar", params={"dry_run": True}))
ok("A19b_probar_en_pausa", req(c, "POST", "/directorio/probar"))
ok("A19c_preview_grupos_en_pausa", get(c, "/directorio/notificaciones/vista-previa/mantenimiento_abierto", params={"persona_id": jc}))
r = req(c, "POST", "/directorio/integracion/desbloquear")
R["A20_desbloquear"] = r.status_code
R["A20_bloqueo_tras_desbloquear_x8"] = [get(c, "/directorio/integracion").json()["bloqueo"]["ad_hasta"] for _ in range(8)]
req(c, "PUT", "/directorio/integracion", json=cfg)  # clave correcta
ok("A21_sync_tras_corregir", req(c, "POST", "/directorio/sincronizar", params={"dry_run": True}))
guardar("ad.json", R)

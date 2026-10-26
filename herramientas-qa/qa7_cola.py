"""QA7 — Cola de correos (outbox): caída y recuperación del SMTP con backoff, estado FALLIDO,
reintento manual, correos con secretos fuera de la cola (reset y 2FA por correo) y
mantenimiento cerrado con costo/moneda.

Uso (cwd=herramientas-qa):  python qa7_cola.py
Nota: para no esperar 7 reintentos (~10 h) se adelanta EOB_Intentos=6 SOLO en la fila QA7 creada aquí.
"""
import json
import subprocess
import time
import uuid

import httpx

from qa7_comun import (BASE, esperar_correos, get, guardar, integ_base, mh_borrar, psql, req, cliente)

R: dict = {}
SUF = time.strftime("%H%M%S")
c = cliente()
datos = json.load(open("salidas/qa7/correo_eventos.json", encoding="utf-8"))["datos"]
CON = json.load(open("salidas/qa7/correo_eventos.json", encoding="utf-8"))["E08_consumible"]["body"]["CON_Consumible"]


def fila(eid):
    s = psql('select "EOB_Estado","EOB_Intentos","EOB_Proximo_Intento","EOB_Ultimo_Error","EOB_Enviado_En",'
             f'(now() at time zone \'utc\')::text from "SYS_EMAIL_OUTBOX" where "EOB_Id"=\'{eid}\'')
    k = ["estado", "intentos", "proximo", "error", "enviado", "ahora"]
    return dict(zip(k, s.split("|")))


def ultima_fila(plantilla, desde):
    return psql(f'select "EOB_Id" from "SYS_EMAIL_OUTBOX" where "EOB_Plantilla"=\'{plantilla}\' and "EOB_Creado_En">=\'{desde}\' '
                'order by "EOB_Creado_En" desc limit 1')


def ahora():
    return psql("select (now() at time zone 'utc')::text")


def salida():
    """Disparador sin efectos de negocio: digest de garantías (regla QA7 → qa7.admins@empresa.local)."""
    return req(c, "POST", "/compras/garantias/notificar", params={"dias": 7}).status_code


def docker(*a):
    return subprocess.run(["docker", *a], capture_output=True, text=True).returncode


mh_borrar()
# Asegurar config CORREO → MailHog
req(c, "PUT", "/directorio/integracion", json=integ_base(cuenta_password=None))

# ------------------------------------------------ Q1: SMTP caído → reintentos con backoff → recuperación
t0 = ahora()
docker("stop", "qa7_mailhog")
R["Q1_salida"] = salida()
time.sleep(8)
eid = ultima_fila("garantia_por_vencer", t0)
R["Q1_id"] = eid
R["Q1_tras_1er_intento"] = fila(eid)
time.sleep(75)
R["Q1_tras_2do_intento"] = fila(eid)
docker("start", "qa7_mailhog")
time.sleep(3)
R["Q1_mailhog_arriba"] = ahora()
fin = time.time() + 420
while time.time() < fin and fila(eid)["estado"] != "ENVIADO":
    time.sleep(10)
R["Q1_final"] = fila(eid)
print("Q1", json.dumps({k: R[k] for k in R if k.startswith("Q1")}, ensure_ascii=False, indent=1))

# ------------------------------------------------ Q2: FALLIDO y reintento manual
t0 = ahora()
docker("stop", "qa7_mailhog")
salida()
time.sleep(8)
eid2 = ultima_fila("garantia_por_vencer", t0)
R["Q2_id"] = eid2
R["Q2_tras_1er_intento"] = fila(eid2)
# Acelerar: fila QA7 propia → intento 6 de 7, vencida ya
psql(f'update "SYS_EMAIL_OUTBOX" set "EOB_Intentos"=6, "EOB_Proximo_Intento"=now() at time zone \'utc\' where "EOB_Id"=\'{eid2}\'')
fin = time.time() + 90
while time.time() < fin and fila(eid2)["estado"] != "FALLIDO":
    time.sleep(5)
R["Q2_fallido"] = fila(eid2)
R["Q2_api_resumen"] = get(c, "/gov/email-outbox").json()
docker("start", "qa7_mailhog")
time.sleep(3)
R["Q2_reintentar_id_inexistente"] = req(c, "POST", "/gov/email-outbox/reintentar", params={"outbox_id": str(uuid.uuid4())}).json()
R["Q2_reintentar"] = req(c, "POST", "/gov/email-outbox/reintentar", params={"outbox_id": eid2}).json()
fin = time.time() + 120
while time.time() < fin and fila(eid2)["estado"] != "ENVIADO":
    time.sleep(5)
R["Q2_final"] = fila(eid2)
R["Q2_api_resumen_final"] = get(c, "/gov/email-outbox").json()
print("Q2", json.dumps({k: R[k] for k in R if k.startswith("Q2")}, ensure_ascii=False, indent=1, default=str)[:4000])

# ------------------------------------------------ Q3: mantenimiento cerrado con costo y moneda (regla con afectado)
req(c, "PUT", "/directorio/notificaciones/reglas/mantenimiento_cerrado", json={
    "activa": True, "notificar_afectado": True, "notificar_jefe": True, "copiar_admins": True, "grupos_ad": [],
    "correos_extra": ["qa7.admins@empresa.local"]})
act = req(c, "POST", "/core/activos", json={
    "ACT_Serie_Fabricante": f"QA7-SN2-{SUF}", "ACT_Fecha_Compra": "2024-01-15", "ACT_Costo": "999.99", "ACT_Moneda": "GTQ",
    "MOD_Modelo": 3, "TAC_Tipo_Activo": 1, "EOP_Estado_Operativo": 1, "SED_Sede": 1}).json()
R["Q3_activo"] = act.get("ACT_Codigo_Interno")
req(c, "POST", "/trazabilidad/movimientos", json={"ACT_Activo": act["ACT_Activo"], "PER_Persona": datos["afectada"],
                                                  "ARE_Area": 1, "TMO_Tipo_Movimiento": 2})
for costo, mon in (("12500.00", "CHF"), ("0", None)):
    man = req(c, "POST", "/mantenimiento/", json={"ACT_Activo": act["ACT_Activo"], "PER_Persona_Solicita": datos["afectada"],
                                                  "TMA_Tipo_Mantenimiento": 1, "MAN_Descripcion_Falla": "QA7 cierre " + costo,
                                                  "MAN_Moneda": "GTQ"},
              headers={"Idempotency-Key": str(uuid.uuid4())}).json()
    body = {"MAN_Costo_Total": costo}
    if mon:
        body["MAN_Moneda"] = mon
    R[f"Q3_cierre_{costo}"] = req(c, "PATCH", f"/mantenimiento/{man['MAN_Mantenimiento']}/cerrar", json=body).status_code
m = esperar_correos(lambda x: "Mantenimiento cerrado" in x["asunto"], timeout=60)
time.sleep(5)
m = esperar_correos(lambda x: "Mantenimiento cerrado" in x["asunto"], timeout=5)
import re, html as _h
R["Q3_correos"] = [{"para": x["para"], "asunto": x["asunto"],
                    "texto": _h.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", re.sub(r"<style.*?</style>", "", x["html"], flags=re.S))))[:400]}
                   for x in m]
for i, x in enumerate(m):
    guardar(f"correo_mant_cerrado_{i}.html", x["html"])
print("Q3", json.dumps(R["Q3_correos"], ensure_ascii=False, indent=1))

# ------------------------------------------------ Q4: secretos fuera de la cola (reset y 2FA por correo)
usuario = f"qa7ana{SUF}"
clave_tmp = f"Qa7-Tmp-{SUF}-Xy!"
u = req(c, "POST", "/org/usuarios", json={"USU_Username": usuario, "USU_Password": clave_tmp, "USU_Rol": "CONSULTA",
                                           "PER_Persona": datos["afectada"], "sedes": [1]})
R["Q4_usuario"] = {"status": u.status_code, "body": u.text[:300]}
t0 = ahora()
mh_borrar()
r = httpx.post(f"{BASE}/login/password-reset/request", json={"identifier": usuario}, verify=False)
R["Q4_reset_request"] = {"status": r.status_code, "body": r.json()}
mr = esperar_correos(lambda x: "Restablecimiento" in x["asunto"], timeout=40)
token = None
if mr:
    mm = re.search(r"token=([A-Za-z0-9_\-\.]+)", mr[0]["html"])
    token = mm.group(1) if mm else None
R["Q4_reset_correo"] = {"recibido": bool(mr), "asunto": mr[0]["asunto"] if mr else None, "token_len": len(token or "")}
R["Q4_reset_en_cola"] = psql(f'select count(*) from "SYS_EMAIL_OUTBOX" where "EOB_Creado_En">=\'{t0}\' and '
                             f'("EOB_Plantilla" in (\'password_reset\',\'2fa_code\') or "EOB_Html" like \'%{token or "@@@"}%\')')

# Primer ingreso del usuario QA7 (contraseña temporal) → cambio → 2FA por correo
u2 = httpx.Client(verify=False, timeout=60)
lg = u2.post(f"{BASE}/login/access-token", data={"username": usuario, "password": clave_tmp})
R["Q4_login_tmp"] = {"status": lg.status_code, "claves": list(lg.json().keys()) if lg.status_code == 200 else lg.text[:200]}
nueva = f"Qa7-Nueva-{SUF}-Zw#9"
if lg.status_code == 200 and lg.json().get("access_token"):
    u2.headers["Authorization"] = "Bearer " + lg.json()["access_token"]
    cp = u2.post(f"{BASE}/me/password", json={"current_password": clave_tmp, "new_password": nueva})
    R["Q4_cambio_clave"] = cp.status_code
    lg2 = u2.post(f"{BASE}/login/access-token", data={"username": usuario, "password": nueva})
    R["Q4_login2"] = lg2.status_code
    if lg2.status_code == 200 and lg2.json().get("access_token"):
        u2.headers["Authorization"] = "Bearer " + lg2.json()["access_token"]
        t1 = ahora()
        st = u2.post(f"{BASE}/me/2fa/email/setup")
        R["Q4_2fa_email_setup"] = {"status": st.status_code, "body": st.text[:200]}
        m2 = esperar_correos(lambda x: "verificaci" in x["asunto"].lower(), timeout=40)
        code = None
        if m2:
            mm = re.search(r"(\d{6})", m2[0]["asunto"])
            code = mm.group(1) if mm else None
        R["Q4_2fa_correo"] = {"recibido": bool(m2), "asunto_contiene_codigo": bool(code), "asunto": (m2[0]["asunto"][:30] + "******") if m2 else None}
        R["Q4_2fa_en_cola"] = psql(f'select count(*) from "SYS_EMAIL_OUTBOX" where "EOB_Creado_En">=\'{t1}\'')
        logs = subprocess.run(["docker", "logs", "--since", "5m", "inv_ui-backend-1"], capture_output=True, text=True,
                              encoding="utf-8", errors="ignore")
        txt = logs.stdout + logs.stderr
        R["Q4_codigo_2fa_en_logs"] = bool(code and code in txt)
        R["Q4_token_reset_en_logs"] = bool(token and token in txt)
        lineas = [l for l in txt.splitlines() if code and code in l]
        guardar("logs_2fa_codigo.txt", "\n".join(l.replace(code, "[CODIGO-6-DIGITOS]") for l in lineas))
guardar("cola.json", R)
print(json.dumps({k: v for k, v in R.items() if k.startswith("Q4")}, ensure_ascii=False, indent=1, default=str))

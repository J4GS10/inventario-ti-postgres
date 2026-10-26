"""QA7 — Correo: configuración de la cuenta de servicio (MailHog), secretos, reglas,
vista previa y plantillas reales de cada evento (entrega de la cola).

Uso (cwd=herramientas-qa):  python qa7_correo.py
Requiere qa7_mailhog en la red inv_ui_internal. Crea datos con prefijo QA7-.
"""
import json
import time
import uuid

from qa7_comun import (BASE, cliente, esperar_correos, get, guardar, integ_base, mh_borrar, psql, req)

R: dict = {}
SUF = time.strftime("%H%M%S")
c = cliente()


def ok(nombre, r, esperado=(200, 201, 202, 204)):
    body = None
    try:
        body = r.json()
    except Exception:  # noqa: BLE001
        body = r.text[:500]
    R[nombre] = {"status": r.status_code, "body": body}
    if r.status_code not in esperado:
        print("!!", nombre, r.status_code, str(body)[:400])
    return body


def outbox_mias(desde: str) -> list[dict]:
    rows = psql(
        'select "EOB_Id","EOB_Plantilla","EOB_Asunto","EOB_Para","EOB_Estado","EOB_Intentos",'
        '"EOB_Proximo_Intento","EOB_Ultimo_Error","EOB_Creado_En","EOB_Enviado_En" '
        f'from "SYS_EMAIL_OUTBOX" where "EOB_Creado_En" >= \'{desde}\' order by "EOB_Creado_En"'
    )
    cols = ["id", "plantilla", "asunto", "para", "estado", "intentos", "proximo", "error", "creado", "enviado"]
    return [dict(zip(cols, line.split("|"))) for line in rows.splitlines() if line]


inicio = psql("select (now() at time zone 'utc')::text")
R["inicio_utc"] = inicio
mh_borrar()

# ---------------------------------------------------------------- 1. Configuración
cfg = integ_base(cuenta_password="Qa7-Secreto-SMTP-123!")
b = ok("C01_guardar_correo", req(c, "PUT", "/directorio/integracion", json=cfg))
R["C01_password_en_respuesta"] = "Qa7-Secreto-SMTP-123!" in json.dumps(b)
g = ok("C02_get_integracion", get(c, "/directorio/integracion"))
R["C02_claves_devueltas"] = sorted(k for k in (g or {}) if "password" in k or "secret" in k)
R["C02_secreto_en_get"] = "Qa7-Secreto-SMTP-123!" in json.dumps(g)
enc = psql('select "INT_Cuenta_Password_Enc" from "SYS_INTEGRACION_CORREO" where "INT_Id"=1')
R["C03_cifrado_bd"] = {"valor_inicio": enc[:12] + "...", "texto_plano_en_bd": "Qa7-Secreto" in enc, "longitud": len(enc)}
aud = psql('select "AUD_Snapshot_JSON"::text from "INV_AUDITORIA_SISTEMA" where "AUD_Entidad"=\'SYS_INTEGRACION_CORREO\' '
           'order by "AUD_Fecha_Hora" desc limit 1')
R["C03_auditoria"] = {"snapshot": aud[:600], "secreto_en_auditoria": "Qa7-Secreto" in aud}

# Prueba de correo (sin guardar) a un destinatario .local
t = ok("C04_probar_correo", req(c, "POST", "/directorio/integracion/probar-correo",
                                json={"config": {**cfg, "cuenta_password": None}, "destinatario": "qa7.prueba@empresa.local"}))
m = esperar_correos(lambda x: "Prueba de la cuenta" in x["asunto"], timeout=30)
R["C04_mailhog"] = [{k: x[k] for k in ("de", "para", "asunto")} for x in m]

# Prueba a un host inexistente
t2 = ok("C05_probar_host_malo", req(c, "POST", "/directorio/integracion/probar-correo",
                                    json={"config": {**cfg, "smtp_host": "qa7-no-existe.invalid", "cuenta_password": None},
                                          "destinatario": "qa7.prueba@empresa.local"}))
# Validación: modo CORREO sin host
ok("C06_validacion_sin_host", req(c, "PUT", "/directorio/integracion", json={**cfg, "smtp_host": ""}), esperado=(422,))

# ---------------------------------------------------------------- 2. Datos QA7
jefe = ok("D01_persona_jefe", req(c, "POST", "/org/personas", json={
    "PER_Primer_Nombre": "QA7-Jefa", "PER_Primer_Apellido": f"Correo{SUF}",
    "PER_Email_Corporativo": f"qa7.jefa.{SUF}@empresa.local", "DEP_Departamento": 2, "CAR_Cargo": 2, "SED_Sede": 1}))
afe = ok("D02_persona_afectada", req(c, "POST", "/org/personas", json={
    "PER_Primer_Nombre": "QA7-Ana", "PER_Segundo_Nombre": "María", "PER_Primer_Apellido": f"Afectada{SUF}",
    "PER_Email_Corporativo": f"qa7.ana.{SUF}@empresa.local", "DEP_Departamento": 2, "CAR_Cargo": 2,
    "PER_Jefe": jefe["PER_Persona"], "SED_Sede": 1}))
destino = ok("D03_persona_destino", req(c, "POST", "/org/personas", json={
    "PER_Primer_Nombre": "QA7-Beto", "PER_Primer_Apellido": f"Destino{SUF}",
    "PER_Email_Corporativo": f"qa7.beto.{SUF}@empresa.local", "DEP_Departamento": 2, "CAR_Cargo": 2, "SED_Sede": 1}))
from datetime import date, timedelta
act = ok("D04_activo", req(c, "POST", "/core/activos", json={
    "ACT_Serie_Fabricante": f"QA7-SN-{SUF}", "ACT_Hostname": f"QA7-HOST-{SUF}", "ACT_Fecha_Compra": "2024-01-15",
    "ACT_Fin_Garantia": (date.today() + timedelta(days=5)).isoformat(), "ACT_Costo": "1500.50", "ACT_Moneda": "USD",
    "MOD_Modelo": 3, "TAC_Tipo_Activo": 1, "EOP_Estado_Operativo": 1, "SED_Sede": 1}))
AID = act["ACT_Activo"]
R["datos"] = {"jefe": jefe["PER_Persona"], "afectada": afe["PER_Persona"], "destino": destino["PER_Persona"],
              "activo": AID, "codigo": act.get("ACT_Codigo_Interno"), "suf": SUF}

# ---------------------------------------------------------------- 3. Reglas
EXTRA = "qa7.extra@empresa.local"
ADM = "qa7.admins@empresa.local"
ok("R01_regla_asignacion", req(c, "PUT", "/directorio/notificaciones/reglas/asignacion", json={
    "activa": True, "notificar_afectado": True, "notificar_jefe": True, "copiar_admins": True,
    "grupos_ad": [], "correos_extra": [EXTRA, "QA7.Extra@Empresa.Local"]}))
for ev in ("baja", "stock_bajo", "garantia_por_vencer", "mantenimiento_abierto", "transferencia"):
    ok(f"R02_regla_{ev}", req(c, "PUT", f"/directorio/notificaciones/reglas/{ev}", json={
        "activa": True, "notificar_afectado": ev in ("mantenimiento_abierto", "transferencia"), "notificar_jefe": ev == "transferencia",
        "copiar_admins": True, "grupos_ad": [], "correos_extra": [ADM]}))
ok("R03_regla_devolucion_inactiva", req(c, "PUT", "/directorio/notificaciones/reglas/devolucion", json={
    "activa": False, "notificar_afectado": True, "notificar_jefe": True, "copiar_admins": True, "grupos_ad": [], "correos_extra": [ADM]}))
ok("R04_correo_extra_invalido", req(c, "PUT", "/directorio/notificaciones/reglas/baja", json={
    "activa": True, "notificar_afectado": False, "notificar_jefe": False, "copiar_admins": True, "grupos_ad": [],
    "correos_extra": ["no-es-correo"]}), esperado=(422,))
ok("R05_evento_inexistente", req(c, "PUT", "/directorio/notificaciones/reglas/qa7_inexistente", json={
    "activa": True, "notificar_afectado": True, "notificar_jefe": False, "copiar_admins": True}), esperado=(404,))
ok("R06_preview_asignacion", get(c, "/directorio/notificaciones/vista-previa/asignacion", params={"persona_id": afe["PER_Persona"]}))
ok("R07_preview_sin_persona", get(c, "/directorio/notificaciones/vista-previa/asignacion"))
ok("R08_preview_mant_cerrado_default", get(c, "/directorio/notificaciones/vista-previa/mantenimiento_cerrado",
                                           params={"persona_id": afe["PER_Persona"]}))
ok("R09_preview_devolucion_inactiva", get(c, "/directorio/notificaciones/vista-previa/devolucion", params={"persona_id": afe["PER_Persona"]}))
ok("R10_preview_persona_inexistente", get(c, "/directorio/notificaciones/vista-previa/asignacion",
                                          params={"persona_id": str(uuid.uuid4())}), esperado=(404,))

# ---------------------------------------------------------------- 4. Eventos
mov = ok("E01_asignacion", req(c, "POST", "/trazabilidad/movimientos", json={
    "ACT_Activo": AID, "PER_Persona": afe["PER_Persona"], "ARE_Area": 1, "TMO_Tipo_Movimiento": 2,
    "MOV_Observacion": "QA7 entrega <b>con</b> cargador & mochila"}))
man = ok("E02_mant_abierto", req(c, "POST", "/mantenimiento/", json={
    "ACT_Activo": AID, "PER_Persona_Solicita": afe["PER_Persona"], "TMA_Tipo_Mantenimiento": 2,
    "MAN_Descripcion_Falla": "QA7: no enciende", "MAN_Moneda": "USD"}, headers={"Idempotency-Key": str(uuid.uuid4())}))
ok("E03_mant_cerrado", req(c, "PATCH", f"/mantenimiento/{man['MAN_Mantenimiento']}/cerrar", json={
    "MAN_Costo_Total": "1234567.5", "MAN_Moneda": "USD"}))
ok("E04_transferencia", req(c, "POST", "/trazabilidad/transferencia", json={
    "ACT_Activo": AID, "PER_Persona_Destino": destino["PER_Persona"], "ARE_Area_Destino": 1, "MOV_Observacion": "QA7 transferencia"}))
ok("E05_devolucion", req(c, "POST", "/trazabilidad/devolucion", json={"ACT_Activo": AID}))
ok("E06_garantias", req(c, "POST", "/compras/garantias/notificar", params={"dias": 7}))
ok("E07_baja", req(c, "DELETE", f"/core/activos/{AID}"))
con = ok("E08_consumible", req(c, "POST", "/consumibles", json={
    "CON_Nombre": f"QA7-Tóner {SUF}", "CON_Categoria": "QA7", "CON_Unidad": "cartucho", "CON_Stock_Minimo": 5,
    "CON_Stock_Actual": 6, "SED_Sede": 1}))
ok("E09_salida_stock", req(c, "POST", f"/consumibles/{con['CON_Consumible']}/salida", json={"MOC_Cantidad": 2, "MOC_Motivo": "QA7"}))

time.sleep(40)
correos = esperar_correos(lambda x: SUF in x["html"] or "QA7-T" in x["asunto"] or "Garant" in x["asunto"], timeout=60)
R["mailhog"] = [{k: x[k] for k in ("de", "para", "cabecera_to", "asunto", "reply_to")} | {"texto_plano": x["texto"][:60]} for x in correos]
for i, x in enumerate(correos):
    guardar(f"correo_{i:02d}_{x['asunto'][:40].replace(' ', '_').replace(':', '').replace('/', '')}.html", x["html"])
R["outbox"] = outbox_mias(inicio)
R["outbox_api"] = get(c, "/gov/email-outbox").json()
guardar("correo_eventos.json", R)
print(json.dumps({k: v for k, v in R.items() if k not in ("mailhog",)}, ensure_ascii=False, indent=1, default=str)[:12000])
print("CORREOS:")
for x in R["mailhog"]:
    print(" ", x["asunto"], "->", x["para"])

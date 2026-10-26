"""
QA4 — Preparación: con UNA sola sesión de `sa` (un único intento de 2FA, sin
reintentos para no sumar fallos a su bloqueo) crea las personas y cuentas qa4_*.
Luego cada cuenta cambia su contraseña temporal y, si toca, enrola MFA TOTP.
Resultado: salidas/qa4/cuentas.json (credenciales de prueba) y preparar.json.
"""
import json
import sys
import time

import pyotp

from qa4_comun import API, Registro, cargar_cuentas, cliente, esperar_ventana_fresca, guardar_cuentas, login_totp_una_vez
from qa_login import credenciales, login as login_sa

R = Registro("preparar")
SUF = time.strftime("%H%M")

# usuario: (rol, sedes, global, enrolar_totp)
PLAN = {
    "qa4_consulta": ("CONSULTA", [2], False, False),
    "qa4_consulta3": ("CONSULTA", [3], False, False),
    "qa4_tecnico": ("TECNICO", [2], False, False),
    "qa4_lock": ("CONSULTA", [2], False, False),
    "qa4_lock2": ("CONSULTA", [2], False, False),
    "qa4_mfa": ("CONSULTA", [2], False, True),
    "qa4_mfa2": ("CONSULTA", [2], False, True),
    "qa4_pwd": ("CONSULTA", [2], False, False),
    "qa4_ses": ("CONSULTA", [2], False, False),
    "qa4_temp": ("CONSULTA", [2], False, False),
    "qa4_admti": ("ADMIN_TI", [2], False, False),     # queda con MFA pendiente (restringida)
    "qa4_admti2": ("ADMIN_TI", [2], False, True),     # ADMIN_TI con MFA: crea datos de prueba
    "qa4_seg": ("ADMIN_SEGURIDAD", [], False, True),
    "qa4_seg2": ("ADMIN_SEGURIDAD", [], False, True),
    "qa4_email": ("CONSULTA", [2], False, False),
}

cuentas = cargar_cuentas()


def alta_con_sa():
    """Unica sesion de sa (login serializado de qa_login, sin reintentos)."""
    sa = credenciales()
    c = cliente()
    body = login_sa(c, sa["usuario"], sa["clave"], sa["totp_secret"])
    c.headers["Authorization"] = f"Bearer {body['access_token']}"
    print("sa autenticado")
    personas = {p["PER_Email_Corporativo"]: p for p in c.get(f"{API}/org/personas").json()}
    usuarios = {u["USU_Username"]: u for u in c.get(f"{API}/org/usuarios").json()}
    for i, (usr, (rol, sedes, glob, _)) in enumerate(PLAN.items()):
        if usr in usuarios:
            cuentas.setdefault(usr, {})["id"] = usuarios[usr]["USU_Usuario"]
            continue
        email = f"{usr.replace('_', '.')}@lombardi.dataset.local"
        per = personas.get(email)
        if not per:
            r = c.post(f"{API}/org/personas", json={
                "PER_Primer_Nombre": "Prueba", "PER_Primer_Apellido": f"Qacuatro{chr(65 + i)}",
                "PER_Email_Corporativo": email, "DEP_Departamento": 2, "CAR_Cargo": 2,
                "SED_Sede": (sedes or [2])[0]})
            if r.status_code != 201:
                sys.exit(f"persona {usr} {r.status_code} {r.text}")
            per = r.json()
        temporal = f"Tmp-{SUF}-Xq{i}zR9!"
        r = c.post(f"{API}/org/usuarios", json={
            "USU_Username": usr, "USU_Password": temporal, "USU_Rol": rol,
            "PER_Persona": per["PER_Persona"], "USU_Alcance_Global": glob, "sedes": sedes})
        if r.status_code != 201:
            sys.exit(f"usuario {usr} {r.status_code} {r.text}")
        u = r.json()
        cuentas[usr] = {"id": u["USU_Usuario"], "persona": per["PER_Persona"], "rol": rol,
                        "clave_temporal": temporal, "clave": temporal, "email": email}
        R.caso(f"PREP-{usr}", f"alta de {usr} ({rol})", True, f"id={u['USU_Usuario']}")
    activos = c.get(f"{API}/core/activos", params={"limit": 200}).json()
    lista = activos if isinstance(activos, list) else activos.get("items", activos.get("data", []))
    por_sede = {}
    for a in lista:
        por_sede.setdefault(a.get("SED_Sede"), a)
    cuentas["_aux"] = {"activo_por_sede": {str(k): v.get("ACT_Activo") for k, v in por_sede.items()}}
    guardar_cuentas(cuentas)


if not all(u in cuentas for u in PLAN):
    alta_con_sa()
else:
    print("cuentas ya creadas: no se inicia sesion con sa")


def cambiar(u, actual, nueva):
    """/me/password tiene 5/min por IP y todos los agentes comparten IP: reintenta tras 429."""
    for _ in range(8):
        r = u.post(f"{API}/me/password", json={"current_password": actual, "new_password": nueva})
        if r.status_code != 429:
            return r
        time.sleep(20)
    return r


# Cada cuenta cambia su temporal y (si toca) enrola TOTP
for i, (usr, (rol, _, _, totp)) in enumerate(PLAN.items()):
    cu = cuentas[usr]
    if cu.get("listo"):
        continue
    u = cliente()
    b = login_totp_una_vez(u, usr, cu["clave"], cu.get("totp"))
    u.headers["Authorization"] = f"Bearer {b['access_token']}"
    if b.get("restriction") == "PASSWORD_CHANGE_REQUIRED":
        nueva = f"Kv{SUF}#Tz{i}mWq!"
        r = cambiar(u, cu["clave"], nueva)
        if r.status_code != 204:
            print("cambio", usr, r.status_code, r.text)
            sys.exit(1)
        cu["clave"] = nueva
        guardar_cuentas(cuentas)
        u = cliente()
        b = login_totp_una_vez(u, usr, cu["clave"], None)
        u.headers["Authorization"] = f"Bearer {b['access_token']}"
    if totp:
        s = u.post(f"{API}/me/2fa/totp/setup").json()
        secret = s["secret"]
        a = u.post(f"{API}/me/2fa/totp/activate", json={"code": pyotp.TOTP(secret).now()})
        if a.status_code != 200:
            print("totp", usr, a.status_code, a.text)
            sys.exit(1)
        cu["totp"] = secret
        cu["recovery"] = a.json()["recovery_codes"]
    cu["listo"] = True
    guardar_cuentas(cuentas)
    print("lista", usr, "restriccion final:", b.get("restriction"))

print(json.dumps({k: {kk: vv for kk, vv in v.items() if kk in ("rol", "id")} for k, v in cuentas.items() if k.startswith("qa4")}, indent=1))

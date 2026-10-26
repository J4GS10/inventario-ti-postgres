"""
Crea (una sola vez) la cuenta `jefe.ti` con rol JEFE_TI en el stack de revisión
(inv_ui): alta como sa, cambio de la contraseña temporal y enrolamiento TOTP.

Las credenciales quedan en salidas/credenciales_jefe.json (no versionado).
Uso:  python herramientas-qa/crear_jefe_ti.py
"""
import json
import pathlib
import secrets
import sys

import httpx
import pyotp

from qa_login import cliente_autenticado, login

API = "https://localhost:8443/api/v1"
SALIDA = pathlib.Path(__file__).parent / "salidas" / "credenciales_jefe.json"
USUARIO = "jefe.ti"
EMAIL = "jefe.ti@lombardi.dataset.local"

if SALIDA.exists():
    print("La cuenta ya existe:", SALIDA)
    sys.exit(0)

sa = cliente_autenticado()
personas = {p["PER_Email_Corporativo"]: p for p in sa.get(f"{API}/org/personas").json()}
base = next(iter(personas.values()))
per = personas.get(EMAIL)
if not per:
    r = sa.post(f"{API}/org/personas", json={
        "PER_Primer_Nombre": "Jefe", "PER_Primer_Apellido": "de TI", "PER_Email_Corporativo": EMAIL,
        "DEP_Departamento": base["DEP_Departamento"], "CAR_Cargo": base["CAR_Cargo"],
        "SED_Sede": base.get("SED_Sede")})
    if r.status_code != 201:
        sys.exit(f"persona {r.status_code} {r.text}")
    per = r.json()

existente = next((u for u in sa.get(f"{API}/org/usuarios").json() if u["USU_Username"] == USUARIO), None)
if existente:
    # Cuenta creada en un intento anterior: nueva contraseña temporal.
    temporal = sa.post(f"{API}/org/usuarios/{existente['USU_Usuario']}/password/reset").json()["temporary_password"]
else:
    temporal = f"Tmp-{secrets.token_hex(4)}-Qx9!"
    r = sa.post(f"{API}/org/usuarios", json={
        "USU_Username": USUARIO, "USU_Password": temporal, "USU_Rol": "JEFE_TI",
        "PER_Persona": per["PER_Persona"], "USU_Alcance_Global": True, "sedes": []})
    if r.status_code != 201:
        sys.exit(f"usuario {r.status_code} {r.text}")

c = httpx.Client(verify=False, timeout=60)
b = login(c, USUARIO, temporal, None)
c.headers["Authorization"] = f"Bearer {b['access_token']}"
# Sin datos personales (la política de contraseñas los rechaza).
clave = f"Rv{secrets.token_hex(6)}#Kq7!"
r = c.post(f"{API}/me/password", json={"current_password": temporal, "new_password": clave})
if r.status_code != 204:
    sys.exit(f"cambio de contraseña {r.status_code} {r.text}")

c = httpx.Client(verify=False, timeout=60)
b = login(c, USUARIO, clave, None)
c.headers["Authorization"] = f"Bearer {b['access_token']}"
secret = c.post(f"{API}/me/2fa/totp/setup").json()["secret"]
a = c.post(f"{API}/me/2fa/totp/activate", json={"code": pyotp.TOTP(secret).now()})
if a.status_code != 200:
    sys.exit(f"TOTP {a.status_code} {a.text}")

SALIDA.write_text(json.dumps({"usuario": USUARIO, "clave": clave, "totp_secret": secret,
                              "recovery_codes": a.json().get("recovery_codes")}, indent=2), encoding="utf-8")
print("Cuenta JEFE_TI lista:", USUARIO, "-", SALIDA)

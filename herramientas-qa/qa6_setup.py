"""
QA6 — crea (una sola vez) los datos propios de las pruebas de adjuntos:
sede QA6 (otra sede), proveedor, activo y orden QA6 en la sede 1, y tres
cuentas sin MFA: técnico de la sede 1, técnico de la sede QA6 y auditor de la sede 1.
Guarda todo en salidas/qa6/estado.json (idempotente).
"""
import sys
import time

from qa6_comun import cargar_estado, cliente_sa, guardar_estado
from qa_login import BASE, login

import httpx

TEMP = "Qa6-Temporal#2026a"
FINAL = "Qa6-Adjuntos#2026x"

e = cargar_estado()
suf = e.setdefault("suf", str(int(time.time()))[-5:])
c = cliente_sa()


def ok(r, *codes):
    if r.status_code not in codes:
        print("ERROR", r.request.method, r.request.url, r.status_code, r.text[:500])
        sys.exit(1)
    return r.json() if r.content else None


SEDE1 = 1
e["sede1"] = SEDE1
if "sede_qa6" not in e:
    s = ok(c.post(f"{BASE}/geo/sedes", json={"SED_Nombre": f"QA6-Sede Sur {suf}", "MUN_Municipio": 1}), 200, 201)
    ed = ok(c.post(f"{BASE}/geo/edificios", json={"EDI_Nombre": "QA6-Torre", "SED_Sede": s["SED_Sede"]}), 200, 201)
    nv = ok(c.post(f"{BASE}/geo/niveles", json={"NIV_Numero_Piso": "1", "EDI_Edificio": ed["EDI_Edificio"]}), 200, 201)
    ar = ok(c.post(f"{BASE}/geo/areas", json={"ARE_Nombre": "QA6-Area Sur", "NIV_Nivel": nv["NIV_Nivel"]}), 200, 201)
    e["sede_qa6"] = s["SED_Sede"]
    guardar_estado(e)

if "proveedor" not in e:
    p = ok(c.post(f"{BASE}/compras/proveedores", json={"PRV_Nombre": f"QA6-Proveedor {suf}"}), 200, 201)
    e["proveedor"] = p["PRV_Proveedor"]
    guardar_estado(e)

if "activo" not in e:
    a = ok(c.post(f"{BASE}/core/activos", json={
        "ACT_Serie_Fabricante": f"QA6-SER-{suf}", "ACT_Hostname": f"QA6-HOST-{suf}",
        "ACT_Fecha_Compra": "2026-09-01", "MOD_Modelo": 1, "TAC_Tipo_Activo": 1,
        "EOP_Estado_Operativo": 1, "SED_Sede": SEDE1}), 200, 201)
    e["activo"] = a["ACT_Activo"]
    e["activo_codigo"] = a.get("ACT_Codigo_Interno")
    guardar_estado(e)

if "activo_sur" not in e:
    # Activo QA6 en la sede QA6 (para probar que un técnico de la sede 1 no sube a él).
    a = ok(c.post(f"{BASE}/core/activos", json={
        "ACT_Serie_Fabricante": f"QA6-SUR-{suf}", "ACT_Fecha_Compra": "2026-09-01",
        "MOD_Modelo": 1, "TAC_Tipo_Activo": 1, "EOP_Estado_Operativo": 1,
        "SED_Sede": e["sede_qa6"]}), 200, 201)
    e["activo_sur"] = a["ACT_Activo"]
    guardar_estado(e)

if "orden" not in e:
    o = ok(c.post(f"{BASE}/compras/ordenes", json={
        "OCO_Numero": f"QA6-OC-{suf}", "OCO_Fecha": time.strftime("%Y-%m-%d"),
        "PRV_Proveedor": e["proveedor"], "SED_Sede": SEDE1,
        "lineas": [{"OCL_Descripcion": "QA6-Linea", "OCL_Cantidad": 1, "OCL_Precio_Unitario": "10"}]}), 200, 201)
    e["orden"] = o["OCO_Orden"]
    e["orden_numero"] = o["OCO_Numero"]
    guardar_estado(e)

cuentas = e.setdefault("cuentas", {})
for clave, rol, sede in (("tec1", "TECNICO", SEDE1), ("tecsur", "TECNICO", e["sede_qa6"]), ("aud1", "AUDITOR", SEDE1)):
    if clave in cuentas:
        continue
    per = ok(c.post(f"{BASE}/org/personas", json={
        "PER_Primer_Nombre": "QA6", "PER_Primer_Apellido": f"{clave.capitalize()} {suf}",
        "PER_Email_Corporativo": f"qa6.{clave}.{suf}@lombardi.local",
        "DEP_Departamento": 2, "CAR_Cargo": 2, "SED_Sede": sede}), 200, 201)
    user = f"qa6-{clave}-{suf}"
    ok(c.post(f"{BASE}/org/usuarios", json={
        "USU_Username": user, "USU_Password": TEMP, "USU_Rol": rol,
        "PER_Persona": per["PER_Persona"], "sedes": [sede]}), 200, 201)
    with httpx.Client(verify=False, timeout=30) as u:
        tok = login(u, user, TEMP)["access_token"]
        r = u.post(f"{BASE}/me/password", headers={"Authorization": f"Bearer {tok}"},
                   json={"current_password": TEMP, "new_password": FINAL})
        if r.status_code != 204:
            print("No se pudo cambiar la clave temporal", user, r.status_code, r.text)
            sys.exit(1)
    cuentas[clave] = {"usuario": user, "clave": FINAL, "rol": rol, "sede": sede, "persona": per["PER_Persona"]}
    guardar_estado(e)

print("Estado QA6:", e)

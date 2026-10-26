"""QA5 - Reportería: crea los datos de prueba propios (prefijo QA5-) y los guarda en salidas/qa5/datos.json.

Crea: sede QA5 con jerarquía, personas, activos con valores de inyección CSV y
costos en USD/CHF/GTQ, movimientos (asignación, devolución), consumible,
proveedor, orden de compra y dos usuarios con alcance SOLO en la sede QA5
(ADMIN_TI con MFA enrolado por API y AUDITOR).
"""
import json
import os
import time

import pyotp

from qa_login import BASE, login
from qa5_comun import cliente
import httpx

AQUI = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(AQUI, "salidas", "qa5")
os.makedirs(OUT, exist_ok=True)
DATOS = os.path.join(OUT, "datos.json")
SUF = str(int(time.time()) % 100000)
TEMP_PWD = "Qa5-Temporal-2026!x"
FINAL_PWD = "Qa5-Definitiva-2026!Zq"


def ok(r, *codes):
    if r.status_code not in (codes or (200, 201)):
        raise RuntimeError(f"{r.request.method} {r.request.url} -> {r.status_code} {r.text[:400]}")
    return r.json() if r.content else None


def _login_retry(u, nombre, pwd):
    for _ in range(20):
        try:
            return login(u, nombre, pwd)
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 429:
                raise
            time.sleep(15)
    raise RuntimeError("429")


def main():
    c = cliente()
    d = {"suf": SUF}
    sedes = ok(c.get(f"{BASE}/org/sedes"))
    sede_a = sedes[0]
    d["sede_otra"] = sede_a["SED_Sede"]
    mun = ok(c.get(f"{BASE}/geo/sedes/{sede_a['SED_Sede']}"))["MUN_Municipio"]
    sede = ok(c.post(f"{BASE}/geo/sedes", json={"SED_Nombre": f"QA5-Sede Sur {SUF}", "MUN_Municipio": mun}))
    edi = ok(c.post(f"{BASE}/geo/edificios", json={"EDI_Nombre": "QA5-Torre", "SED_Sede": sede["SED_Sede"]}))
    niv = ok(c.post(f"{BASE}/geo/niveles", json={"NIV_Numero_Piso": "1", "EDI_Edificio": edi["EDI_Edificio"]}))
    area = ok(c.post(f"{BASE}/geo/areas", json={"ARE_Nombre": "QA5-Bodega Señorío", "NIV_Nivel": niv["NIV_Nivel"]}))
    d.update(sede=sede["SED_Sede"], sede_nombre=sede["SED_Nombre"], area=area["ARE_Area"])

    def persona(nombre, apellido, email):
        return ok(c.post(f"{BASE}/org/personas", json={
            "PER_Primer_Nombre": nombre, "PER_Primer_Apellido": apellido,
            "PER_Email_Corporativo": f"{email}{SUF}@lombardi.dataset.local",
            "DEP_Departamento": 2, "CAR_Cargo": 2, "SED_Sede": sede["SED_Sede"]}))

    ana = persona("QA5-Íñigo", "Peña", "qa5.inigo")
    bea = persona("QA5-Beatriz", "Muñoz", "qa5.beatriz")
    per_tec = persona("QA5-Técnico", "Sur", "qa5.tecnico")
    per_aud = persona("QA5-Auditora", "Sur", "qa5.auditora")
    d.update(ana=ana["PER_Persona"], bea=bea["PER_Persona"])

    def activo(serie, host, costo, moneda, modelo=1, tipo=1):
        body = {"ACT_Serie_Fabricante": serie, "ACT_Hostname": host, "ACT_Fecha_Compra": "2026-09-24",
                "ACT_Fin_Garantia": "2029-09-24", "MOD_Modelo": modelo, "TAC_Tipo_Activo": tipo,
                "EOP_Estado_Operativo": 1, "SED_Sede": sede["SED_Sede"]}
        if costo is not None:
            body.update(ACT_Costo=costo, ACT_Moneda=moneda)
        return ok(c.post(f"{BASE}/core/activos", json=body))

    a1 = activo(f"QA5-SER-001-{SUF}", '=HYPERLINK("http://qa5.invalid","x")', 1234.5, "USD")
    a2 = activo(f"+QA5-SER-002-{SUF}", "@SUM(1+1)", 999.99, "CHF")
    a3 = activo(f"QA5-SER-003-{SUF}", "-QA5-HOST", 5000, "GTQ", tipo=2, modelo=3)
    a4 = activo(f"QA5-SER-004-{SUF}", f"QA5-HOST4-{SUF}", None, None)
    d["activos"] = {k: {"id": v["ACT_Activo"], "codigo": v["ACT_Codigo_Interno"], "serie": v["ACT_Serie_Fabricante"]}
                    for k, v in (("a1", a1), ("a2", a2), ("a3", a3), ("a4", a4))}

    tipos = {t["TMO_Nombre"]: t["TMO_Tipo_Movimiento"] for t in ok(c.get(f"{BASE}/trazabilidad/tipos"))}
    asign = tipos["Asignación"]
    movs = {}
    for k, a, per, obs in (
        ("m1", a1, ana, "Entrega QA5 con tildes: señal, ñandú, acción & <prueba>"),
        ("m2", a2, ana, "=cmd|' /C calc'!A0"),
        ("m3", a3, ana, None),
        ("m4", a4, bea, "QA5 entrega a otra persona"),
    ):
        m = ok(c.post(f"{BASE}/trazabilidad/movimientos", json={
            "ACT_Activo": a["ACT_Activo"], "PER_Persona": per["PER_Persona"], "ARE_Area": area["ARE_Area"],
            "TMO_Tipo_Movimiento": asign, "MOV_Observacion": obs}))
        movs[k] = m["MOV_Movimiento"]
    dev = ok(c.post(f"{BASE}/trazabilidad/devolucion", json={"ACT_Activo": a3["ACT_Activo"]}))
    d["devolucion_resp"] = dev
    hist = ok(c.get(f"{BASE}/trazabilidad/activo/{a3['ACT_Activo']}/historial"))
    d["hist_a3"] = [{"id": h["MOV_Movimiento"], "tipo": h["tipo_movimiento"]["TMO_Nombre"] if h.get("tipo_movimiento") else None,
                     "dev": h.get("MOV_Fecha_Devolucion")} for h in hist]
    d["movs"] = movs

    con = ok(c.post(f"{BASE}/consumibles", json={
        "CON_Nombre": "=QA5-Tóner negro", "CON_Categoria": "+Impresión", "CON_Unidad": "caja",
        "CON_Stock_Minimo": 5, "CON_Stock_Actual": 2, "SED_Sede": sede["SED_Sede"]}))
    d["consumible"] = con["CON_Consumible"]
    prv = ok(c.post(f"{BASE}/compras/proveedores", json={
        "PRV_Nombre": f"QA5-Proveedor Ñandú {SUF}", "PRV_Identificacion_Fiscal": "-12345",
        "PRV_Contacto": "@QA5 contacto", "PRV_Email": "qa5@example.com",
        "PRV_Telefono": "+502 5555 0000", "PRV_Direccion": "Zona 10, Guatemala"}))
    d["proveedor"] = prv["PRV_Proveedor"]
    oc = ok(c.post(f"{BASE}/compras/ordenes", json={
        "OCO_Numero": f"QA5-OC-{SUF}", "OCO_Fecha": "2026-09-24", "OCO_Moneda": "CHF",
        "OCO_Notas": "=QA5 nota con acentuación", "PRV_Proveedor": prv["PRV_Proveedor"],
        "SED_Sede": sede["SED_Sede"],
        "lineas": [{"OCL_Descripcion": "QA5 línea", "OCL_Cantidad": 2, "OCL_Precio_Unitario": "150.25"}]}))
    d["orden"] = {"id": oc["OCO_Orden"], "numero": oc["OCO_Numero"], "total": oc.get("OCO_Total")}

    # Usuarios con alcance SOLO en la sede QA5.
    usuarios = {}
    for rol, per, nombre in (("ADMIN_TI", per_tec, f"qa5tec{SUF}"), ("AUDITOR", per_aud, f"qa5aud{SUF}")):
        ok(c.post(f"{BASE}/org/usuarios", json={
            "USU_Username": nombre, "USU_Password": TEMP_PWD, "USU_Rol": rol,
            "PER_Persona": per["PER_Persona"], "sedes": [sede["SED_Sede"]]}), 201)
        u = httpx.Client(verify=False, timeout=60)
        tok = _login_retry(u, nombre, TEMP_PWD)["access_token"]
        ok(u.post(f"{BASE}/me/password", headers={"Authorization": f"Bearer {tok}"},
                  json={"current_password": TEMP_PWD, "new_password": FINAL_PWD}), 204)
        body = _login_retry(u, nombre, FINAL_PWD)
        secret = None
        if body.get("restriction") == "MFA_ENROLLMENT_REQUIRED":
            H = {"Authorization": f"Bearer {body['access_token']}"}
            secret = ok(u.post(f"{BASE}/me/2fa/totp/setup", headers=H))["secret"]
            ok(u.post(f"{BASE}/me/2fa/totp/activate", headers=H, json={"code": pyotp.TOTP(secret).now()}))
        usuarios[rol] = {"usuario": nombre, "clave": FINAL_PWD, "totp_secret": secret}
    d["usuarios"] = usuarios

    with open(DATOS, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    print(json.dumps(d, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()

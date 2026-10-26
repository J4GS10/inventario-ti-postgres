"""
QA3 — Preparación de datos para la matriz de roles x alcance por sede (RLS).

Crea (como `sa`) tres sedes propias con datos en cada una y 13 cuentas de
prueba: ADMIN_SEGURIDAD (global) y ADMIN_TI/TECNICO/AUDITOR/CONSULTA con
alcance global, de una sede (N) y de varias sedes (N+S). Todo lleva el prefijo
QA3-/qa3_. El estado (ids, claves y secretos TOTP) queda en
salidas/qa3/estado.json; si ya existe, no se vuelve a crear nada.

Uso: python qa3_setup.py
"""
import io
import json
import os
import sys
import time
import warnings

import httpx
import pyotp

warnings.filterwarnings("ignore")
from qa_login import BASE, login  # noqa: E402
from qa3_comun import sa_cliente  # noqa: E402

AQUI = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(AQUI, "salidas", "qa3")
ESTADO = os.path.join(OUT, "estado.json")
TEMP_PWD = "Qa3-Temporal#Rv2609"
FINAL_PWD = "Qa3-Definitiva#Kx7Lm"
SUF = time.strftime("%H%M%S")


def ok(r: httpx.Response, *codes):
    if r.status_code not in (codes or (200, 201)):
        raise RuntimeError(f"{r.request.method} {r.request.url} -> {r.status_code} {r.text[:400]}")
    return r.json() if r.content and r.headers.get("content-type", "").startswith("application/json") else None


def main():
    os.makedirs(OUT, exist_ok=True)
    sa = sa_cliente()
    if os.path.exists(ESTADO):
        estado = json.load(open(ESTADO, encoding="utf-8"))
        print("estado.json ya existe: se reanuda la creación de cuentas")
        return crear_cuentas(sa, estado)
    P = lambda p, **kw: ok(sa.post(BASE + p, **kw))  # noqa: E731
    G = lambda p, **kw: ok(sa.get(BASE + p, **kw))  # noqa: E731
    mun = G("/geo/sedes/1")["MUN_Municipio"]
    estado = {"suf": SUF, "sedes": {}, "usuarios": {}}

    # ── Proveedor, software y licencia (compartidos) ──
    prv = next((x for x in G("/compras/proveedores") if x["PRV_Nombre"].startswith("QA3-")), None)         or P("/compras/proveedores", json={"PRV_Nombre": f"QA3-Proveedor {SUF}"})
    tlis = G("/soft/tipos-licencia")
    tli = (tlis[0] if tlis else P("/soft/tipos-licencia", json={"TLI_Nombre": f"QA3-Tipo {SUF}"}))["TLI_Tipo_Licencia"]
    sof = next((x for x in G("/soft/software") if x["SOF_Nombre"].startswith("QA3-")), None)         or P("/soft/software", json={"SOF_Nombre": f"QA3-Soft {SUF}", "SOF_Fabricante": "QA3"})
    lic = P("/soft/licencias", json={"SOF_Software": sof["SOF_Software"], "TLI_Tipo_Licencia": tli,
                                     "LIC_Cantidad_Total": 10, "LIC_Clave_Activacion": f"QA3-KEY-{SUF}"})
    estado.update(proveedor=prv["PRV_Proveedor"], software=sof["SOF_Software"], licencia=lic["LIC_Licencia"])

    # ── Tres sedes con jerarquía y datos ──
    for letra in ("N", "S", "E"):
        sede = P("/geo/sedes", json={"SED_Nombre": f"QA3-Sede-{letra} {SUF}", "MUN_Municipio": mun})
        sid = sede["SED_Sede"]
        edi = P("/geo/edificios", json={"EDI_Nombre": f"QA3-Edif-{letra}", "SED_Sede": sid})
        niv = P("/geo/niveles", json={"NIV_Numero_Piso": "1", "EDI_Edificio": edi["EDI_Edificio"]})
        area = P("/geo/areas", json={"ARE_Nombre": f"QA3-Area-{letra}", "NIV_Nivel": niv["NIV_Nivel"]})
        per = P("/org/personas", json={
            "PER_Primer_Nombre": f"Qatres{letra}", "PER_Primer_Apellido": f"Custodio{letra}",
            "PER_Email_Corporativo": f"qa3.custodio.{letra.lower()}.{SUF}@lombardi-qa.com",
            "DEP_Departamento": 2, "CAR_Cargo": 2, "SED_Sede": sid})
        serie = f"QA3-{letra}-{SUF}"
        act = P("/core/activos", json={
            "ACT_Serie_Fabricante": serie + "-A", "ACT_Hostname": f"QA3-HOST-{letra}{SUF}",
            "ACT_Fecha_Compra": "2025-01-10", "MOD_Modelo": 3, "TAC_Tipo_Activo": 1,
            "EOP_Estado_Operativo": 1, "SED_Sede": sid,
            "especificaciones": []})
        act2 = P("/core/activos", json={
            "ACT_Serie_Fabricante": serie + "-B", "ACT_Fecha_Compra": "2025-01-10", "MOD_Modelo": 3,
            "TAC_Tipo_Activo": 1, "EOP_Estado_Operativo": 1, "SED_Sede": sid})
        mov = P("/trazabilidad/movimientos", json={
            "ACT_Activo": act["ACT_Activo"], "PER_Persona": per["PER_Persona"], "ARE_Area": area["ARE_Area"],
            "TMO_Tipo_Movimiento": 2, "MOV_Observacion": f"QA3 asignación {letra}"})
        man = P("/mantenimiento/", json={
            "ACT_Activo": act["ACT_Activo"], "PER_Persona_Solicita": per["PER_Persona"],
            "TMA_Tipo_Mantenimiento": 1, "MAN_Descripcion_Falla": f"QA3 falla {letra}"})
        con = P("/consumibles", json={"CON_Nombre": f"QA3-Toner-{letra} {SUF}", "CON_Stock_Actual": 5,
                                      "SED_Sede": sid})
        P(f"/consumibles/{con['CON_Consumible']}/salida", json={"MOC_Cantidad": 1, "PER_Persona": per["PER_Persona"],
                                                                "MOC_Motivo": "QA3"})
        oc = P("/compras/ordenes", json={
            "OCO_Numero": f"QA3-OC-{letra}-{SUF}", "OCO_Fecha": "2026-09-01", "PRV_Proveedor": prv["PRV_Proveedor"],
            "SED_Sede": sid, "lineas": [{"OCL_Descripcion": f"QA3 laptop {letra}", "OCL_Cantidad": 2,
                                         "OCL_Precio_Unitario": 100}]})
        ins = P("/soft/instalaciones", json={"ACT_Activo": act["ACT_Activo"], "LIC_Licencia": lic["LIC_Licencia"]})
        pdf = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
        adj = ok(sa.post(f"{BASE}/adjuntos/activos/{act['ACT_Activo']}",
                         files={"file": (f"qa3_{letra}.pdf", io.BytesIO(pdf), "application/pdf")},
                         data={"categoria": "otro", "descripcion": f"QA3 adjunto {letra}"}), 201)
        estado["sedes"][letra] = {
            "sede": sid, "nombre": sede["SED_Nombre"], "edificio": edi["EDI_Edificio"], "nivel": niv["NIV_Nivel"],
            "area": area["ARE_Area"], "persona": per["PER_Persona"], "activo": act["ACT_Activo"],
            "codigo": act["ACT_Codigo_Interno"], "serie": serie + "-A", "hostname": f"QA3-HOST-{letra}{SUF}",
            "activo2": act2["ACT_Activo"], "codigo2": act2["ACT_Codigo_Interno"], "movimiento": mov["MOV_Movimiento"],
            "mantenimiento": man["MAN_Mantenimiento"], "consumible": con["CON_Consumible"],
            "consumible_nombre": con["CON_Nombre"], "orden": oc["OCO_Orden"], "orden_numero": oc["OCO_Numero"],
            "orden_linea": oc["lineas"][0]["OCL_Linea"], "instalacion": ins["INS_Instalacion"],
            "adjunto": adj["ADJ_Adjunto"],
        }
        print("sede", letra, "lista", sid, flush=True)

    return crear_cuentas(sa, estado)


def reintentar(fn, codigos):
    """Los límites por IP son compartidos con otros agentes: esperar y repetir."""
    for _ in range(10):
        r = fn()
        if r.status_code == 429 and "RATE_LIMIT" in r.text:
            time.sleep(62 - (int(time.time()) % 60))
            continue
        return ok(r, *codigos)
    return ok(r, *codigos)


def crear_cuentas(sa, estado):
    P = lambda p, **kw: ok(sa.post(BASE + p, **kw))  # noqa: E731
    SUF = estado["suf"]
    existentes = {u["USU_Username"]: u for u in ok(sa.get(BASE + "/org/usuarios"))}
    # ── Cuentas de prueba ──
    N, S = estado["sedes"]["N"]["sede"], estado["sedes"]["S"]["sede"]
    alcances = {"g": {"USU_Alcance_Global": True}, "n": {"sedes": [N]}, "ns": {"sedes": [N, S]}}
    cuentas = [("qa3_seg", "ADMIN_SEGURIDAD", {}, N)]
    for pref, rol in (("ti", "ADMIN_TI"), ("tec", "TECNICO"), ("aud", "AUDITOR"), ("con", "CONSULTA")):
        for k, alc in alcances.items():
            cuentas.append((f"qa3_{pref}_{k}", rol, alc, N))
    for username, rol, alc, sede_persona in cuentas:
        if username in estado["usuarios"]:
            continue
        uname = f"{username}_{SUF}"
        if uname in existentes:
            u = existentes[uname]
            per = {"PER_Persona": u.get("PER_Persona") or (u.get("persona") or {}).get("PER_Persona")}
        else:
            per = P("/org/personas", json={
                "PER_Primer_Nombre": "Cuenta", "PER_Primer_Apellido": username.replace("_", "").capitalize(),
                "PER_Email_Corporativo": f"{username}.{SUF}@lombardi-qa.com",
                "DEP_Departamento": 2, "CAR_Cargo": 2, "SED_Sede": sede_persona})
            u = P("/org/usuarios", json={"USU_Username": uname, "USU_Password": TEMP_PWD, "USU_Rol": rol,
                                         "PER_Persona": per["PER_Persona"], **alc})
        # Primer ingreso: cambio de contraseña y, si el rol lo exige, MFA.
        c = httpx.Client(verify=False, timeout=60)
        tok = login(c, u["USU_Username"], TEMP_PWD)["access_token"]
        reintentar(lambda: c.post(f"{BASE}/me/password", headers={"Authorization": f"Bearer {tok}"},
                                  json={"current_password": TEMP_PWD, "new_password": FINAL_PWD}), (204,))
        estado["usuarios"][username] = {"username": u["USU_Username"], "id": u["USU_Usuario"], "rol": rol,
                                        "persona": per["PER_Persona"], "clave": FINAL_PWD, "totp": None}
        body = login(c, u["USU_Username"], FINAL_PWD)
        if rol in ("ADMIN_TI", "ADMIN_SEGURIDAD"):
            h = {"Authorization": f"Bearer {body['access_token']}"}
            secret = reintentar(lambda: c.post(f"{BASE}/me/2fa/totp/setup", headers=h), (200,))["secret"]
            reintentar(lambda: c.post(f"{BASE}/me/2fa/totp/activate", headers=h,
                                      json={"code": pyotp.TOTP(secret).now()}), (200,))
            estado["usuarios"][username]["totp"] = secret
        print("cuenta", u["USU_Username"], rol, alc, flush=True)
        with open(ESTADO, "w", encoding="utf-8") as f:
            json.dump(estado, f, ensure_ascii=False, indent=2)
    with open(ESTADO, "w", encoding="utf-8") as f:
        json.dump(estado, f, ensure_ascii=False, indent=2)
    print("OK", ESTADO)


if __name__ == "__main__":
    sys.exit(main())

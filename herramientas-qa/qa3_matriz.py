"""
QA3 — Matriz rol x alcance por sede (RLS) contra la API de producción.

Requiere salidas/qa3/estado.json (python qa3_setup.py). Para cada cuenta de
prueba (6 roles; global, una sede N y varias sedes N+S) comprueba:
  - Lectura: listados, detalle por id, búsquedas, exportes CSV, actas,
    etiquetas, tablero, sugerencias/contexto, instalaciones, adjuntos y
    bitácora. Una "fuga" es cualquier marcador (código, serie, hostname, id,
    nombre) de una sede fuera del alcance en una respuesta 2xx.
  - Escritura fuera del alcance: crear, editar, mover a otra sede, recibir
    una orden, etc. (se espera 403; 404 se acepta como "no revela").
  - Roles: AUDITOR/CONSULTA solo leen; ADMIN_SEGURIDAD no ve inventario.
Resultados: salidas/qa3/matriz_resultados.json y .csv

Uso: python qa3_matriz.py [usuario1,usuario2,...]
"""
import csv
import io
import json
import os
import sys
import time
import warnings

import httpx

warnings.filterwarnings("ignore")
from qa_login import BASE, login  # noqa: E402

AQUI = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(AQUI, "salidas", "qa3")
EST = json.load(open(os.path.join(OUT, "estado.json"), encoding="utf-8"))
SED = EST["sedes"]
LETRAS = ("N", "S", "E")
ALCANCE = {"g": set(LETRAS), "n": {"N"}, "ns": {"N", "S"}}
BIZ = {"SUPER_ADMIN", "ADMIN_TI", "TECNICO", "AUDITOR", "CONSULTA"}
OPER = {"SUPER_ADMIN", "ADMIN_TI", "TECNICO"}
ADMIN = {"SUPER_ADMIN", "ADMIN_TI"}
EXPORT = {"SUPER_ADMIN", "ADMIN_TI", "AUDITOR"}
AUDIT = {"SUPER_ADMIN", "ADMIN_SEGURIDAD", "AUDITOR"}
RES = []


def marcadores(letra):
    s = SED[letra]
    return [s["codigo"], s["codigo2"], s.get("codigo3", "~"), s["serie"], s["hostname"], s["consumible_nombre"], s["orden_numero"],
            s["activo"], s["activo2"], s["movimiento"], s["persona"], f"Custodio{letra}"]


def fugas(texto, fuera):
    return sorted({m for letra in fuera for m in marcadores(letra) if m and m in texto})


def req(c, metodo, ruta, **kw):
    for _ in range(8):
        r = c.request(metodo, BASE + ruta, **kw)
        if r.status_code == 429 and "LOCKED" not in r.text:
            time.sleep(62 - (int(time.time()) % 60))
            continue
        return r
    return r


def reg(usuario, cat, caso, ruta, esperado, obtenido, ok, detalle=""):
    RES.append({"usuario": usuario, "categoria": cat, "caso": caso, "ruta": ruta, "esperado": esperado,
                "obtenido": obtenido, "ok": bool(ok), "detalle": str(detalle)[:400]})
    print(f"{'OK ' if ok else 'XX '} {usuario:10} {cat:9} {caso:48} {obtenido} {'' if ok else str(detalle)[:160]}",
          flush=True)


def cliente(u):
    c = httpx.Client(verify=False, timeout=90)
    body = login(c, u["username"], u["clave"], u.get("totp"))
    c.headers["Authorization"] = f"Bearer {body['access_token']}"
    return c


def texto(r):
    try:
        return r.text
    except Exception:
        return ""


def pdf_texto(r):
    """Texto aproximado de un PDF (para buscar marcadores en actas/etiquetas)."""
    try:
        from pypdf import PdfReader
        return "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(r.content)).pages)
    except Exception:
        return r.content.decode("latin-1", "ignore")


# ─────────────────────────────── LECTURAS ────────────────────────────────
def lecturas(clave, u, c, dentro, fuera):
    rol = u["rol"]
    inv = rol in BIZ
    sof, lic = EST["software"], EST["licencia"]
    listados = [
        ("GET", "/core/activos?limit=200", None, BIZ),
        ("POST", "/core/activos/search", {"q": "QA3-", "per_page": 200}, BIZ),
        ("POST", "/core/activos/search", {"per_page": 200, "sede_id": SED["E"]["sede"]}, BIZ),
        ("GET", "/trazabilidad/movimientos?limit=200", None, BIZ),
        ("GET", "/mantenimiento/", None, BIZ),
        ("GET", "/consumibles", None, BIZ),
        ("GET", "/compras/ordenes", None, BIZ),
        ("GET", "/compras/garantias", None, BIZ),
        ("GET", "/stats/dashboard", None, BIZ),
        ("GET", f"/soft/licencias?software_id={sof}", None, BIZ),
        ("GET", f"/soft/licencias/{lic}", None, BIZ),
        ("GET", "/org/personas", None, None),
        ("GET", "/org/personas/disponibles", None, None),
        ("GET", "/org/departamentos/resumen", None, None),
        ("GET", "/org/departamentos/2/detalle", None, None),
        ("GET", "/org/departamentos/2/contexto", None, None),
        ("GET", "/cat/modelos/3/contexto", None, BIZ),
        ("GET", "/org/sedes", None, None),
        ("GET", "/geo/areas/all", None, BIZ),
        ("GET", "/gov/auditoria?limit=200", None, AUDIT),
        ("GET", "/gov/auditoria/resumen", None, AUDIT),
        ("GET", "/export/activos.csv", None, EXPORT),
        ("GET", "/export/movimientos.csv", None, EXPORT),
        ("GET", "/export/consumibles.csv", None, EXPORT),
        ("GET", "/export/ordenes.csv", None, EXPORT),
        ("GET", "/export/proveedores.csv", None, EXPORT),
        ("GET", "/export/auditoria.csv", None, AUDIT),
    ]
    for metodo, ruta, body, roles in listados:
        r = req(c, metodo, ruta, json=body) if body is not None else req(c, metodo, ruta)
        permitido = roles is None or rol in roles
        if not permitido:
            reg(clave, "rol", f"{metodo} {ruta.split('?')[0]} denegado", ruta, "403", r.status_code,
                r.status_code == 403, texto(r)[:200])
            continue
        t = texto(r)
        f = fugas(t, fuera)
        if rol == "ADMIN_SEGURIDAD":
            # Sin inventario: ningún marcador de activos/movimientos de ninguna sede.
            inv_m = sorted({m for letra in LETRAS for m in (SED[letra]["codigo"], SED[letra]["serie"],
                                                            SED[letra]["hostname"]) if m in t})
            reg(clave, "sin-inv", f"{metodo} {ruta.split('?')[0]} sin datos de inventario", ruta,
                "2xx sin activos / 403", r.status_code, not inv_m, inv_m)
            continue
        reg(clave, "lectura", f"{metodo} {ruta.split('?')[0]}{' sede E' if body and body.get('sede_id') else ''}",
            ruta, "2xx sin fugas", r.status_code, r.is_success and not f, f or t[:200])
        # Positivo: lo del alcance sí aparece (listados de activos y exportes de activos).
        if ruta.startswith(("/core/activos?", "/export/activos.csv")) or (body and body.get("q")):
            falt = [letra for letra in dentro if SED[letra]["codigo"] not in t]
            reg(clave, "positivo", f"{ruta.split('?')[0]} incluye sus sedes", ruta, "todas sus sedes",
                "faltan " + ",".join(falt) if falt else "ok", not falt, falt)
        if ruta.startswith("/stats/dashboard") and r.is_success:
            RES[-1]["detalle"] = json.dumps(r.json(), ensure_ascii=False)[:400]
        if ruta.startswith("/gov/auditoria?") and r.is_success and rol == "AUDITOR":
            sedes_ok = {SED[x]["sede"] for x in dentro}
            vistos = {i.get("AUD_Sede") for i in r.json()["items"]}
            ajenas = sorted(s for s in vistos if s is not None and s not in sedes_ok) if clave[-1] != "g" else []
            reg(clave, "lectura", "bitácora: AUD_Sede dentro del alcance", ruta, "solo sus sedes/propias",
                "ajenas=" + str(ajenas), not ajenas, ajenas)
            propias_nulas = [i for i in r.json()["items"] if i.get("AUD_Sede") is None
                             and i.get("USU_Usuario") != u["id"]] if clave[-1] != "g" else []
            reg(clave, "lectura", "bitácora: sin eventos sin sede de otros usuarios", ruta,
                "0", len(propias_nulas), not propias_nulas,
                [(i.get("AUD_Accion"), i.get("AUD_Entidad")) for i in propias_nulas[:5]])
        if ruta == "/org/sedes" and r.is_success and clave[-1] != "g":
            ids = {s["SED_Sede"] for s in r.json()}
            ajenas = ids - {SED[x]["sede"] for x in dentro}
            reg(clave, "lectura", "/org/sedes solo sedes del alcance", ruta, "solo sus sedes",
                sorted(ajenas), not ajenas, ajenas)
        if ruta == "/geo/areas/all" and r.is_success and clave[-1] != "g":
            ajenas = {a.get("SED_Sede") for a in r.json()} - {SED[x]["sede"] for x in dentro}
            reg(clave, "lectura", "/geo/areas/all solo áreas del alcance (sugerencias)", ruta, "solo sus sedes",
                sorted(ajenas, key=str), not ajenas, ajenas)

    # Detalle por id / subrecursos, por sede
    for letra in LETRAS:
        s = SED[letra]
        en = letra in dentro
        detalles = [
            ("GET", f"/core/activos/{s['activo']}", None, BIZ),
            ("GET", f"/core/activos/by-code/{s['codigo']}", None, BIZ),
            ("GET", f"/core/activos/{s['activo']}/especificaciones", None, BIZ),
            ("GET", f"/core/activos/{s['activo']}/contexto", None, BIZ),
            ("GET", f"/trazabilidad/activo/{s['activo']}/historial", None, BIZ),
            ("GET", f"/trazabilidad/persona/{s['persona']}/asignaciones", None, BIZ),
            ("GET", f"/mantenimiento/{s['mantenimiento']}", None, BIZ),
            ("GET", f"/mantenimiento/{s['mantenimiento']}/detalles", None, BIZ),
            ("GET", f"/consumibles/{s['consumible']}", None, BIZ),
            ("GET", f"/consumibles/{s['consumible']}/movimientos", None, BIZ),
            ("GET", f"/compras/ordenes/{s['orden']}", None, BIZ),
            ("GET", f"/adjuntos/activos/{s['activo']}", None, BIZ),
            ("GET", f"/adjuntos/ordenes/{s['orden']}", None, BIZ),
            ("GET", f"/adjuntos/{s['adjunto']}/download", None, BIZ),
            ("GET", f"/soft/activos/{s['activo']}/instalaciones", None, BIZ),
            ("GET", f"/soft/personas/{s['persona']}/instalaciones", None, BIZ),
            ("GET", f"/org/personas/{s['persona']}", None, None),
            ("GET", f"/org/personas/{s['persona']}/contexto", None, None),
            ("GET", f"/trazabilidad/acta/{s['movimiento']}?formato=pdf", None, OPER),
            ("POST", "/trazabilidad/acta/lote?formato=pdf", {"movimientos_ids": [s["movimiento"]]}, OPER),
            ("POST", "/core/activos/etiquetas.pdf", {"activos_ids": [s["activo"]]}, OPER),
            ("POST", "/core/activos/etiquetas.pdf", {"codigos": [s["codigo"]]}, OPER),
            ("POST", "/core/activos/search", {"q": s["serie"], "per_page": 5}, BIZ),
            ("POST", "/core/activos/search", {"q": s["hostname"], "per_page": 5}, BIZ),
        ]
        for metodo, ruta, body, roles in detalles:
            r = req(c, metodo, ruta, json=body) if body is not None else req(c, metodo, ruta)
            nombre = f"{metodo} {ruta.replace(s['activo'], '{act}').replace(s['persona'], '{per}').replace(s['movimiento'], '{mov}')}"
            if body and "codigos" in body:
                nombre += " (códigos)"
            if body and "q" in body:
                nombre = f"search q={'serie' if body['q'] == s['serie'] else 'hostname'}"
            nombre += f" sede {letra}"
            permitido = roles is None or rol in roles
            if not permitido:
                reg(clave, "rol", nombre + " denegado", ruta, "403", r.status_code, r.status_code == 403,
                    texto(r)[:150])
                continue
            es_pdf = "pdf" in r.headers.get("content-type", "") or ruta.endswith("download")
            t = pdf_texto(r) if es_pdf else texto(r)
            if rol == "ADMIN_SEGURIDAD":
                inv_m = [m for m in (s["codigo"], s["serie"], s["hostname"]) if m in t]
                reg(clave, "sin-inv", nombre + " sin inventario", ruta, "sin activos / 403", r.status_code,
                    not inv_m, inv_m)
                continue
            if en:
                bien = r.is_success
                reg(clave, "detalle", nombre + " (dentro)", ruta, "2xx", r.status_code, bien, t[:150])
            else:
                f = fugas(t, {letra})
                if es_pdf and r.is_success:
                    t = f"[PDF {len(r.content)} bytes] " + t
                vacio = r.is_success and t.strip() in ("[]", "")
                bien = r.status_code in (403, 404) or vacio or (r.is_success and not f)
                reg(clave, "fuera", nombre + " (fuera)", ruta, "403/404/vacío", r.status_code, bien, f or t[:150])


# ─────────────────────────────── ESCRITURAS ──────────────────────────────
def escrituras(clave, u, c, dentro, fuera):
    rol = u["rol"]
    if rol == "ADMIN_SEGURIDAD":
        casos = [
            ("POST", "/core/activos", {"ACT_Serie_Fabricante": f"QA3-SEG-{EST['suf']}", "ACT_Fecha_Compra": "2025-01-01",
                                       "MOD_Modelo": 3, "TAC_Tipo_Activo": 1, "EOP_Estado_Operativo": 1,
                                       "SED_Sede": SED["N"]["sede"]}),
            ("POST", "/org/personas", {"PER_Primer_Nombre": "Qa", "PER_Primer_Apellido": "Seg",
                                       "PER_Email_Corporativo": f"qa3.seg.p.{EST['suf']}@lombardi-qa.com",
                                       "DEP_Departamento": 2, "CAR_Cargo": 2, "SED_Sede": SED["N"]["sede"]}),
            ("PATCH", f"/org/personas/{SED['N']['persona']}", {"PER_Telefono": "000"}),
        ]
        for m, ruta, body in casos:
            r = req(c, m, ruta, json=body)
            reg(clave, "sin-inv", f"{m} {ruta.split('/')[1]}/{ruta.split('/')[2]} escritura", ruta, "403",
                r.status_code, r.status_code == 403, texto(r)[:150])
        return
    N = SED["N"]
    for letra in fuera:
        s = SED[letra]
        casos = [
            ("crear activo en sede ajena", "POST", "/core/activos",
             {"ACT_Serie_Fabricante": f"QA3-W-{clave}-{letra}-{EST['suf']}", "ACT_Fecha_Compra": "2025-01-01",
              "MOD_Modelo": 3, "TAC_Tipo_Activo": 1, "EOP_Estado_Operativo": 1, "SED_Sede": s["sede"]}),
            ("editar activo ajeno", "PATCH", f"/core/activos/{s['activo2']}", {"ACT_Hostname": "QA3-HACK"}),
            ("mover activo propio a sede ajena (PATCH SED_Sede)", "PATCH", f"/core/activos/{N['activo2']}",
             {"SED_Sede": s["sede"]}),
            ("asignar activo propio a área ajena (mueve la sede)", "POST", "/trazabilidad/movimientos",
             {"ACT_Activo": N["activo2"], "PER_Persona": N["persona"], "ARE_Area": s["area"],
              "TMO_Tipo_Movimiento": 2}),
            ("transferir activo propio a área/persona ajena", "POST", "/trazabilidad/transferencia",
             {"ACT_Activo": N["activo3"], "PER_Persona_Destino": s["persona"], "ARE_Area_Destino": s["area"]}),
            ("asignar activo ajeno", "POST", "/trazabilidad/movimientos",
             {"ACT_Activo": s["activo2"], "PER_Persona": N["persona"], "ARE_Area": N["area"],
              "TMO_Tipo_Movimiento": 2}),
            ("devolver activo ajeno", "POST", "/trazabilidad/devolucion", {"ACT_Activo": s["activo3"]}),
            ("recibir orden de sede ajena", "POST", f"/compras/ordenes/{s['orden']}/recibir",
             {"activos": [{"OCL_Linea": s["orden_linea"], "ACT_Serie_Fabricante": f"QA3-REC-{clave}-{EST['suf']}",
                           "MOD_Modelo": 3, "TAC_Tipo_Activo": 1, "ACT_Fecha_Compra": "2026-09-01"}]}),
            ("cambiar estado orden ajena", "PATCH", f"/compras/ordenes/{s['orden']}/estado", {"OCO_Estado": "CANCELADA"}),
            ("crear orden en sede ajena", "POST", "/compras/ordenes",
             {"OCO_Numero": f"QA3-OCW-{clave}-{letra}-{EST['suf']}", "OCO_Fecha": "2026-09-01",
              "PRV_Proveedor": EST["proveedor"], "SED_Sede": s["sede"], "lineas": []}),
            ("crear consumible en sede ajena", "POST", "/consumibles",
             {"CON_Nombre": f"QA3-W-{clave}-{letra}", "SED_Sede": s["sede"]}),
            ("entrada de stock a consumible ajeno", "POST", f"/consumibles/{s['consumible']}/entrada",
             {"MOC_Cantidad": 1}),
            ("salida de stock de consumible ajeno", "POST", f"/consumibles/{s['consumible']}/salida",
             {"MOC_Cantidad": 1}),
            ("abrir mantenimiento a activo ajeno", "POST", "/mantenimiento/",
             {"ACT_Activo": s["activo2"], "PER_Persona_Solicita": N["persona"], "TMA_Tipo_Mantenimiento": 1,
              "MAN_Descripcion_Falla": "QA3"}),
            ("cerrar mantenimiento ajeno", "PATCH", f"/mantenimiento/{s['mantenimiento']}/cerrar",
             {"MAN_Costo_Total": 1}),
            ("especificación a activo ajeno", "POST", f"/core/activos/{s['activo']}/especificaciones",
             {"TES_Tipo_Especificacion": 1, "ESP_Valor": "QA3"}),
            ("crear persona en sede ajena", "POST", "/org/personas",
             {"PER_Primer_Nombre": "Qa", "PER_Primer_Apellido": "Ajena",
              "PER_Email_Corporativo": f"qa3.w.{clave}.{letra}.{EST['suf']}@lombardi-qa.com",
              "DEP_Departamento": 2, "CAR_Cargo": 2, "SED_Sede": s["sede"]}),
            ("editar persona ajena", "PATCH", f"/org/personas/{s['persona']}", {"PER_Telefono": "000"}),
            ("mover persona propia a sede ajena", "PATCH", f"/org/personas/{N['persona']}", {"SED_Sede": s["sede"]}),
            ("crear edificio en sede ajena", "POST", "/geo/edificios", {"EDI_Nombre": "QA3-W", "SED_Sede": s["sede"]}),
            ("crear área en nivel de sede ajena", "POST", "/geo/areas", {"ARE_Nombre": "QA3-W", "NIV_Nivel": s["nivel"]}),
            ("editar sede ajena", "PATCH", f"/geo/sedes/{s['sede']}", {"SED_Direccion_Calle": "QA3"}),
            ("subir adjunto a activo ajeno", "POST", f"/adjuntos/activos/{s['activo']}", "FILE"),
            ("instalar software en activo ajeno", "POST", "/soft/instalaciones",
             {"ACT_Activo": s["activo2"], "LIC_Licencia": EST["licencia"]}),
            ("borrar adjunto ajeno", "DELETE", f"/adjuntos/{s['adjunto']}", None),
            ("baja de activo ajeno", "DELETE", f"/core/activos/{s['activo2']}", None),
        ]
        for caso, m, ruta, body in casos:
            if body == "FILE":
                r = req(c, m, ruta, files={"file": ("qa3.pdf", io.BytesIO(b"%PDF-1.4\n%%EOF\n"), "application/pdf")})
            elif body is None:
                r = req(c, m, ruta)
            else:
                r = req(c, m, ruta, json=body)
            bien = r.status_code in (403, 404) or (r.status_code == 400 and "NOT_ASSIGNED" in r.text)
            reg(clave, "escritura", f"{caso} [{letra}]", ruta, "403 (404 aceptable)", r.status_code, bien,
                texto(r)[:200])
    # Controles positivos y de rol dentro del alcance
    crea = req(c, "POST", "/core/activos", json={
        "ACT_Serie_Fabricante": f"QA3-OK-{clave}-{EST['suf']}", "ACT_Fecha_Compra": "2025-01-01", "MOD_Modelo": 3,
        "TAC_Tipo_Activo": 1, "EOP_Estado_Operativo": 1, "SED_Sede": N["sede"]})
    esperado = 201 if rol in OPER else 403
    reg(clave, "rol", "crear activo en su sede", "/core/activos", str(esperado), crea.status_code,
        crea.status_code == esperado, texto(crea)[:150])
    r = req(c, "PATCH", f"/core/activos/{N['activo2']}", json={"ACT_Hostname": f"QA3-ED-{clave}"[:40]})
    esperado = 200 if rol in OPER else 403
    reg(clave, "rol", "editar activo de su sede", "/core/activos/{id}", str(esperado), r.status_code,
        r.status_code == esperado, texto(r)[:150])
    r = req(c, "POST", "/compras/ordenes", json={
        "OCO_Numero": f"QA3-OCOK-{clave}-{EST['suf']}", "OCO_Fecha": "2026-09-01", "PRV_Proveedor": EST["proveedor"],
        "SED_Sede": N["sede"], "lineas": []})
    esperado = 201 if rol in ADMIN else 403
    reg(clave, "rol", "crear orden en su sede", "/compras/ordenes", str(esperado), r.status_code,
        r.status_code == esperado, texto(r)[:150])
    if rol in ("AUDITOR", "CONSULTA", "TECNICO"):
        for caso, m, ruta, body in [
            ("baja lógica de activo de su sede", "DELETE", f"/core/activos/{N['activo2']}", None),
            ("crear persona en su sede", "POST", "/org/personas", {
                "PER_Primer_Nombre": "Qa", "PER_Primer_Apellido": "Rol",
                "PER_Email_Corporativo": f"qa3.r.{clave}.{EST['suf']}@lombardi-qa.com",
                "DEP_Departamento": 2, "CAR_Cargo": 2, "SED_Sede": N["sede"]}),
            ("crear consumible en su sede", "POST", "/consumibles", {"CON_Nombre": f"QA3-R-{clave}", "SED_Sede": N["sede"]}),
            ("crear marca (catálogo)", "POST", "/cat/marcas", {"MAR_Nombre": f"QA3-R-{clave}"}),
        ] + ([
            ("asignar activo de su sede", "POST", "/trazabilidad/movimientos",
             {"ACT_Activo": N["activo2"], "PER_Persona": N["persona"], "ARE_Area": N["area"], "TMO_Tipo_Movimiento": 2}),
            ("salida de stock en su sede", "POST", f"/consumibles/{N['consumible']}/salida", {"MOC_Cantidad": 1}),
            ("abrir mantenimiento en su sede", "POST", "/mantenimiento/", {
                "ACT_Activo": N["activo2"], "PER_Persona_Solicita": N["persona"], "TMA_Tipo_Mantenimiento": 1,
                "MAN_Descripcion_Falla": "QA3"}),
            ("etiquetas de su sede", "POST", "/core/activos/etiquetas.pdf", {"activos_ids": [N["activo"]]}),
        ] if rol != "TECNICO" else []):
            r = req(c, m, ruta, json=body) if body is not None else req(c, m, ruta)
            reg(clave, "rol", f"{caso} (solo lectura/rol)", ruta, "403", r.status_code, r.status_code == 403,
                texto(r)[:150])


def main():
    solo = set(sys.argv[1].split(",")) if len(sys.argv) > 1 else None
    for clave, u in EST["usuarios"].items():
        if clave.startswith("qa3_seg2") or (solo and clave not in solo):
            continue
        alc = clave.rsplit("_", 1)[-1]
        dentro = set(LETRAS) if u["rol"] == "ADMIN_SEGURIDAD" else ALCANCE[alc]
        fuera = set(LETRAS) - dentro
        c = cliente(u)
        me = c.get(BASE + "/me").json()
        reg(clave, "perfil", "/me refleja rol y alcance", "/me", f"{u['rol']} {sorted(dentro)}",
            json.dumps(me.get("scope")), me.get("role") == u["rol"], me)
        lecturas(clave, u, c, dentro, fuera)
        escrituras(clave, u, c, dentro, fuera)
        c.close()
        guardar(solo)
    guardar(solo)


def guardar(solo):
    sufijo = "" if not solo else "_" + "_".join(sorted(solo))
    with open(os.path.join(OUT, f"matriz_resultados{sufijo}.json"), "w", encoding="utf-8") as f:
        json.dump(RES, f, ensure_ascii=False, indent=1)
    with open(os.path.join(OUT, f"matriz_resultados{sufijo}.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(RES[0].keys()) if RES else ["usuario"])
        w.writeheader()
        w.writerows(RES)
    fallos = [r for r in RES if not r["ok"]]
    print(f"\n{len(RES) - len(fallos)}/{len(RES)} correctos; {len(fallos)} fallos")


if __name__ == "__main__":
    main()

"""
QA de las fases 3 a 6 contra el stack de revisión (inv_ui, https://localhost:8443).

Recorre por la API, como sa, los flujos nuevos sobre datos reales:
  1. Compra con líneas de equipo y de servicio: recepción parcial, precio
     distinto (exige justificación), sobrante rechazado, conformidad del
     servicio, bandeja «Requiere su atención» y cierre con faltante.
  2. Kardex y hoja de vida del equipo recibido; baja formal con motivo, que
     queda pendiente hasta que el Jefe de TI (otra persona) la aprueba.
  3. Consumible: saldo inicial valorizado, costo promedio ponderado, salida
     que deja el stock bajo el mínimo (el servidor lo señala) y ajuste por conteo.
  (Requiere la cuenta JEFE_TI: python herramientas-qa/crear_jefe_ti.py)
  4. Reportes: licencias.csv, custodia, entradas y salidas.
  5. El frontend publicado incluye las pantallas nuevas.

Uso:  python herramientas-qa/qa9_fases.py      (resultado en salidas/qa9_fases.json)
"""
import json
import pathlib
import re
import time

import httpx

from qa_login import cliente_autenticado

BASE = "https://localhost:8443"
API = f"{BASE}/api/v1"
SALIDA = pathlib.Path(__file__).parent / "salidas" / "qa9_fases.json"
resultados: list[dict] = []


def caso(nombre: str, ok: bool, detalle: str = "") -> None:
    resultados.append({"caso": nombre, "ok": bool(ok), "detalle": detalle})
    print(("OK   " if ok else "FALLA"), nombre, ("· " + detalle) if detalle and not ok else "")


def main() -> None:
    c = cliente_autenticado()
    cj = json.loads((pathlib.Path(__file__).parent / "salidas" / "credenciales_jefe.json").read_text(encoding="utf-8"))
    jefe = cliente_autenticado(cj["usuario"], cj["clave"], cj["totp_secret"])
    sufijo = time.strftime("%H%M%S")

    # ---------- Datos base ----------
    sedes = c.get(f"{API}/org/sedes").json()
    sede = sedes[0]["SED_Sede"]
    tipos = c.get(f"{API}/cat/tipos-activo").json()
    tipo = next((t for t in tipos if t["TAC_Nombre"].lower().startswith("laptop")), tipos[0])
    modelos = c.get(f"{API}/cat/modelos-flat").json()
    modelo = next((m for m in modelos if m.get("TAC_Tipo_Activo") in (None, tipo["TAC_Tipo_Activo"])), modelos[0])
    prov = c.post(f"{API}/compras/proveedores", json={"PRV_Nombre": f"QA9 Proveedor {sufijo}"}).json()

    # ---------- 1. Compra con recepción controlada ----------
    orden = c.post(f"{API}/compras/ordenes", json={
        "OCO_Numero": f"QA9-{sufijo}", "OCO_Fecha": time.strftime("%Y-%m-%d"), "OCO_Moneda": "USD",
        "PRV_Proveedor": prov["PRV_Proveedor"], "SED_Sede": sede,
        "lineas": [
            {"OCL_Descripcion": "Laptop QA9", "OCL_Cantidad": 2, "OCL_Precio_Unitario": "1000", "OCL_Tipo": "ACTIVO"},
            {"OCL_Descripcion": "Flete QA9", "OCL_Cantidad": 1, "OCL_Precio_Unitario": "40", "OCL_Tipo": "OTRO"},
        ]})
    caso("Crear orden con tipo por línea", orden.status_code == 201, orden.text[:200])
    orden = orden.json()
    oid = orden["OCO_Orden"]
    l_act, l_otro = (ln["OCL_Linea"] for ln in orden["lineas"])

    def activo(serie: str, costo: str | None = None) -> dict:
        d = {"OCL_Linea": l_act, "ACT_Serie_Fabricante": serie, "MOD_Modelo": modelo["MOD_Modelo"],
             "TAC_Tipo_Activo": tipo["TAC_Tipo_Activo"], "ACT_Fecha_Compra": time.strftime("%Y-%m-%d")}
        if costo:
            d["ACT_Costo"] = costo
        return d

    r = c.post(f"{API}/compras/ordenes/{oid}/recibir", json={"activos": [activo(f"QA9A{sufijo}", "1100")]})
    caso("Precio fuera de tolerancia exige justificación",
         r.status_code == 400 and r.json().get("detail") == f"PRICE_DIFFERENCE_REQUIRES_JUSTIFICATION:{l_act}", r.text[:200])

    r = c.post(f"{API}/compras/ordenes/{oid}/recibir", json={"activos": [activo(f"QA9A{sufijo}{i}") for i in range(3)]})
    caso("Sobrante rechazado sin justificación",
         r.status_code == 400 and r.json().get("detail", "").startswith("RECEIPT_EXCEEDS_PENDING"), r.text[:200])

    r = c.post(f"{API}/compras/ordenes/{oid}/recibir", json={
        "documento": "Guía QA9-1", "activos": [activo(f"QA9A{sufijo}", "1100")],
        "diferencias": [{"OCL_Linea": l_act, "motivo": "Tipo de cambio de la factura"}]})
    ok = r.status_code == 200 and r.json()["OCO_Estado"] == "PARCIAL"
    caso("Recepción parcial con precio justificado", ok, r.text[:300])
    res = r.json() if ok else {}
    codigo = (res.get("activos_codigos") or [None])[0]
    caso("Resultado informa códigos, pendientes y diferencias",
         bool(codigo) and len(res.get("pendientes", [])) == 2 and res.get("diferencias", [{}])[0].get("tipo") == "PRECIO",
         json.dumps(res)[:300])

    atencion = c.get(f"{API}/inventario/atencion").json()
    parcial = next((i for i in atencion["items"] if i["clave"] == "ORDEN_PARCIAL"), None)
    caso("Bandeja de atención incluye la orden parcial",
         bool(parcial) and any(x["id"] == oid for x in parcial["items"]), json.dumps(atencion)[:200])

    r = c.post(f"{API}/compras/ordenes/{oid}/recibir", json={"otros": [{"OCL_Linea": l_otro, "cantidad": 1}]})
    caso("Conformidad del flete (línea OTRO)", r.status_code == 200 and r.json()["OCO_Estado"] == "PARCIAL", r.text[:200])

    r = c.patch(f"{API}/compras/ordenes/{oid}/estado", json={"OCO_Estado": "CANCELADA"})
    caso("No se cancela una orden con entregas", r.status_code in (400, 409), r.text[:200])

    r = c.post(f"{API}/compras/ordenes/{oid}/cerrar", json={"motivo": "El proveedor no tiene la segunda unidad"})
    caso("Cierre con faltante", r.status_code == 200 and r.json()["OCO_Estado"] == "CERRADA_CON_FALTANTE", r.text[:200])
    recs = c.get(f"{API}/compras/ordenes/{oid}/recepciones").json()
    caso("Entregas consultables con documento", len(recs) == 2 and recs[0]["REC_Documento"] == "Guía QA9-1", json.dumps(recs)[:200])

    # ---------- 2. Kardex, hoja de vida y baja ----------
    if codigo:
        a = c.get(f"{API}/core/activos/by-code/{codigo}").json()
        aid = a.get("ACT_Activo")
        k = c.get(f"{API}/inventario/kardex", params={"activo_id": aid}).json()
        tipos_ev = [e["KDX_Tipo"] for e in k.get("items", [])]
        caso("Kardex del equipo registra la recepción", "RECEPCION" in tipos_ev, str(tipos_ev))
        pdf = c.get(f"{API}/inventario/activos/{aid}/hoja-de-vida.pdf", params={"lang": "es"})
        caso("Hoja de vida en PDF", pdf.status_code == 200 and pdf.content[:4] == b"%PDF", str(pdf.status_code))
        r = c.post(f"{API}/core/activos/{aid}/baja", json={"motivo": "OTRO", "destino": "RESGUARDO"})
        caso("Baja con motivo «Otro» exige observación", r.status_code in (400, 422), r.text[:200])
        r = c.post(f"{API}/core/activos/{aid}/baja", json={
            "motivo": "DANO_IRREPARABLE", "destino": "RECICLAJE", "documento": "Informe QA9"})
        caso("Baja queda como solicitud pendiente", r.status_code == 202 and r.json()["APR_Estado"] == "PENDIENTE",
             r.text[:200])
        apr = r.json().get("APR_Aprobacion")
        persona = next(x for x in c.get(f"{API}/org/personas").json() if x.get("PER_Estado", True))
        area = next(x for x in c.get(f"{API}/geo/areas/all").json() if x.get("SED_Sede") in (None, sede))
        tipos_mov = c.get(f"{API}/trazabilidad/tipos").json()
        tmo = next(x for x in tipos_mov if "asign" in x["TMO_Nombre"].lower())
        r = c.post(f"{API}/trazabilidad/movimientos", json={
            "ACT_Activo": aid, "PER_Persona": persona["PER_Persona"], "ARE_Area": area["ARE_Area"],
            "TMO_Tipo_Movimiento": tmo["TMO_Tipo_Movimiento"]})
        caso("Equipo congelado mientras la baja está pendiente",
             r.status_code == 409 and r.json().get("detail") == "ASSET_HAS_PENDING_DECOMMISSION", r.text[:200])
        r = c.post(f"{API}/aprobaciones/{apr}/aprobar", json={})
        caso("Quien solicita no puede aprobar", r.status_code == 403, r.text[:200])
        r = jefe.post(f"{API}/aprobaciones/{apr}/aprobar", json={"comentario": "Revisado en QA9"})
        caso("El Jefe de TI aprueba y la baja se ejecuta", r.status_code == 200 and r.json()["APR_Estado"] == "APROBADA",
             r.text[:200])
        k = c.get(f"{API}/inventario/kardex", params={"activo_id": aid}).json()
        baja = next((e for e in k.get("items", []) if e["KDX_Tipo"] == "BAJA"), {})
        caso("Kardex registra la baja con solicitante y aprobador",
             bool(baja) and baja["KDX_Detalle"].get("aprobado_por") not in (None, baja["KDX_Detalle"].get("solicitado_por")),
             json.dumps(baja)[:200])
        r = c.post(f"{API}/trazabilidad/movimientos", json={"ACT_Activo": aid})
        caso("Un activo de baja no admite asignación", r.status_code in (400, 409, 422), r.text[:200])

    # ---------- 3. Consumible ----------
    con = c.post(f"{API}/consumibles", json={"CON_Nombre": f"Tóner QA9 {sufijo}", "CON_Unidad": "unidad",
                                              "CON_Stock_Actual": 2, "CON_Stock_Minimo": 3, "SED_Sede": sede,
                                              "CON_Costo_Unitario": "20", "CON_Moneda": "USD"}).json()
    cid = con["CON_Consumible"]
    ini = c.get(f"{API}/inventario/kardex", params={"consumible_id": cid}).json()["items"]
    caso("El saldo inicial queda en el kardex valorizado",
         len(ini) == 1 and ini[0]["KDX_Saldo"] == 2 and float(ini[0]["KDX_Saldo_Valor"]) == 40.0, json.dumps(ini)[:200])
    r = c.post(f"{API}/consumibles/{cid}/entrada", json={"MOC_Cantidad": 2, "precio_unitario": "30", "moneda": "USD"})
    caso("Entrada de consumible con costo y moneda", r.status_code == 201, r.text[:200])
    det = c.get(f"{API}/consumibles/{cid}").json()
    caso("Costo promedio ponderado ((2×20 + 2×30) / 4 = 25)",
         float(det.get("CON_Costo_Unitario") or 0) == 25.0 and det.get("CON_Moneda") == "USD", json.dumps(det)[:200])
    r = c.post(f"{API}/consumibles/{cid}/entrada", json={"MOC_Cantidad": 1, "precio_unitario": "100", "moneda": "GTQ"})
    caso("No se promedian monedas distintas", r.status_code == 400, r.text[:200])
    r = c.post(f"{API}/consumibles/{cid}/entrada", json={"MOC_Cantidad": 1})
    caso("Entrada sin costo entra al promedio", r.status_code == 201, r.text[:200])
    r = c.post(f"{API}/consumibles/{cid}/salida", json={"MOC_Cantidad": 3})
    caso("Salida que deja el stock bajo el mínimo lo señala", r.status_code == 201 and r.json().get("bajo_minimo") is True, r.text[:200])
    r = c.post(f"{API}/consumibles/{cid}/ajuste", json={"CON_Stock_Real": 2, "MOC_Motivo": "corto"})
    caso("Ajuste con motivo corto se rechaza", r.status_code in (400, 422), r.text[:200])
    r = c.post(f"{API}/consumibles/{cid}/ajuste", json={"CON_Stock_Real": 1, "MOC_Motivo": "Conteo físico mensual"})
    caso("Ajuste por conteo", r.status_code == 201 and r.json()["MOC_Stock_Resultante"] == 1, r.text[:200])

    # ---------- 4. Reportes ----------
    for ruta in ["/inventario/licencias.csv?lang=es", "/inventario/custodia?lang=es", "/inventario/entradas-salidas",
                 "/inventario/kardex.csv?lang=en"]:
        r = c.get(f"{API}{ruta}")
        caso(f"Reporte {ruta.split('?')[0]}", r.status_code == 200, f"{r.status_code} {r.text[:120]}")

    # ---------- 5. Kardex verificable ----------
    v = jefe.get(f"{API}/inventario/kardex/verificar").json()
    caso("Kardex íntegro (sello encadenado)", v.get("integro") is True and v.get("ultima_secuencia") == v.get("total"),
         json.dumps(v)[:200])

    # ---------- 6. Frontend publicado ----------
    index = httpx.get(f"{BASE}/", verify=False).text
    js = [m for m in re.findall(r'src="(/assets/[^"]+\.js)"', index)]
    bundle = "".join(httpx.get(f"{BASE}{p}", verify=False).text for p in js)
    caso("Frontend enruta /trazabilidad, /inventario/tomas y /aprobaciones",
         all(x in bundle for x in ("/trazabilidad", "/inventario/tomas", "/aprobaciones")), str(js))

    SALIDA.parent.mkdir(exist_ok=True)
    SALIDA.write_text(json.dumps(resultados, ensure_ascii=False, indent=2), encoding="utf-8")
    fallas = [r for r in resultados if not r["ok"]]
    print(f"\n{len(resultados) - len(fallas)}/{len(resultados)} casos OK")


if __name__ == "__main__":
    main()

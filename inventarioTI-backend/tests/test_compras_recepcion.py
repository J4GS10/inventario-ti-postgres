"""
Fase 3 — recepción controlada de compras (QA2 §2, B1, B2, A7).

  - Entregas parciales: la orden queda PARCIAL con lo pendiente y se completa
    en otra recepción; nunca se recibe más de lo pedido sin justificación.
  - Sobrante: se rechaza, o se acepta con motivo y aceptar_sobrante.
  - Precio fuera de tolerancia: exige justificación y queda registrado.
  - Una línea solo recibe lo que es (y el consumible indicado en la línea).
  - Cierre con faltante (con motivo) y no se cancela una orden con recepciones.
  - Cada entrega es un documento (guía/factura) consultable.
"""
import pytest

BASE = "/api/v1/compras"


async def _proveedor(client, h, nombre="Proveedor Recepción"):
    return (await client.post(f"{BASE}/proveedores", headers=h, json={"PRV_Nombre": nombre})).json()["PRV_Proveedor"]


async def _consumible(client, h, nombre="Tóner QA3"):
    return (await client.post("/api/v1/consumibles", headers=h, json={
        "CON_Nombre": nombre, "CON_Unidad": "unidad", "CON_Stock_Actual": 0, "CON_Stock_Minimo": 0,
    })).json()["CON_Consumible"]


async def _orden(client, h, pid, numero, lineas, moneda="GTQ"):
    r = await client.post(f"{BASE}/ordenes", headers=h, json={
        "OCO_Numero": numero, "OCO_Fecha": "2026-09-01", "PRV_Proveedor": pid, "OCO_Moneda": moneda,
        "lineas": lineas})
    assert r.status_code == 201, r.text
    return r.json()


def _activo(linea, serie, d, **extra):
    return {"OCL_Linea": linea, "ACT_Serie_Fabricante": serie, "MOD_Modelo": d["mod"],
            "TAC_Tipo_Activo": d["tac_lap"], "ACT_Fecha_Compra": "2026-09-01", **extra}


@pytest.mark.asyncio
async def test_recepcion_parcial_y_completa(client, auth_headers, domain_seed):
    h, d = auth_headers, domain_seed
    pid = await _proveedor(client, h)
    orden = await _orden(client, h, pid, "OC-PARC-1", [
        {"OCL_Descripcion": "Laptops", "OCL_Cantidad": 3, "OCL_Precio_Unitario": "1000", "OCL_Tipo": "ACTIVO"}])
    oid, linea = orden["OCO_Orden"], orden["lineas"][0]["OCL_Linea"]

    r = await client.post(f"{BASE}/ordenes/{oid}/recibir", headers=h, json={
        "documento": "Guía 001", "activos": [_activo(linea, "PARC-S1", d), _activo(linea, "PARC-S2", d)]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["OCO_Estado"] == "PARCIAL"
    assert body["pendientes"] == [{"OCL_Linea": linea, "descripcion": "Laptops", "tipo": "ACTIVO",
                                   "pedido": 3, "recibido": 2, "pendiente": 1}]

    # Recibir de más en la segunda entrega se rechaza (pendiente: 1).
    r = await client.post(f"{BASE}/ordenes/{oid}/recibir", headers=h, json={
        "activos": [_activo(linea, "PARC-S3", d), _activo(linea, "PARC-S4", d)]})
    assert r.status_code == 400
    assert r.json()["detail"] == f"RECEIPT_EXCEEDS_PENDING:{linea}:1"

    r = await client.post(f"{BASE}/ordenes/{oid}/recibir", headers=h, json={
        "documento": "Guía 002", "activos": [_activo(linea, "PARC-S3", d)]})
    assert r.status_code == 200, r.text
    assert r.json()["OCO_Estado"] == "RECIBIDA"
    assert r.json()["pendientes"] == []

    det = (await client.get(f"{BASE}/ordenes/{oid}", headers=h)).json()
    assert det["lineas"][0]["OCL_Cantidad_Recibida"] == 3
    assert det["lineas"][0]["OCL_Pendiente"] == 0
    recepciones = (await client.get(f"{BASE}/ordenes/{oid}/recepciones", headers=h)).json()
    assert [x["REC_Documento"] for x in recepciones] == ["Guía 001", "Guía 002"]
    assert [x["REC_Resultado"] for x in recepciones] == ["PARCIAL", "COMPLETA"]

    # Recibida es final.
    r = await client.post(f"{BASE}/ordenes/{oid}/recibir", headers=h, json={"activos": [_activo(linea, "PARC-S9", d)]})
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_sobrante_solo_con_justificacion(client, auth_headers, domain_seed, jefe_headers):
    h = auth_headers
    pid = await _proveedor(client, h)
    cid = await _consumible(client, h)
    orden = await _orden(client, h, pid, "OC-SOBRA", [
        {"OCL_Descripcion": "Tóner", "OCL_Cantidad": 10, "OCL_Precio_Unitario": "30", "CON_Consumible": cid}])
    oid, linea = orden["OCO_Orden"], orden["lineas"][0]["OCL_Linea"]
    item = {"consumibles": [{"OCL_Linea": linea, "CON_Consumible": cid, "cantidad": 12}]}
    r = await client.post(f"{BASE}/ordenes/{oid}/recibir", headers=h, json=item)
    assert r.status_code == 400 and r.json()["detail"].startswith("RECEIPT_EXCEEDS_PENDING")

    r = await client.post(f"{BASE}/ordenes/{oid}/recibir", headers=h, json={
        **item, "diferencias": [{"OCL_Linea": linea, "motivo": "El proveedor bonificó 2 unidades",
                                 "aceptar_sobrante": True}]})
    assert r.status_code == 200, r.text
    assert r.json()["diferencias"][0]["tipo"] == "SOBRANTE"
    # El sobrante lo aprueba el Jefe de TI (otra persona) antes de entrar.
    r = await client.post(f"/api/v1/aprobaciones/{r.json()['aprobacion_pendiente']}/aprobar",
                          headers=jefe_headers, json={})
    assert r.status_code == 200, r.text
    assert (await client.get(f"{BASE}/ordenes/{oid}", headers=h)).json()["OCO_Estado"] == "RECIBIDA"
    assert (await client.get(f"/api/v1/consumibles/{cid}", headers=h)).json()["CON_Stock_Actual"] == 12


@pytest.mark.asyncio
async def test_precio_fuera_de_tolerancia_exige_justificacion(client, auth_headers, domain_seed):
    h, d = auth_headers, domain_seed
    pid = await _proveedor(client, h)
    orden = await _orden(client, h, pid, "OC-PRECIO", [
        {"OCL_Descripcion": "Laptop", "OCL_Cantidad": 1, "OCL_Precio_Unitario": "1000"}], moneda="USD")
    oid, linea = orden["OCO_Orden"], orden["lineas"][0]["OCL_Linea"]
    # 1,5 % de diferencia: dentro de la tolerancia (2 %).
    r = await client.post(f"{BASE}/ordenes/{oid}/recibir", headers=h, json={
        "activos": [_activo(linea, "PRE-S1", d, ACT_Costo="1015")]})
    assert r.status_code == 200, r.text

    orden = await _orden(client, h, pid, "OC-PRECIO-2", [
        {"OCL_Descripcion": "Laptop", "OCL_Cantidad": 1, "OCL_Precio_Unitario": "1000"}], moneda="USD")
    oid, linea = orden["OCO_Orden"], orden["lineas"][0]["OCL_Linea"]
    r = await client.post(f"{BASE}/ordenes/{oid}/recibir", headers=h, json={
        "activos": [_activo(linea, "PRE-S2", d, ACT_Costo="1150")]})
    assert r.status_code == 400
    assert r.json()["detail"] == f"PRICE_DIFFERENCE_REQUIRES_JUSTIFICATION:{linea}"
    r = await client.post(f"{BASE}/ordenes/{oid}/recibir", headers=h, json={
        "activos": [_activo(linea, "PRE-S2", d, ACT_Costo="1150")],
        "diferencias": [{"OCL_Linea": linea, "motivo": "Aumento por tipo de cambio del proveedor"}]})
    assert r.status_code == 200, r.text
    assert r.json()["diferencias"][0]["tipo"] == "PRECIO"
    rec = (await client.get(f"{BASE}/ordenes/{oid}/recepciones", headers=h)).json()[0]
    assert rec["lineas"][0]["RCL_Diferencia"] == "PRECIO"
    assert float(rec["lineas"][0]["RCL_Precio_Real"]) == 1150.0


@pytest.mark.asyncio
async def test_linea_solo_recibe_lo_que_es(client, auth_headers, domain_seed):
    h, d = auth_headers, domain_seed
    pid = await _proveedor(client, h)
    cid = await _consumible(client, h, "Tóner línea")
    otro = await _consumible(client, h, "Papel otro")
    orden = await _orden(client, h, pid, "OC-TIPO", [
        {"OCL_Descripcion": "Tóner", "OCL_Cantidad": 5, "OCL_Precio_Unitario": "30", "CON_Consumible": cid,
         "OCL_Tipo": "CONSUMIBLE"}])
    oid, linea = orden["OCO_Orden"], orden["lineas"][0]["OCL_Linea"]
    r = await client.post(f"{BASE}/ordenes/{oid}/recibir", headers=h, json={
        "consumibles": [{"OCL_Linea": linea, "CON_Consumible": otro, "cantidad": 1}]})
    assert r.status_code == 400 and r.json()["detail"] == f"CONSUMABLE_DOES_NOT_MATCH_LINE:{linea}"
    r = await client.post(f"{BASE}/ordenes/{oid}/recibir", headers=h, json={"activos": [_activo(linea, "TIPO-S1", d)]})
    assert r.status_code == 400 and r.json()["detail"] == f"LINE_TYPE_MISMATCH:{linea}"


@pytest.mark.asyncio
async def test_cierre_con_faltante_y_sin_cancelar_con_recepciones(client, auth_headers, jefe_headers, domain_seed):
    h = auth_headers
    pid = await _proveedor(client, h)
    cid = await _consumible(client, h, "Mouse QA")
    orden = await _orden(client, h, pid, "OC-FALTA", [
        {"OCL_Descripcion": "Mouse", "OCL_Cantidad": 10, "OCL_Precio_Unitario": "5", "CON_Consumible": cid}])
    oid, linea = orden["OCO_Orden"], orden["lineas"][0]["OCL_Linea"]
    assert (await client.post(f"{BASE}/ordenes/{oid}/recibir", headers=h, json={
        "consumibles": [{"OCL_Linea": linea, "CON_Consumible": cid, "cantidad": 6}]})).json()["OCO_Estado"] == "PARCIAL"

    r = await client.patch(f"{BASE}/ordenes/{oid}/estado", headers=h, json={"OCO_Estado": "CANCELADA"})
    assert r.status_code == 409 and r.json()["detail"] == "ORDER_HAS_RECEIPTS_CLOSE_WITH_SHORTAGE"
    assert (await client.post(f"{BASE}/ordenes/{oid}/cerrar", headers=h, json={"motivo": "x"})).status_code == 422
    # Cerrar con faltante se solicita y lo aprueba el Jefe de TI.
    r = await client.post(f"{BASE}/ordenes/{oid}/cerrar", headers=h,
                          json={"motivo": "El proveedor descontinuó el modelo"})
    assert r.status_code == 202 and r.json()["APR_Tipo"] == "CIERRE_FALTANTE", r.text
    assert (await client.get(f"{BASE}/ordenes/{oid}", headers=h)).json()["OCO_Estado"] == "PARCIAL"
    r = await client.post(f"/api/v1/aprobaciones/{r.json()['APR_Aprobacion']}/aprobar", headers=jefe_headers, json={})
    assert r.status_code == 200, r.text
    r = await client.get(f"{BASE}/ordenes/{oid}", headers=h)
    assert r.json()["OCO_Estado"] == "CERRADA_CON_FALTANTE"
    assert r.json()["OCO_Motivo_Cierre"] == "El proveedor descontinuó el modelo"
    # Cerrada es final: no se recibe más.
    r = await client.post(f"{BASE}/ordenes/{oid}/recibir", headers=h, json={
        "consumibles": [{"OCL_Linea": linea, "CON_Consumible": cid, "cantidad": 1}]})
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_lineas_otro_se_reciben_por_conformidad(client, auth_headers, domain_seed):
    """Fletes o servicios (OTRO) no generan inventario pero sí completan la orden."""
    h, d = auth_headers, domain_seed
    pid = await _proveedor(client, h)
    orden = await _orden(client, h, pid, "OC-OTRO", [
        {"OCL_Descripcion": "Laptop", "OCL_Cantidad": 1, "OCL_Precio_Unitario": "900", "OCL_Tipo": "ACTIVO"},
        {"OCL_Descripcion": "Flete", "OCL_Cantidad": 1, "OCL_Precio_Unitario": "50", "OCL_Tipo": "OTRO"}])
    oid = orden["OCO_Orden"]
    la, lo = (ln["OCL_Linea"] for ln in orden["lineas"])

    # Un OTRO no se puede recibir como activo.
    r = await client.post(f"{BASE}/ordenes/{oid}/recibir", headers=h, json={"activos": [_activo(lo, "OTRO-S0", d)]})
    assert r.status_code == 400 and r.json()["detail"] == f"LINE_TYPE_MISMATCH:{lo}"

    r = await client.post(f"{BASE}/ordenes/{oid}/recibir", headers=h, json={"activos": [_activo(la, "OTRO-S1", d)]})
    assert r.json()["OCO_Estado"] == "PARCIAL"
    # La orden parcial aparece en la bandeja «Requiere su atención».
    atencion = (await client.get("/api/v1/inventario/atencion", headers=h)).json()
    parciales = next(i for i in atencion["items"] if i["clave"] == "ORDEN_PARCIAL")
    assert any(x["numero"] == "OC-OTRO" for x in parciales["items"])
    r = await client.post(f"{BASE}/ordenes/{oid}/recibir", headers=h, json={"otros": [{"OCL_Linea": lo, "cantidad": 1}]})
    assert r.status_code == 200, r.text
    assert r.json()["OCO_Estado"] == "RECIBIDA"

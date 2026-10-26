"""
Control interno (evaluación profesional del 2026-10-02):

  - Separación de funciones: bajas y sobrantes los aprueba el Jefe de TI (otra
    persona); mientras tanto el equipo queda congelado y el inventario intacto.
  - Costo promedio ponderado de consumibles, en una sola moneda de valoración.
  - Bitácora completa: saldo inicial, traslado de bodega y baja de licencias.
  - Kardex verificable: sello encadenado, sin TRUNCATE, alteraciones detectadas.
"""
import pytest
from sqlalchemy import text

from tests.conftest import crear_sede

BASE = "/api/v1"


async def _eventos(client, h, **params):
    r = await client.get(f"{BASE}/inventario/kardex", headers=h, params=params)
    assert r.status_code == 200, r.text
    return r.json()["items"]


# ---------------------------------------------------------------- aprobaciones
@pytest.mark.asyncio
async def test_baja_requiere_aprobacion_de_otra_persona(client, auth_headers, jefe_headers, domain_seed):
    h, d = auth_headers, domain_seed
    r = await client.post(f"{BASE}/core/activos/{d['act_1']}/baja", headers=h,
                          json={"motivo": "OBSOLESCENCIA", "destino": "RECICLAJE", "documento": "Acta 7"})
    assert r.status_code == 202, r.text
    solicitud = r.json()
    assert solicitud["APR_Estado"] == "PENDIENTE" and solicitud["APR_Tipo"] == "BAJA"

    # Nada se ejecuta todavía, y el equipo queda congelado.
    activo = (await client.get(f"{BASE}/core/activos/{d['act_1']}", headers=h)).json()
    assert activo["EOP_Estado_Operativo"] == d["eop_disp"]
    r = await client.post(f"{BASE}/trazabilidad/movimientos", headers=h, json={
        "ACT_Activo": d["act_1"], "PER_Persona": d["alice"], "ARE_Area": d["area"],
        "TMO_Tipo_Movimiento": d["tmo_asg"]})
    assert r.status_code == 409 and r.json()["detail"] == "ASSET_HAS_PENDING_DECOMMISSION"
    r = await client.post(f"{BASE}/core/activos/{d['act_1']}/baja", headers=h,
                          json={"motivo": "OBSOLESCENCIA", "destino": "RECICLAJE"})
    assert r.status_code == 409 and r.json()["detail"].startswith("DECOMMISSION_ALREADY_REQUESTED")

    # Aparece en la bandeja del tablero.
    atencion = (await client.get(f"{BASE}/inventario/atencion", headers=jefe_headers)).json()
    assert any(i["clave"] == "APROBACION_PENDIENTE" for i in atencion["items"])

    # Quien pide no aprueba (aunque sea SUPER_ADMIN).
    aid = solicitud["APR_Aprobacion"]
    r = await client.post(f"{BASE}/aprobaciones/{aid}/aprobar", headers=h, json={})
    assert r.status_code == 403 and r.json()["detail"] == "CANNOT_APPROVE_OWN_REQUEST"

    r = await client.post(f"{BASE}/aprobaciones/{aid}/aprobar", headers=jefe_headers,
                          json={"comentario": "Equipo sin soporte del fabricante"})
    assert r.status_code == 200, r.text
    assert r.json()["APR_Estado"] == "APROBADA" and r.json()["aprobador_nombre"] == "Jefa TI"
    activo = (await client.get(f"{BASE}/core/activos/{d['act_1']}", headers=h)).json()
    assert activo["EOP_Estado_Operativo"] == d["eop_baja"]
    baja = next(e for e in await _eventos(client, h, activo_id=d["act_1"]) if e["KDX_Tipo"] == "BAJA")
    assert baja["KDX_Detalle"]["aprobacion"] == aid
    assert baja["KDX_Detalle"]["solicitado_por"] != baja["KDX_Detalle"]["aprobado_por"]

    # Resuelta es final.
    r = await client.post(f"{BASE}/aprobaciones/{aid}/rechazar", headers=jefe_headers,
                          json={"comentario": "Tarde"})
    assert r.status_code == 409 and r.json()["detail"].startswith("APPROVAL_ALREADY_RESOLVED")


@pytest.mark.asyncio
async def test_rechazo_y_anulacion_de_solicitudes(client, auth_headers, jefe_headers, domain_seed):
    h, d = auth_headers, domain_seed
    aid = (await client.post(f"{BASE}/core/activos/{d['act_1']}/baja", headers=h,
                             json={"motivo": "PERDIDA", "destino": "OTRO", "observacion": "No aparece"})).json()["APR_Aprobacion"]
    # El rechazo exige motivo.
    r = await client.post(f"{BASE}/aprobaciones/{aid}/rechazar", headers=jefe_headers, json={"comentario": ""})
    assert r.status_code == 422
    r = await client.post(f"{BASE}/aprobaciones/{aid}/rechazar", headers=jefe_headers,
                          json={"comentario": "Apareció en la bodega del segundo piso"})
    assert r.status_code == 200 and r.json()["APR_Estado"] == "RECHAZADA"
    activo = (await client.get(f"{BASE}/core/activos/{d['act_1']}", headers=h)).json()
    assert activo["EOP_Estado_Operativo"] == d["eop_disp"]

    # El Jefe de TI tampoco se aprueba a sí mismo; solo quien pidió puede anular.
    aid = (await client.post(f"{BASE}/core/activos/{d['act_2']}/baja", headers=jefe_headers,
                             json={"motivo": "OBSOLESCENCIA", "destino": "DONACION"})).json()["APR_Aprobacion"]
    r = await client.post(f"{BASE}/aprobaciones/{aid}/aprobar", headers=jefe_headers, json={})
    assert r.status_code == 403
    r = await client.post(f"{BASE}/aprobaciones/{aid}/anular", headers=h)
    assert r.status_code == 403 and r.json()["detail"] == "ONLY_REQUESTER_CAN_CANCEL"
    r = await client.post(f"{BASE}/aprobaciones/{aid}/anular", headers=jefe_headers)
    assert r.status_code == 200 and r.json()["APR_Estado"] == "ANULADA"
    lista = (await client.get(f"{BASE}/aprobaciones", headers=h)).json()
    assert {x["APR_Estado"] for x in lista} == {"RECHAZADA", "ANULADA"}


@pytest.mark.asyncio
async def test_sobrante_queda_pendiente_hasta_su_aprobacion(client, auth_headers, jefe_headers):
    h = auth_headers
    pid = (await client.post(f"{BASE}/compras/proveedores", headers=h, json={"PRV_Nombre": "Prov CI"})).json()["PRV_Proveedor"]
    cid = (await client.post(f"{BASE}/consumibles", headers=h, json={
        "CON_Nombre": "Tóner CI", "CON_Unidad": "unidad", "CON_Stock_Actual": 0, "CON_Stock_Minimo": 0,
    })).json()["CON_Consumible"]
    orden = (await client.post(f"{BASE}/compras/ordenes", headers=h, json={
        "OCO_Numero": "OC-CI-1", "OCO_Fecha": "2026-10-01", "PRV_Proveedor": pid, "OCO_Moneda": "GTQ",
        "lineas": [{"OCL_Descripcion": "Tóner", "OCL_Cantidad": 10, "OCL_Precio_Unitario": "30",
                    "OCL_Tipo": "CONSUMIBLE", "CON_Consumible": cid}]})).json()
    oid, linea = orden["OCO_Orden"], orden["lineas"][0]["OCL_Linea"]
    r = await client.post(f"{BASE}/compras/ordenes/{oid}/recibir", headers=h, json={
        "documento": "Factura 99",
        "consumibles": [{"OCL_Linea": linea, "CON_Consumible": cid, "cantidad": 12}],
        "diferencias": [{"OCL_Linea": linea, "motivo": "El proveedor bonificó 2 unidades", "aceptar_sobrante": True}]})
    assert r.status_code == 200, r.text
    aid = r.json()["aprobacion_pendiente"]
    assert aid and r.json()["OCO_Estado"] == "BORRADOR" and r.json()["REC_Recepcion"] is None
    assert (await client.get(f"{BASE}/consumibles/{cid}", headers=h)).json()["CON_Stock_Actual"] == 0

    r = await client.post(f"{BASE}/aprobaciones/{aid}/aprobar", headers=jefe_headers, json={})
    assert r.status_code == 200, r.text
    assert (await client.get(f"{BASE}/consumibles/{cid}", headers=h)).json()["CON_Stock_Actual"] == 12
    det = (await client.get(f"{BASE}/compras/ordenes/{oid}", headers=h)).json()
    assert det["OCO_Estado"] == "RECIBIDA"
    rec = (await client.get(f"{BASE}/compras/ordenes/{oid}/recepciones", headers=h)).json()[0]
    assert f"solicitud #{aid}" in rec["REC_Observacion"]


# ---------------------------------------------------------------- valoración
@pytest.mark.asyncio
async def test_costo_promedio_ponderado(client, auth_headers):
    h = auth_headers
    cid = (await client.post(f"{BASE}/consumibles", headers=h, json={
        "CON_Nombre": "Papel CI", "CON_Unidad": "resma", "CON_Stock_Actual": 10, "CON_Stock_Minimo": 0,
        "CON_Costo_Unitario": "10", "CON_Moneda": "USD",
    })).json()["CON_Consumible"]
    # Saldo inicial en el kardex, valorizado.
    inicial = (await _eventos(client, h, consumible_id=cid))[0]
    assert inicial["KDX_Tipo"] == "CONSUMIBLE_ENTRADA" and inicial["KDX_Saldo"] == 10
    assert float(inicial["KDX_Saldo_Valor"]) == 100.0

    r = await client.post(f"{BASE}/consumibles/{cid}/entrada", headers=h,
                          json={"MOC_Cantidad": 10, "precio_unitario": "20", "moneda": "USD"})
    assert r.status_code == 201, r.text
    det = (await client.get(f"{BASE}/consumibles/{cid}", headers=h)).json()
    assert float(det["CON_Costo_Unitario"]) == 15.0 and det["CON_Moneda"] == "USD"

    r = await client.post(f"{BASE}/consumibles/{cid}/salida", headers=h, json={"MOC_Cantidad": 5})
    assert r.status_code == 201
    salida = (await _eventos(client, h, consumible_id=cid))[0]
    assert salida["KDX_Tipo"] == "CONSUMIBLE_SALIDA"
    assert float(salida["KDX_Costo"]) == 15.0 and float(salida["KDX_Saldo_Valor"]) == 225.0

    # No se promedian monedas distintas.
    r = await client.post(f"{BASE}/consumibles/{cid}/entrada", headers=h,
                          json={"MOC_Cantidad": 1, "precio_unitario": "100", "moneda": "GTQ"})
    assert r.status_code == 400 and r.json()["detail"] == "CURRENCY_DIFFERS_FROM_VALUATION:USD"
    assert (await client.get(f"{BASE}/consumibles/{cid}", headers=h)).json()["CON_Stock_Actual"] == 15


# ---------------------------------------------------------------- bitácora completa
@pytest.mark.asyncio
async def test_traslado_de_bodega_y_baja_de_licencia_en_kardex(client, auth_headers, software_seed, session):
    h, d = auth_headers, software_seed
    otra = await crear_sede(session, "Bodega Norte")
    await session.commit()
    cid = (await client.post(f"{BASE}/consumibles", headers=h, json={
        "CON_Nombre": "Cable CI", "CON_Unidad": "unidad", "CON_Stock_Actual": 4, "CON_Stock_Minimo": 0,
        "SED_Sede": d["sede"]})).json()["CON_Consumible"]
    r = await client.patch(f"{BASE}/consumibles/{cid}", headers=h, json={"SED_Sede": otra["sede"]})
    assert r.status_code == 200, r.text
    traslado = (await _eventos(client, h, consumible_id=cid))[0]
    assert traslado["KDX_Tipo"] == "CONSUMIBLE_TRASLADO" and traslado["KDX_Cantidad"] == 4
    assert traslado["KDX_Origen_Sede"] == d["sede"] and traslado["KDX_Destino_Sede"] == otra["sede"]

    r = await client.delete(f"{BASE}/soft/licencias/{d['lic']}", headers=h)
    assert r.status_code == 204, r.text
    tipos = [e["KDX_Tipo"] for e in await _eventos(client, h, licencia_id=d["lic"])]
    assert tipos[0] == "LICENCIA_BAJA"
    salidas = {e["KDX_Tipo"] for e in await _eventos(client, h, categoria="SALIDAS")}
    assert "LICENCIA_BAJA" in salidas


# ---------------------------------------------------------------- kardex verificable
@pytest.mark.asyncio
async def test_kardex_sellado_y_verificable(client, auth_headers, domain_seed, session):
    h, d = auth_headers, domain_seed
    for _ in range(2):
        await client.post(f"{BASE}/trazabilidad/movimientos", headers=h, json={
            "ACT_Activo": d["act_1"], "PER_Persona": d["alice"], "ARE_Area": d["area"],
            "TMO_Tipo_Movimiento": d["tmo_asg"]})
        await client.post(f"{BASE}/trazabilidad/devolucion", headers=h, json={"ACT_Activo": d["act_1"]})
    v = (await client.get(f"{BASE}/inventario/kardex/verificar", headers=h)).json()
    assert v["integro"] is True and v["total"] >= 4 and v["ultima_secuencia"] == v["total"]
    assert len(v["ultimo_hash"]) == 64

    # Vaciar la tabla está prohibido.
    with pytest.raises(Exception) as exc:
        await session.execute(text('TRUNCATE "INV_KARDEX"'))
    assert "no se puede vaciar" in str(exc.value)
    await session.rollback()

    # Alterar un evento (aun desactivando los disparadores) se detecta.
    await session.execute(text('ALTER TABLE "INV_KARDEX" DISABLE TRIGGER USER'))
    await session.execute(text(
        'UPDATE "INV_KARDEX" SET "KDX_Motivo" = \'alterado\' WHERE "KDX_Secuencia" = 2'))
    await session.execute(text('ALTER TABLE "INV_KARDEX" ENABLE TRIGGER USER'))
    await session.commit()
    v = (await client.get(f"{BASE}/inventario/kardex/verificar", headers=h)).json()
    assert v["integro"] is False
    assert v["problemas"][0] == {"evento": v["problemas"][0]["evento"], "secuencia": 2, "problema": "EVENTO_ALTERADO"}

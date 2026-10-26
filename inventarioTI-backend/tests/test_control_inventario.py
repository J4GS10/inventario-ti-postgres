"""
Control del inventario (sesión 16):

- Clases de activo: una impresora no es una laptop (se instala en un área, no
  lleva software ni RAM); los insumos no se registran como activos y se
  reclasifican a consumibles con aprobación.
- Motivo único y obligatorio en los movimientos.
- Aprobaciones completas: ajuste que baja existencias, cierre con faltante y
  cancelación de órdenes.
- Faltantes de la toma: investigación, localización y baja por pérdida.
- Cierre mensual para Contabilidad.
"""
from datetime import date

import pytest
from sqlalchemy import select

BASE = "/api/v1"


async def _estado_instalado(session):
    from app.models.catalogs import EstadoOperativo
    if not (await session.execute(select(EstadoOperativo).where(EstadoOperativo.EOP_Nombre == "Instalado"))).first():
        session.add(EstadoOperativo(EOP_Nombre="Instalado"))
        await session.commit()


async def _tipo(client, h, nombre: str, **extra) -> dict:
    r = await client.post(f"{BASE}/cat/tipos-activo", headers=h, json={"TAC_Nombre": nombre, **extra})
    assert r.status_code == 201, r.text
    return r.json()


async def _activo(client, h, d, tipo: int, serie: str, **extra) -> dict:
    r = await client.post(f"{BASE}/core/activos", headers=h, json={
        "ACT_Serie_Fabricante": serie, "ACT_Fecha_Compra": "2024-01-10", "MOD_Modelo": d["mod"],
        "TAC_Tipo_Activo": tipo, "EOP_Estado_Operativo": d["eop_bod"], "SED_Sede": d["sede"], **extra})
    assert r.status_code == 201, r.text
    return r.json()


async def _motivo(client, h, codigo: str) -> int:
    r = await client.get(f"{BASE}/catalogos/motivos-movimiento", headers=h)
    return next(m["id"] for m in r.json() if m["codigo"] == codigo)


async def _kardex(client, h, activo):
    return (await client.get(f"{BASE}/inventario/kardex", headers=h, params={"activo_id": activo})).json()["items"]


# ------------------------------------------------------------ clases de activo
@pytest.mark.asyncio
async def test_impresora_se_instala_en_un_area_y_no_se_asigna(client, auth_headers, software_seed, session):
    h, d = auth_headers, software_seed
    await _estado_instalado(session)
    tipo = await _tipo(client, h, "Impresora", TAC_Prefijo="IMP")
    assert tipo["TAC_Clase"] == "IMPRESION" and tipo["reglas"]["uso"] == "COMPARTIDO"
    # La plantilla de la clase pide tecnología de impresión y color.
    plantilla = (await client.get(f"{BASE}/catalogos/especificaciones-tipo/{tipo['TAC_Tipo_Activo']}", headers=h)).json()
    nombres = {e.get("nombre") or e.get("TES_Nombre") for e in plantilla}
    assert {"Tecnología de impresión", "Impresión a color"} <= nombres

    imp = await _activo(client, h, d, tipo["TAC_Tipo_Activo"], "IMP-SER-1")
    r = await client.post(f"{BASE}/trazabilidad/movimientos", headers=h, json={
        "ACT_Activo": imp["ACT_Activo"], "PER_Persona": d["alice"], "ARE_Area": d["area"],
        "TMO_Tipo_Movimiento": d["tmo_asg"], "MOV_Motivo": "Prueba"})
    assert r.status_code == 422 and r.json()["detail"] == "ASSET_IS_SHARED_INSTALL_IN_AREA"

    r = await client.post(f"{BASE}/trazabilidad/instalar-area", headers=h, json={
        "ACT_Activo": imp["ACT_Activo"], "ARE_Area": d["area"], "PER_Responsable": d["bob"],
        "MMO_Motivo": await _motivo(client, h, "INSTALACION_NUEVA")})
    assert r.status_code == 200, r.text
    assert r.json()["ACT_Instalado"] and r.json()["responsable"] == "Bob Test"
    detalle = (await client.get(f"{BASE}/core/activos/{imp['ACT_Activo']}", headers=h)).json()
    assert detalle["estado_operativo"]["EOP_Nombre"] == "Instalado" and detalle["ACT_Responsable"] == d["bob"]
    # No es custodia de Bob.
    asignaciones = (await client.get(f"{BASE}/trazabilidad/persona/{d['bob']}/asignaciones", headers=h)).json()
    assert asignaciones == []

    # Mantenimiento: al cerrar vuelve a «Instalado».
    r = await client.post(f"{BASE}/mantenimiento/", headers=h, json={
        "ACT_Activo": imp["ACT_Activo"], "PER_Persona_Solicita": d["bob"], "TMA_Tipo_Mantenimiento": d["tma_corr"],
        "MAN_Descripcion_Falla": "Atasco de papel", "MAN_Costo_Total": 0})
    assert r.status_code == 201, r.text
    r = await client.patch(f"{BASE}/mantenimiento/{r.json()['MAN_Mantenimiento']}/cerrar", headers=h,
                           json={"MAN_Costo_Total": 0})
    assert r.status_code == 200, r.text
    detalle = (await client.get(f"{BASE}/core/activos/{imp['ACT_Activo']}", headers=h)).json()
    assert detalle["estado_operativo"]["EOP_Nombre"] == "Instalado"

    r = await client.post(f"{BASE}/trazabilidad/retirar-area", headers=h, json={
        "ACT_Activo": imp["ACT_Activo"], "MMO_Motivo": await _motivo(client, h, "RETIRO_USO")})
    assert r.status_code == 200, r.text
    assert not r.json()["ACT_Instalado"] and r.json()["responsable"] is None
    tipos = [e["KDX_Tipo"] for e in await _kardex(client, h, imp["ACT_Activo"])]
    assert "INSTALACION_AREA" in tipos and "RETIRO_AREA" in tipos


@pytest.mark.asyncio
async def test_software_y_componentes_segun_la_clase(client, auth_headers, domain_seed, software_seed, session):
    h, d = auth_headers, domain_seed
    monitor = await _activo(client, h, d, d["tac_mon"], "MON-SER-9")
    lic = software_seed["lic"]
    r = await client.post(f"{BASE}/soft/instalaciones", headers=h, json={
        "ACT_Activo": monitor["ACT_Activo"], "LIC_Licencia": lic, "INS_Fecha_Instalacion": date.today().isoformat()})
    assert r.status_code == 422 and r.json()["detail"] == "ASSET_CLASS_NO_SOFTWARE"

    ram = await _tipo(client, h, "Memoria RAM", TAC_Prefijo="RAM")
    assert ram["TAC_Clase"] == "COMPONENTE"
    r = await client.put(f"{BASE}/cat/tipos-activo/{d['tac_lap']}/componentes", headers=h,
                         json={"componentes": [ram["TAC_Tipo_Activo"]]})
    assert r.status_code == 200, r.text
    modulo = await _activo(client, h, d, ram["TAC_Tipo_Activo"], "RAM-SER-1")
    # Un monitor no se instala dentro de una laptop; la RAM sí.
    otro_monitor = await _activo(client, h, d, d["tac_mon"], "MON-SER-10")
    r = await client.post(f"{BASE}/core/activos/{d['act_1']}/componentes", headers=h,
                          json={"ACT_Componente": otro_monitor["ACT_Activo"], "motivo": "Prueba"})
    assert r.status_code == 422 and r.json()["detail"] == "COMPONENT_TYPE_NOT_ALLOWED"
    r = await client.post(f"{BASE}/core/activos/{d['act_1']}/componentes", headers=h,
                          json={"ACT_Componente": modulo["ACT_Activo"], "motivo": "Ampliación de memoria"})
    assert r.status_code == 200, r.text
    # Un monitor no admite componentes.
    r = await client.post(f"{BASE}/core/activos/{monitor['ACT_Activo']}/componentes", headers=h,
                          json={"ACT_Componente": otro_monitor["ACT_Activo"], "motivo": "Prueba"})
    assert r.status_code == 422 and r.json()["detail"] == "ASSET_CLASS_NO_COMPONENTS"


@pytest.mark.asyncio
async def test_insumo_no_es_activo_y_se_reclasifica_con_aprobacion(client, auth_headers, jefe_headers, domain_seed):
    h, d = auth_headers, domain_seed
    tipo = await _tipo(client, h, "Spray limpiador", TAC_Prefijo="SPR", TAC_Clase="OTRO")
    for i in range(3):
        await _activo(client, h, d, tipo["TAC_Tipo_Activo"], f"SPR-{i}", ACT_Costo="10.00", ACT_Moneda="GTQ")
    r = await client.patch(f"{BASE}/cat/tipos-activo/{tipo['TAC_Tipo_Activo']}", headers=h, json={"TAC_Clase": "INSUMO"})
    assert r.status_code == 200 and r.json()["reglas"]["uso"] == "INSUMO"
    r = await client.post(f"{BASE}/core/activos", headers=h, json={
        "ACT_Serie_Fabricante": "SPR-9", "ACT_Fecha_Compra": "2024-01-10", "MOD_Modelo": d["mod"],
        "TAC_Tipo_Activo": tipo["TAC_Tipo_Activo"], "EOP_Estado_Operativo": d["eop_bod"], "SED_Sede": d["sede"]})
    assert r.status_code == 422 and r.json()["detail"] == "ASSET_TYPE_IS_SUPPLY"
    atencion = (await client.get(f"{BASE}/inventario/atencion", headers=h)).json()["items"]
    assert any(i["clave"] == "INSUMOS_POR_RECLASIFICAR" for i in atencion)

    vista = (await client.get(f"{BASE}/core/tipos-activo/{tipo['TAC_Tipo_Activo']}/reclasificar", headers=h)).json()
    assert vista["activos"] == 3 and vista["bloqueados"] == []
    r = await client.post(f"{BASE}/core/tipos-activo/{tipo['TAC_Tipo_Activo']}/reclasificar", headers=h, json={
        "SED_Sede": d["sede"], "nombre": "Spray limpiador", "motivo": "Es un insumo, se controla por cantidad"})
    assert r.status_code == 202 and r.json()["APR_Tipo"] == "RECLASIFICACION"
    r = await client.post(f"{BASE}/aprobaciones/{r.json()['APR_Aprobacion']}/aprobar", headers=jefe_headers, json={})
    assert r.status_code == 200, r.text

    consumibles = (await client.get(f"{BASE}/consumibles", headers=h)).json()
    spray = next(c for c in consumibles if c["CON_Nombre"] == "Spray limpiador")
    assert spray["CON_Stock_Actual"] == 3 and float(spray["CON_Costo_Unitario"]) == 10.0
    vista = (await client.get(f"{BASE}/core/tipos-activo/{tipo['TAC_Tipo_Activo']}/reclasificar", headers=h)).json()
    assert vista["activos"] == 0


# ------------------------------------------------------------ motivo único
@pytest.mark.asyncio
async def test_motivo_obligatorio_y_nota_unica(client, auth_headers, domain_seed, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "MOVIMIENTO_EXIGE_MOTIVO", True)
    h, d = auth_headers, domain_seed
    cuerpo = {"ACT_Activo": d["act_1"], "PER_Persona": d["alice"], "ARE_Area": d["area"],
              "TMO_Tipo_Movimiento": d["tmo_asg"]}
    r = await client.post(f"{BASE}/trazabilidad/movimientos", headers=h, json=cuerpo)
    assert r.status_code == 422 and r.json()["detail"] == "MOVEMENT_REASON_REQUIRED"
    r = await client.post(f"{BASE}/trazabilidad/movimientos", headers=h, json={
        **cuerpo, "MMO_Motivo": await _motivo(client, h, "INGRESO_PERSONAL"), "MOV_Observacion": "Puesto de ventas"})
    assert r.status_code in (200, 201), r.text
    assert r.json()["MOV_Motivo"] == "Ingreso de personal: Puesto de ventas"
    assert r.json()["MOV_Motivo_Detalle"] == "Puesto de ventas"


# ------------------------------------------------------------ aprobaciones
@pytest.mark.asyncio
async def test_ajuste_que_baja_existencias_requiere_aprobacion(client, auth_headers, jefe_headers, domain_seed):
    h = auth_headers
    r = await client.post(f"{BASE}/consumibles", headers=h, json={
        "CON_Nombre": "Tóner 26A", "CON_Unidad": "unidad", "CON_Stock_Actual": 10, "SED_Sede": domain_seed["sede"]})
    assert r.status_code == 201, r.text
    cid = r.json()["CON_Consumible"]
    r = await client.post(f"{BASE}/consumibles/{cid}/ajuste", headers=h,
                          json={"CON_Stock_Real": 12, "MOC_Motivo": "Conteo de cierre"})
    assert r.status_code == 201 and r.json()["MOC_Stock_Resultante"] == 12
    r = await client.post(f"{BASE}/consumibles/{cid}/ajuste", headers=h,
                          json={"CON_Stock_Real": 8, "MOC_Motivo": "Conteo de cierre"})
    assert r.status_code == 202 and r.json()["APR_Tipo"] == "AJUSTE_CONSUMIBLE"
    aid = r.json()["APR_Aprobacion"]
    assert (await client.get(f"{BASE}/consumibles/{cid}", headers=h)).json()["CON_Stock_Actual"] == 12
    r = await client.post(f"{BASE}/aprobaciones/{aid}/aprobar", headers=jefe_headers, json={})
    assert r.status_code == 200, r.text
    assert (await client.get(f"{BASE}/consumibles/{cid}", headers=h)).json()["CON_Stock_Actual"] == 8

    # Si la existencia cambió mientras esperaba, no se aplica a ciegas.
    r = await client.post(f"{BASE}/consumibles/{cid}/ajuste", headers=h,
                          json={"CON_Stock_Real": 5, "MOC_Motivo": "Conteo de control"})
    aid = r.json()["APR_Aprobacion"]
    await client.post(f"{BASE}/consumibles/{cid}/salida", headers=h, json={"MOC_Cantidad": 1, "MOC_Motivo": "Uso"})
    r = await client.post(f"{BASE}/aprobaciones/{aid}/aprobar", headers=jefe_headers, json={})
    assert r.status_code == 409 and r.json()["detail"] == "STOCK_CHANGED_SINCE_REQUEST"


@pytest.mark.asyncio
async def test_cancelar_orden_requiere_aprobacion(client, auth_headers, jefe_headers, domain_seed):
    h = auth_headers
    prv = (await client.post(f"{BASE}/compras/proveedores", headers=h, json={"PRV_Nombre": "Proveedor C1"})).json()
    orden = (await client.post(f"{BASE}/compras/ordenes", headers=h, json={
        "OCO_Numero": "OC-C1", "OCO_Fecha": date.today().isoformat(), "PRV_Proveedor": prv["PRV_Proveedor"],
        "SED_Sede": domain_seed["sede"],
        "lineas": [{"OCL_Descripcion": "Mouse", "OCL_Cantidad": 2, "OCL_Precio_Unitario": "50"}]})).json()
    r = await client.patch(f"{BASE}/compras/ordenes/{orden['OCO_Orden']}/estado", headers=h,
                           json={"OCO_Estado": "CANCELADA", "motivo": "El proveedor no tiene existencias"})
    assert r.status_code == 202 and r.json()["APR_Tipo"] == "CANCELACION_ORDEN"
    assert (await client.get(f"{BASE}/compras/ordenes/{orden['OCO_Orden']}", headers=h)).json()["OCO_Estado"] == "BORRADOR"
    r = await client.post(f"{BASE}/aprobaciones/{r.json()['APR_Aprobacion']}/aprobar", headers=jefe_headers, json={})
    assert r.status_code == 200, r.text
    assert (await client.get(f"{BASE}/compras/ordenes/{orden['OCO_Orden']}", headers=h)).json()["OCO_Estado"] == "CANCELADA"


# ------------------------------------------------------------ faltantes
@pytest.mark.asyncio
async def test_faltantes_de_la_toma_se_investigan_y_resuelven(client, auth_headers, jefe_headers, domain_seed):
    h, d = auth_headers, domain_seed
    a1 = await _activo(client, h, d, d["tac_lap"], "FAL-SER-1")
    a2 = await _activo(client, h, d, d["tac_lap"], "FAL-SER-2")
    toma = (await client.post(f"{BASE}/inventario/tomas", headers=h, json={"SED_Sede": d["sede"]})).json()
    r = await client.post(f"{BASE}/inventario/tomas/{toma['TOM_Toma']}/cerrar", headers=h)
    assert r.status_code == 200, r.text
    faltantes = (await client.get(f"{BASE}/inventario/faltantes", headers=h, params={"abiertos": True})).json()
    assert {f["codigo"] for f in faltantes} == {a1["ACT_Codigo_Interno"], a2["ACT_Codigo_Interno"]}
    lap1 = next(f for f in faltantes if f["codigo"] == a1["ACT_Codigo_Interno"])
    lap2 = next(f for f in faltantes if f["codigo"] == a2["ACT_Codigo_Interno"])
    assert any(i["clave"] == "FALTANTE_PENDIENTE" for i in
               (await client.get(f"{BASE}/inventario/atencion", headers=h)).json()["items"])

    r = await client.post(f"{BASE}/inventario/faltantes/{lap1['FAL_Faltante']}/seguimiento", headers=h,
                          json={"nota": "Se consultó con recepción"})
    assert r.status_code == 200 and r.json()["FAL_Estado"] == "EN_INVESTIGACION"
    r = await client.post(f"{BASE}/inventario/faltantes/{lap1['FAL_Faltante']}/localizado", headers=h,
                          json={"nota": "Estaba en la sala de juntas", "ARE_Area": d["area"]})
    assert r.status_code == 200 and r.json()["FAL_Estado"] == "LOCALIZADO"
    assert len(r.json()["FAL_Seguimiento"]) == 2

    r = await client.post(f"{BASE}/inventario/faltantes/{lap2['FAL_Faltante']}/baja", headers=h,
                          json={"motivo": "PERDIDA", "destino": "NO_RECUPERADO", "observacion": "No apareció"})
    assert r.status_code == 202 and r.json()["FAL_Estado"] == "BAJA_SOLICITADA"
    r = await client.post(f"{BASE}/aprobaciones/{r.json()['APR_Aprobacion']}/aprobar", headers=jefe_headers, json={})
    assert r.status_code == 200, r.text
    resueltos = (await client.get(f"{BASE}/inventario/faltantes", headers=h)).json()
    assert next(f for f in resueltos if f["codigo"] == a2["ACT_Codigo_Interno"])["FAL_Estado"] == "DADO_DE_BAJA"


@pytest.mark.asyncio
async def test_faltante_leido_en_otra_toma_queda_localizado(client, auth_headers, domain_seed):
    h, d = auth_headers, domain_seed
    a1 = await _activo(client, h, d, d["tac_lap"], "FAL-SER-3")
    t1 = (await client.post(f"{BASE}/inventario/tomas", headers=h, json={"SED_Sede": d["sede"]})).json()
    await client.post(f"{BASE}/inventario/tomas/{t1['TOM_Toma']}/cerrar", headers=h)
    t2 = (await client.post(f"{BASE}/inventario/tomas", headers=h, json={"SED_Sede": d["sede"]})).json()
    r = await client.post(f"{BASE}/inventario/tomas/{t2['TOM_Toma']}/lecturas", headers=h,
                          json={"codigo": a1["ACT_Codigo_Interno"]})
    assert r.status_code == 201, r.text
    faltantes = (await client.get(f"{BASE}/inventario/faltantes", headers=h)).json()
    assert next(f for f in faltantes if f["codigo"] == a1["ACT_Codigo_Interno"])["FAL_Estado"] == "LOCALIZADO"


# ------------------------------------------------------------ consumibles por equipo
@pytest.mark.asyncio
async def test_toner_instalado_en_la_impresora(client, auth_headers, domain_seed, session):
    h, d = auth_headers, domain_seed
    await _estado_instalado(session)
    tipo = await _tipo(client, h, "Impresora", TAC_Prefijo="IMP")
    imp = await _activo(client, h, d, tipo["TAC_Tipo_Activo"], "IMP-SER-2")
    toner = (await client.post(f"{BASE}/consumibles", headers=h, json={
        "CON_Nombre": "Tóner 30A", "CON_Unidad": "unidad", "CON_Stock_Actual": 4, "SED_Sede": d["sede"]})).json()
    r = await client.put(f"{BASE}/consumibles/{toner['CON_Consumible']}/compatibles", headers=h,
                         json={"modelos": [d["mod"]]})
    assert r.status_code == 200 and r.json()[0]["MOD_Modelo"] == d["mod"]
    r = await client.post(f"{BASE}/consumibles/{toner['CON_Consumible']}/salida", headers=h, json={
        "MOC_Cantidad": 1, "MOC_Motivo": "Cambio de tóner", "ACT_Activo": imp["ACT_Activo"]})
    assert r.status_code == 201 and r.json()["ACT_Activo"] == imp["ACT_Activo"]
    consumo = (await client.get(f"{BASE}/core/activos/{imp['ACT_Activo']}/consumibles", headers=h)).json()
    assert consumo["historial"][0]["consumible"] == "Tóner 30A"
    assert consumo["compatibles"][0]["CON_Nombre"] == "Tóner 30A"


# ------------------------------------------------------------ cierre mensual
@pytest.mark.asyncio
async def test_cierre_mensual_por_sede_y_moneda(client, auth_headers, domain_seed):
    h, d = auth_headers, domain_seed
    hoy = date.today()
    await _activo(client, h, d, d["tac_lap"], "LAP-CIERRE-1", ACT_Fecha_Compra=hoy.isoformat(),
                  ACT_Costo="1000.00", ACT_Moneda="GTQ")
    await client.post(f"{BASE}/consumibles", headers=h, json={
        "CON_Nombre": "Cable HDMI", "CON_Unidad": "unidad", "CON_Stock_Actual": 5, "CON_Costo_Unitario": "20",
        "CON_Moneda": "GTQ", "SED_Sede": d["sede"]})
    r = await client.get(f"{BASE}/inventario/cierre-mensual", headers=h, params={"anio": hoy.year, "mes": hoy.month})
    assert r.status_code == 200, r.text
    datos = r.json()
    gtq = [a for a in datos["activos"] if a["moneda"] == "GTQ" and a["SED_Sede"] == d["sede"]]
    assert gtq[0]["altas_cantidad"] == 1 and float(gtq[0]["altas_valor"]) == 1000.0
    assert gtq[0]["final_cantidad"] == 1
    assert any(a["serie"] == "LAP-CIERRE-1" for a in datos["altas"])
    cable = next(c for c in datos["consumibles"] if c["nombre"] == "Cable HDMI")
    assert cable["final_cantidad"] == 5 and float(cable["final_valor"]) == 100.0
    r = await client.get(f"{BASE}/inventario/cierre-mensual.xlsx", headers=h,
                         params={"anio": hoy.year, "mes": hoy.month, "lang": "en"})
    assert r.status_code == 200 and r.content[:2] == b"PK"

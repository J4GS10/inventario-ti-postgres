"""Los formularios operativos usan los catálogos: motivos, bajas, fallas, consumibles, adjuntos, kits."""
import pytest
from sqlalchemy import select

from app.models.traceability import Movimiento

API = "/api/v1"
pytestmark = pytest.mark.asyncio


async def _item(client, h, clave: str, codigo: str) -> dict:
    items = (await client.get(f"{API}/catalogos/{clave}", headers=h)).json()
    return next(i for i in items if i["codigo"] == codigo)


async def _asignar(client, h, seed, activo: str, **extra):
    return await client.post(f"{API}/trazabilidad/movimientos", headers=h, json={
        "ACT_Activo": seed[activo], "PER_Persona": seed["alice"], "ARE_Area": seed["area"],
        "TMO_Tipo_Movimiento": seed["tmo_asg"], **extra,
    })


async def test_motivo_del_catalogo_en_asignacion_y_devolucion(client, auth_headers, domain_seed, session):
    ingreso = await _item(client, auth_headers, "motivos-movimiento", "INGRESO_PERSONAL")
    r = await _asignar(client, auth_headers, domain_seed, "act_1", MMO_Motivo=ingreso["id"])
    assert r.status_code in (200, 201), r.text
    mov = r.json()
    assert mov["MMO_Motivo"] == ingreso["id"] and mov["MOV_Motivo"] == "Ingreso de personal"

    # Un motivo que no aplica a devoluciones se rechaza.
    r = await client.post(f"{API}/trazabilidad/devolucion", headers=auth_headers,
                          json={"ACT_Activo": domain_seed["act_1"], "MMO_Motivo": ingreso["id"]})
    assert r.status_code == 422 and r.json()["detail"] == "MOVEMENT_REASON_NOT_APPLICABLE"
    # «Otro» exige detalle.
    otro = await _item(client, auth_headers, "motivos-movimiento", "OTRO")
    r = await client.post(f"{API}/trazabilidad/devolucion", headers=auth_headers,
                          json={"ACT_Activo": domain_seed["act_1"], "MMO_Motivo": otro["id"]})
    assert r.status_code == 422 and r.json()["detail"] == "MOVEMENT_REASON_DETAIL_REQUIRED"

    renuncia = await _item(client, auth_headers, "motivos-movimiento", "RENUNCIA")
    r = await client.post(f"{API}/trazabilidad/devolucion", headers=auth_headers, json={
        "ACT_Activo": domain_seed["act_1"], "MMO_Motivo": renuncia["id"], "MOV_Motivo_Detalle": "Último día 30/09",
    })
    assert r.status_code == 200, r.text
    fila = (await session.execute(select(Movimiento).where(
        Movimiento.MOV_Movimiento == mov["MOV_Movimiento"]))).scalar_one()
    assert fila.MMO_Motivo == renuncia["id"]
    assert fila.MOV_Motivo == "Salida de la persona: Último día 30/09"


async def test_texto_libre_sigue_aceptandose(client, auth_headers, domain_seed):
    r = await _asignar(client, auth_headers, domain_seed, "act_1", MOV_Motivo="Proyecto especial")
    assert r.status_code in (200, 201), r.text
    assert r.json()["MOV_Motivo"] == "Proyecto especial" and r.json()["MMO_Motivo"] is None


async def test_motivo_inactivo_se_rechaza(client, auth_headers, domain_seed):
    nuevo = (await client.post(f"{API}/catalogos/motivos-movimiento", headers=auth_headers,
                               json={"nombre": "Campaña 2026"})).json()
    await client.patch(f"{API}/catalogos/motivos-movimiento/{nuevo['id']}", headers=auth_headers, json={"activo": False})
    r = await _asignar(client, auth_headers, domain_seed, "act_1", MMO_Motivo=nuevo["id"])
    assert r.status_code == 422 and r.json()["detail"] == "MOVEMENT_REASON_NOT_FOUND"


async def test_entrega_en_lote_con_motivo(client, auth_headers, domain_seed):
    ingreso = await _item(client, auth_headers, "motivos-movimiento", "INGRESO_PERSONAL")
    r = await client.post(f"{API}/trazabilidad/entrega-lote", headers=auth_headers, json={
        "PER_Persona": domain_seed["bob"], "ARE_Area": domain_seed["area"],
        "activos": [domain_seed["act_1"], domain_seed["act_2"]], "MMO_Motivo": ingreso["id"],
    })
    assert r.status_code in (200, 201), r.text
    assert r.json()["total"] == 2


async def test_baja_valida_contra_el_catalogo(client, auth_headers, domain_seed):
    activo = domain_seed["act_2"]
    r = await client.post(f"{API}/core/activos/{activo}/baja", headers=auth_headers,
                          json={"motivo": "INVENTADO", "destino": "RECICLAJE"})
    assert r.status_code == 422 and r.json()["detail"] == "DECOMMISSION_REASON_NOT_FOUND"
    r = await client.post(f"{API}/core/activos/{activo}/baja", headers=auth_headers,
                          json={"motivo": "OTRO", "destino": "RECICLAJE"})
    assert r.status_code == 422 and r.json()["detail"] == "OBSERVATION_REQUIRED_FOR_OTHER"
    # Un motivo creado por el usuario se usa por su código.
    nuevo = (await client.post(f"{API}/catalogos/motivos-baja", headers=auth_headers,
                               json={"nombre": "Siniestro"})).json()
    r = await client.post(f"{API}/core/activos/{activo}/baja", headers=auth_headers,
                          json={"motivo": nuevo["codigo"], "destino": "RECICLAJE"})
    assert r.status_code == 202, r.text


async def test_mantenimiento_con_falla_y_accion(client, auth_headers, software_seed):
    falla = await _item(client, auth_headers, "tipos-falla", "PANTALLA")
    accion = await _item(client, auth_headers, "acciones-mantenimiento", "CAMBIO_PIEZA")
    otro = await _item(client, auth_headers, "tipos-falla", "OTRO")
    base = {"ACT_Activo": software_seed["act_1"], "PER_Persona_Solicita": software_seed["alice"],
            "TMA_Tipo_Mantenimiento": software_seed["tma_corr"]}

    r = await client.post(f"{API}/mantenimiento/", headers=auth_headers, json={**base, "TFA_Tipo_Falla": otro["id"]})
    assert r.status_code == 422 and r.json()["detail"] == "FAULT_DESCRIPTION_REQUIRED"
    r = await client.post(f"{API}/mantenimiento/", headers=auth_headers, json={
        **base, "TFA_Tipo_Falla": falla["id"], "MAN_Descripcion_Falla": "Línea vertical",
        "detalles": [{"AMA_Accion": accion["id"]}],
    })
    assert r.status_code in (200, 201), r.text
    ticket = r.json()
    assert ticket["TFA_Tipo_Falla"] == falla["id"] and ticket["MAN_Descripcion_Falla"] == "Pantalla: Línea vertical"
    r = await client.get(f"{API}/mantenimiento/{ticket['MAN_Mantenimiento']}/detalles", headers=auth_headers)
    assert r.json()[0]["DMA_Accion_Realizada"] == "Cambio de pieza" and r.json()[0]["AMA_Accion"] == accion["id"]


async def test_consumible_con_categoria_y_unidad(client, auth_headers):
    cat = await _item(client, auth_headers, "categorias-consumible", "CABLES")
    caja = await _item(client, auth_headers, "unidades-medida", "CAJA")
    r = await client.post(f"{API}/consumibles", headers=auth_headers, json={
        "CON_Nombre": "Cable HDMI 2 m", "CCO_Categoria": cat["id"], "UME_Unidad": caja["id"],
    })
    assert r.status_code in (200, 201), r.text
    assert r.json()["CON_Categoria"] == "Cables" and r.json()["CON_Unidad"] == "caja"
    assert r.json()["CCO_Categoria"] == cat["id"]


async def test_adjunto_usa_codigos_del_catalogo(client, auth_headers, domain_seed):
    nuevo = (await client.post(f"{API}/catalogos/tipos-evidencia", headers=auth_headers,
                               json={"nombre": "Garantía"})).json()
    assert nuevo["codigo"] == "GARANTIA"
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    r = await client.post(f"{API}/adjuntos/activos/{domain_seed['act_1']}", headers=auth_headers,
                          files={"file": ("g.png", png, "image/png")}, data={"categoria": "GARANTIA"})
    assert r.status_code in (200, 201), r.text
    assert r.json()["ADJ_Categoria"] == "GARANTIA"
    r = await client.post(f"{API}/adjuntos/activos/{domain_seed['act_1']}", headers=auth_headers,
                          files={"file": ("g.png", png, "image/png")}, data={"categoria": "reporte"})
    assert r.status_code in (200, 201), r.text


async def test_especificaciones_por_tipo_y_kit_estandar(client, auth_headers, domain_seed, session):
    from app.models.catalogs import TipoEspecificacion
    ram = TipoEspecificacion(TES_Nombre="RAM", TES_Unidad_Medida="GB")
    session.add(ram)
    await session.commit()
    tipo = domain_seed["tac_lap"]
    r = await client.put(f"{API}/catalogos/especificaciones-tipo/{tipo}", headers=auth_headers, json={
        "items": [{"TES_Tipo_Especificacion": ram.TES_Tipo_Especificacion, "obligatoria": True,
                   "valores": ["8", "16", " "]}],
    })
    assert r.status_code == 200, r.text
    assert r.json() == [{"TES_Tipo_Especificacion": ram.TES_Tipo_Especificacion, "nombre": "RAM", "unidad": "GB",
                         "obligatoria": True, "valores": ["8", "16"], "orden": 10}]
    todas = (await client.get(f"{API}/catalogos/especificaciones-tipo", headers=auth_headers)).json()
    assert str(tipo) in todas

    from app.models.organization import Persona
    alice = await session.get(Persona, __import__("uuid").UUID(domain_seed["alice"]))
    r = await client.post(f"{API}/catalogos/kits", headers=auth_headers, json={
        "KIT_Nombre": "Ingeniería", "CAR_Cargo": alice.CAR_Cargo,
        "items": [{"TAC_Tipo_Activo": domain_seed["tac_lap"]}, {"TAC_Tipo_Activo": domain_seed["tac_mon"], "KIT_Cantidad": 2}],
    })
    assert r.status_code == 201, r.text
    r = await client.post(f"{API}/catalogos/kits", headers=auth_headers, json={
        "KIT_Nombre": "Otro", "CAR_Cargo": alice.CAR_Cargo, "items": [{"TAC_Tipo_Activo": domain_seed["tac_lap"]}],
    })
    assert r.status_code == 409 and r.json()["detail"] == "STANDARD_KIT_ALREADY_EXISTS"

    kit = (await client.get(f"{API}/productividad/kit-sugerido/{domain_seed['alice']}", headers=auth_headers)).json()
    assert kit["origen"] == "kit" and kit["kit"] == "Ingeniería"
    assert {t["TAC_Tipo_Activo"]: t["cantidad"] for t in kit["tipos"]} == {domain_seed["tac_lap"]: 1,
                                                                            domain_seed["tac_mon"]: 2}


async def test_semilla_idempotente_y_respeta_lo_existente(session):
    from app.db.catalogos_semilla import sembrar
    conn = await session.connection()
    assert await conn.run_sync(sembrar) == 0
    await session.rollback()


async def test_calidad_marcas_similares_y_especificaciones(client, auth_headers, domain_seed, session):
    from app.models.catalogs import Marca, TipoEspecificacion
    session.add_all([Marca(MAR_Nombre="Kingston"), Marca(MAR_Nombre="Kingstom"), Marca(MAR_Nombre="LOGITECH"),
                     Marca(MAR_Nombre="Logitech.")])
    ram = TipoEspecificacion(TES_Nombre="RAM")
    session.add(ram)
    await session.commit()
    await client.put(f"{API}/catalogos/especificaciones-tipo/{domain_seed['tac_lap']}", headers=auth_headers,
                     json={"items": [{"TES_Tipo_Especificacion": ram.TES_Tipo_Especificacion, "obligatoria": True}]})
    r = (await client.get(f"{API}/productividad/calidad-datos", headers=auth_headers)).json()
    v = {x["clave"]: x for x in r["verificaciones"]}
    titulos = {i["titulo"] for i in v["marcas_similares"]["items"]}
    assert any("Kingstom" in t and "Kingston" in t for t in titulos)
    assert any("LOGITECH" in t and "Logitech." in t for t in titulos)
    assert v["especificaciones_faltantes"]["total"] == 2  # LAP-001 y LAP-002 sin RAM
    assert v["especificaciones_faltantes"]["items"][0]["detalle"] == "RAM"


async def test_baja_aprobada_aunque_el_motivo_se_desactive(client, auth_headers, jefe_headers, domain_seed):
    nuevo = (await client.post(f"{API}/catalogos/motivos-baja", headers=auth_headers, json={"nombre": "Siniestro"})).json()
    r = await client.post(f"{API}/core/activos/{domain_seed['act_2']}/baja", headers=auth_headers,
                          json={"motivo": nuevo["codigo"], "destino": "RECICLAJE"})
    assert r.status_code == 202, r.text
    await client.patch(f"{API}/catalogos/motivos-baja/{nuevo['id']}", headers=auth_headers, json={"activo": False})
    r = await client.post(f"{API}/aprobaciones/{r.json()['APR_Aprobacion']}/aprobar", headers=jefe_headers, json={})
    assert r.status_code == 200, r.text
    # Una solicitud nueva con el motivo inactivo sí se rechaza.
    r = await client.post(f"{API}/core/activos/{domain_seed['act_1']}/baja", headers=auth_headers,
                          json={"motivo": nuevo["codigo"], "destino": "RECICLAJE"})
    assert r.status_code == 422 and r.json()["detail"] == "DECOMMISSION_REASON_NOT_FOUND"


async def test_resumen_por_motivo_falla_accion_y_baja(client, auth_headers, software_seed):
    h = auth_headers
    ingreso = await _item(client, h, "motivos-movimiento", "INGRESO_PERSONAL")
    await _asignar(client, h, software_seed, "act_1", MMO_Motivo=ingreso["id"])
    falla = await _item(client, h, "tipos-falla", "PANTALLA")
    accion = await _item(client, h, "acciones-mantenimiento", "LIMPIEZA")
    r = await client.post(f"{API}/mantenimiento/", headers=h, json={
        "ACT_Activo": software_seed["act_2"], "PER_Persona_Solicita": software_seed["alice"],
        "TMA_Tipo_Mantenimiento": software_seed["tma_corr"], "TFA_Tipo_Falla": falla["id"],
        "MAN_Costo_Total": "150", "detalles": [{"AMA_Accion": accion["id"]}]})
    assert r.status_code in (200, 201), r.text
    r = await client.get(f"{API}/inventario/resumen-motivos", headers=h)
    assert r.status_code == 200, r.text
    d = r.json()
    assert {"codigo": "INGRESO_PERSONAL", "nombre": "Ingreso de personal", "tipo": "Asignación", "total": 1} in d["movimientos"]
    assert d["fallas"][0]["codigo"] == "PANTALLA" and d["fallas"][0]["costo_por_moneda"] == {"GTQ": "150.00"}
    assert d["acciones"] == [{"codigo": "LIMPIEZA", "nombre": "Limpieza", "total": 1}]

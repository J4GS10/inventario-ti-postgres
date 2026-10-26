"""Ficha 360 de la persona: equipos, licencias, consumibles e historial en una sola consulta."""
import pytest

from app.models.software import Software

pytestmark = pytest.mark.asyncio


async def test_ficha_360(client, auth_headers, domain_seed, session):
    h = auth_headers
    r = await client.post("/api/v1/trazabilidad/movimientos", headers=h, json={
        "ACT_Activo": domain_seed["act_1"], "PER_Persona": domain_seed["alice"], "ARE_Area": domain_seed["area"],
        "TMO_Tipo_Movimiento": domain_seed["tmo_asg"]})
    assert r.status_code == 201, r.text

    sw = Software(SOF_Nombre="Office QA", SOF_Fabricante="Microsoft")
    session.add(sw)
    await session.commit()
    cats = {}
    for clave, codigo in (("modelos-comerciales", "SUSCRIPCION"), ("metodos-activacion", "CUENTA"), ("formas-conteo", "PERSONA")):
        items = (await client.get(f"/api/v1/catalogos/{clave}", headers=h)).json()
        cats[clave] = next(i["id"] for i in items if i["codigo"] == codigo)
    lic = (await client.post("/api/v1/soft/licencias", headers=h, json={
        "SOF_Software": sw.SOF_Software, "LIC_Cantidad_Total": 1, "MCO_Modelo_Comercial": cats["modelos-comerciales"],
        "MAC_Metodo_Activacion": cats["metodos-activacion"], "FCO_Forma_Conteo": cats["formas-conteo"],
        "LIC_Credenciales": [{"usuario": "alice@lombardi.test", "secreto": "x"}]})).json()
    r = await client.post("/api/v1/soft/instalaciones", headers=h, json={"LIC_Licencia": lic["LIC_Licencia"],
                                                                        "PER_Persona": domain_seed["alice"]})
    assert r.status_code == 201, r.text

    con = (await client.post("/api/v1/consumibles", headers=h, json={
        "CON_Nombre": "Mouse pad", "CON_Stock_Actual": 5, "SED_Sede": domain_seed["sede"]})).json()
    r = await client.post(f"/api/v1/consumibles/{con['CON_Consumible']}/salida", headers=h,
                          json={"MOC_Cantidad": 1, "PER_Persona": domain_seed["alice"]})
    assert r.status_code == 201, r.text

    f = (await client.get(f"/api/v1/productividad/personas/{domain_seed['alice']}/ficha", headers=h)).json()
    assert f["persona"]["nombre"] == "Alice Test" and f["persona"]["departamento"] == "Tecnología"
    assert [e["codigo"] for e in f["equipos"]] == ["LAP-001"]
    assert f["licencias"][0]["software"] == "Office QA" and f["licencias"][0]["cuenta"] == "alice@lombardi.test"
    assert f["consumibles"][0]["nombre"] == "Mouse pad"
    tipos = {e["tipo"] for e in f["historial"]}
    assert {"ASIGNACION", "LICENCIA_ASIGNADA"} <= tipos

    r = await client.get("/api/v1/productividad/personas/00000000-0000-0000-0000-000000000000/ficha", headers=h)
    assert r.status_code == 404

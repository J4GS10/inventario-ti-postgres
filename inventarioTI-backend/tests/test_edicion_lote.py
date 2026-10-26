"""Edición en lote de activos (bloque C §2): aplicar, deshacer, todo o nada y especificaciones."""
import pytest
from sqlalchemy import func, select

from app.models.catalogs import TipoEspecificacion
from app.models.core import Activo, Especificacion
from app.models.kardex import Kardex
from app.models.procurement import Proveedor

API = "/api/v1/core/activos"
pytestmark = pytest.mark.asyncio


async def test_editar_y_deshacer(client, auth_headers, domain_seed, session):
    prv = Proveedor(PRV_Nombre="Proveedor QA")
    session.add(prv)
    await session.commit()
    ids = [domain_seed["act_1"], domain_seed["act_2"]]
    r = await client.patch(f"{API}/lote", headers=auth_headers, json={"activos": ids, "cambios": {
        "ACT_Costo": "8500.00", "ACT_Moneda": "USD", "ACT_Fin_Garantia": "2027-01-31",
        "PRV_Proveedor": prv.PRV_Proveedor, "ACT_Factura": "F-001"}})
    assert r.status_code == 200, r.text
    resultado = r.json()
    assert resultado["total"] == 2
    assert resultado["anteriores"][0]["cambios"]["ACT_Costo"] is None

    session.expire_all()
    a1 = await session.get(Activo, __import__("uuid").UUID(ids[0]))
    assert str(a1.ACT_Costo) == "8500.00" and a1.ACT_Moneda == "USD" and a1.ACT_Factura == "F-001"
    eventos = (await session.execute(select(func.count()).select_from(Kardex).where(
        Kardex.KDX_Tipo == "CAMBIO_ESTADO", Kardex.KDX_Motivo == "Edición en lote"))).scalar_one()
    assert eventos == 2

    # Deshacer: cada activo vuelve a sus valores anteriores.
    r = await client.post(f"{API}/lote/restaurar", headers=auth_headers, json={"items": resultado["anteriores"]})
    assert r.status_code == 200, r.text
    session.expire_all()
    a1 = await session.get(Activo, __import__("uuid").UUID(ids[0]))
    assert a1.ACT_Costo is None and a1.ACT_Factura is None and a1.ACT_Moneda == "GTQ"


async def test_todo_o_nada(client, auth_headers, domain_seed, session):
    # act_1 asignado: su ubicación la da la custodia.
    r = await client.post("/api/v1/trazabilidad/movimientos", headers=auth_headers, json={
        "ACT_Activo": domain_seed["act_1"], "PER_Persona": domain_seed["alice"], "ARE_Area": domain_seed["area"],
        "TMO_Tipo_Movimiento": domain_seed["tmo_asg"]})
    assert r.status_code == 201, r.text
    r = await client.patch(f"{API}/lote", headers=auth_headers, json={
        "activos": [domain_seed["act_2"], domain_seed["act_1"]],
        "cambios": {"ARE_Area_Ubicacion": domain_seed["area"], "ACT_Costo": "100"}})
    assert r.status_code == 400 and r.json()["detail"] == "BATCH_ITEM_FAILED:LAP-001:ASSET_LOCATION_FOLLOWS_CUSTODY"
    session.expire_all()
    a2 = await session.get(Activo, __import__("uuid").UUID(domain_seed["act_2"]))
    assert a2.ACT_Costo is None  # el otro activo tampoco cambió

    r = await client.patch(f"{API}/lote", headers=auth_headers, json={
        "activos": [domain_seed["act_2"]], "cambios": {"ACT_Fin_Garantia": "2020-01-01"}})
    assert r.status_code == 400 and r.json()["detail"].endswith("WARRANTY_DATE_CANNOT_BE_BEFORE_PURCHASE_DATE")


async def test_especificaciones_en_lote(client, auth_headers, domain_seed, session):
    ram = TipoEspecificacion(TES_Nombre="RAM", TES_Unidad_Medida="GB")
    session.add(ram)
    await session.commit()
    ids = [domain_seed["act_1"], domain_seed["act_2"]]
    r = await client.patch(f"{API}/lote", headers=auth_headers, json={"activos": ids, "cambios": {
        "especificaciones": [{"TES_Tipo_Especificacion": ram.TES_Tipo_Especificacion, "valor": "16"}]}})
    assert r.status_code == 200, r.text
    total = (await session.execute(select(func.count()).select_from(Especificacion).where(
        Especificacion.TES_Tipo_Especificacion == ram.TES_Tipo_Especificacion, Especificacion.ESP_Valor == "16"))).scalar_one()
    assert total == 2

    # Valores permitidos del tipo de activo.
    r = await client.put(f"/api/v1/catalogos/especificaciones-tipo/{domain_seed['tac_lap']}", headers=auth_headers,
                         json={"items": [{"TES_Tipo_Especificacion": ram.TES_Tipo_Especificacion, "valores": ["8", "16"]}]})
    assert r.status_code == 200
    r = await client.patch(f"{API}/lote", headers=auth_headers, json={"activos": ids, "cambios": {
        "especificaciones": [{"TES_Tipo_Especificacion": ram.TES_Tipo_Especificacion, "valor": "12"}]}})
    assert r.status_code == 422 and r.json()["detail"].endswith("SPECIFICATION_VALUE_NOT_ALLOWED")

    # Deshacer quita la especificación que no existía.
    previo = (await client.patch(f"{API}/lote", headers=auth_headers, json={"activos": ids[:1], "cambios": {
        "especificaciones": [{"TES_Tipo_Especificacion": ram.TES_Tipo_Especificacion, "valor": "8"}]}})).json()
    assert previo["anteriores"][0]["cambios"]["especificaciones"][0]["valor"] == "16"


async def test_solo_administradores(client, session, sa_user, domain_seed):
    from tests.test_catalogo_generico import _cuenta
    h = await _cuenta(client, session, sa_user, "tecnico.lote", "TECNICO")
    r = await client.patch(f"{API}/lote", headers=h, json={"activos": [domain_seed["act_1"]], "cambios": {"ACT_Costo": "1"}})
    assert r.status_code == 403

"""La entrada de un consumible con costo valoriza el stock en la moneda de la compra."""
import pytest


@pytest.mark.asyncio
async def test_entrada_guarda_costo_y_moneda(client, auth_headers):
    h = auth_headers
    cid = (await client.post("/api/v1/consumibles", headers=h, json={
        "CON_Nombre": "Tóner costo", "CON_Unidad": "unidad", "CON_Stock_Actual": 0, "CON_Stock_Minimo": 0,
    })).json()["CON_Consumible"]
    r = await client.post(f"/api/v1/consumibles/{cid}/entrada", headers=h,
                          json={"MOC_Cantidad": 4, "precio_unitario": "25.50", "moneda": "USD"})
    assert r.status_code == 201, r.text
    det = (await client.get(f"/api/v1/consumibles/{cid}", headers=h)).json()
    assert det["CON_Stock_Actual"] == 4
    assert float(det["CON_Costo_Unitario"]) == 25.5
    assert det["CON_Moneda"] == "USD"

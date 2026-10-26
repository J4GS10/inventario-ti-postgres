"""Deshacer asignaciones, transferencias y entregas recientes (movimiento inverso, nada se borra)."""
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.models.kardex import Kardex
from app.models.traceability import Movimiento

API = "/api/v1/trazabilidad"
pytestmark = pytest.mark.asyncio


async def _custodio(session, activo_id: str):
    session.expire_all()
    return (await session.execute(select(Movimiento.PER_Persona).where(
        Movimiento.ACT_Activo == uuid.UUID(activo_id), Movimiento.MOV_Fecha_Devolucion.is_(None)))).scalar_one_or_none()


async def test_deshacer_asignacion_y_transferencia(client, auth_headers, domain_seed, session):
    h = auth_headers
    r = await client.post(f"{API}/entrega-lote", headers=h, json={
        "PER_Persona": domain_seed["alice"], "ARE_Area": domain_seed["area"], "activos": [domain_seed["act_1"]]})
    assert r.status_code == 201, r.text
    # Transferencia a Bob y luego se deshace: vuelve a Alice.
    r = await client.post(f"{API}/entrega-lote", headers=h, json={
        "PER_Persona": domain_seed["bob"], "ARE_Area": domain_seed["area"], "activos": [domain_seed["act_1"]]})
    mov_bob = r.json()["movimientos"][0]
    assert r.json()["items"][0]["operacion"] == "TRANSFERENCIA"
    r = await client.post(f"{API}/movimientos/revertir", headers=h, json={"movimientos": [mov_bob]})
    assert r.status_code == 200, r.text
    assert r.json()["items"][0]["operacion"] == "TRANSFERENCIA_REVERTIDA"
    assert str(await _custodio(session, domain_seed["act_1"])) == domain_seed["alice"]

    # Deshacer lo deshecho vuelve a transferir a Bob (el movimiento vigente es una transferencia).
    vigente = (await session.execute(select(Movimiento.MOV_Movimiento).where(
        Movimiento.ACT_Activo == uuid.UUID(domain_seed["act_1"]), Movimiento.MOV_Fecha_Devolucion.is_(None)))).scalar_one()
    r = await client.post(f"{API}/movimientos/revertir", headers=h, json={"movimientos": [str(vigente)]})
    assert r.status_code == 200, r.text
    assert str(await _custodio(session, domain_seed["act_1"])) == domain_seed["bob"]

    # Una asignación simple se deshace devolviendo el equipo a bodega.
    r = await client.post(f"{API}/entrega-lote", headers=h, json={
        "PER_Persona": domain_seed["alice"], "ARE_Area": domain_seed["area"], "activos": [domain_seed["act_2"]]})
    r = await client.post(f"{API}/movimientos/revertir", headers=h, json={"movimientos": r.json()["movimientos"]})
    assert r.status_code == 200 and r.json()["items"][0]["operacion"] == "ASIGNACION_REVERTIDA"
    assert await _custodio(session, domain_seed["act_2"]) is None
    # Un movimiento ya cerrado no se deshace dos veces.
    r = await client.post(f"{API}/movimientos/revertir", headers=h, json={"movimientos": [mov_bob]})
    assert r.status_code == 409 and r.json()["detail"].endswith("MOVEMENT_UNDO_NOT_CURRENT")
    # El kardex conserva todo (solo inserción).
    tipos = [k for k in (await session.execute(select(Kardex.KDX_Tipo).where(
        Kardex.ACT_Activo == uuid.UUID(domain_seed["act_1"])))).scalars()]
    assert tipos.count("TRANSFERENCIA") == 3


async def test_deshacer_limites(client, auth_headers, domain_seed, session, sa_user):
    from tests.test_catalogo_generico import _cuenta
    r = await client.post(f"{API}/entrega-lote", headers=auth_headers, json={
        "PER_Persona": domain_seed["alice"], "ARE_Area": domain_seed["area"], "activos": [domain_seed["act_2"]]})
    mov = r.json()["movimientos"][0]
    tecnico = await _cuenta(client, session, sa_user, "tecnico.undo", "TECNICO")
    r = await client.post(f"{API}/movimientos/revertir", headers=tecnico, json={"movimientos": [mov]})
    assert r.status_code == 403 and r.json()["detail"].endswith("MOVEMENT_UNDO_NOT_OWNER")
    fila = await session.get(Movimiento, uuid.UUID(mov))
    fila.MOV_Fecha_Asignacion = fila.MOV_Fecha_Asignacion - timedelta(minutes=45)
    await session.commit()
    r = await client.post(f"{API}/movimientos/revertir", headers=auth_headers, json={"movimientos": [mov]})
    assert r.status_code == 409 and r.json()["detail"].endswith("MOVEMENT_UNDO_WINDOW_EXPIRED")

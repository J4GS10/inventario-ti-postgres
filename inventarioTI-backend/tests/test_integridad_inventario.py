"""
Fase 1 — integridad del inventario (regresiones de la QA del 2026-09-28).

  - B3: el estado de un activo no se edita a mano salvo Disponible ↔ En Bodega.
  - M2: un activo nuevo nace en Disponible o En Bodega.
  - A1: el detalle por id responde (antes 500 siempre).
  - A2: la edición guarda la fecha de compra y la de garantía.
  - M5: un activo con sede no puede quedar sin sede.
  - A3: /movimientos solo abre custodias (asignación o préstamo).
  - A4: no se entrega un equipo a una persona inactiva.
  - A5: un equipo con ticket abierto no se asigna ni transfiere; al devolverlo
        sigue en reparación; cerrar el ticket no "resucita" una baja; la baja
        exige cerrar antes el ticket.
  - A6: un ticket no se cierra dos veces.
  - M20: el tablero cuenta asignados y stock por estado.
"""
import uuid
from datetime import date, datetime, timedelta

import pytest

from tests.aprobacion_utils import baja_aprobada
from sqlalchemy import select, update

from app.models.core import Activo
from app.models.organization import Persona

BASE = "/api/v1"


def _u(v):
    return uuid.UUID(v) if isinstance(v, str) else v


async def _asignar(client, h, d, activo, persona="alice", tipo="tmo_asg"):
    return await client.post(f"{BASE}/trazabilidad/movimientos", headers=h, json={
        "ACT_Activo": d[activo], "PER_Persona": d[persona],
        "ARE_Area": d["area"], "TMO_Tipo_Movimiento": d[tipo],
        "MOV_Fecha_Prevista_Devolucion": "2099-12-31" if tipo == "tmo_pres" else None,
    })


async def _abrir_ticket(client, h, d, activo="act_1"):
    return await client.post(f"{BASE}/mantenimiento/", headers=h, json={
        "ACT_Activo": d[activo], "PER_Persona_Solicita": d["alice"],
        "TMA_Tipo_Mantenimiento": d["tma_corr"], "MAN_Descripcion_Falla": "No enciende",
        "MAN_Costo_Total": 0,
    })


async def _estado(session, d, activo) -> int:
    session.expire_all()
    return (await session.execute(
        select(Activo.EOP_Estado_Operativo).where(Activo.ACT_Activo == _u(d[activo]))
    )).scalar_one()


# --------------------------------------------------------------------------
# Estado y edición del activo
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_estado_no_se_edita_a_mano(client, auth_headers, domain_seed):
    d, h = domain_seed, auth_headers
    for destino in ("eop_asig", "eop_baja", "eop_rep"):
        r = await client.patch(f"{BASE}/core/activos/{d['act_1']}", headers=h,
                               json={"EOP_Estado_Operativo": d[destino]})
        assert r.status_code == 400, (destino, r.text)
        assert r.json()["detail"] == "STATE_CHANGE_REQUIRES_OPERATION"

    # Disponible ↔ En Bodega sí es una edición legítima de almacén.
    r = await client.patch(f"{BASE}/core/activos/{d['act_1']}", headers=h,
                           json={"EOP_Estado_Operativo": d["eop_bod"]})
    assert r.status_code == 200, r.text
    assert r.json()["EOP_Estado_Operativo"] == d["eop_bod"]


@pytest.mark.asyncio
async def test_estado_de_un_asignado_no_se_edita(client, auth_headers, domain_seed):
    d, h = domain_seed, auth_headers
    assert (await _asignar(client, h, d, "act_1")).status_code == 201
    r = await client.patch(f"{BASE}/core/activos/{d['act_1']}", headers=h,
                           json={"EOP_Estado_Operativo": d["eop_disp"]})
    assert r.status_code == 400
    assert r.json()["detail"] == "STATE_CHANGE_REQUIRES_OPERATION"


@pytest.mark.asyncio
async def test_alta_solo_en_estado_de_almacen(client, auth_headers, domain_seed):
    d, h = domain_seed, auth_headers
    base = {"ACT_Serie_Fabricante": "INT-ALTA-1", "ACT_Codigo_Interno": "LAP-INT-1",
            "ACT_Fecha_Compra": "2026-01-10", "MOD_Modelo": d["mod"], "TAC_Tipo_Activo": d["tac_lap"]}
    r = await client.post(f"{BASE}/core/activos", headers=h, json={**base, "EOP_Estado_Operativo": d["eop_asig"]})
    assert r.status_code == 400
    assert r.json()["detail"] == "INITIAL_STATE_MUST_BE_AVAILABLE_OR_STORAGE"
    r = await client.post(f"{BASE}/core/activos", headers=h, json={**base, "EOP_Estado_Operativo": d["eop_disp"]})
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_detalle_por_id_responde(client, auth_headers, domain_seed):
    r = await client.get(f"{BASE}/core/activos/{domain_seed['act_1']}", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ACT_Codigo_Interno"] == "LAP-001"
    assert body["modelo"]["MOD_Nombre"] == "Model-X"
    assert body["estado_operativo"]["EOP_Nombre"] == "Disponible"


@pytest.mark.asyncio
async def test_edicion_guarda_fechas(client, auth_headers, domain_seed):
    d, h = domain_seed, auth_headers
    r = await client.patch(f"{BASE}/core/activos/{d['act_1']}", headers=h, json={
        "ACT_Fecha_Compra": "2025-03-01", "ACT_Fin_Garantia": "2028-03-01"})
    assert r.status_code == 200, r.text
    assert r.json()["ACT_Fecha_Compra"] == "2025-03-01"
    assert r.json()["ACT_Fin_Garantia"] == "2028-03-01"

    # La garantía se valida contra la fecha de compra final.
    r = await client.patch(f"{BASE}/core/activos/{d['act_1']}", headers=h, json={"ACT_Fin_Garantia": "2024-01-01"})
    assert r.status_code == 400
    assert r.json()["detail"] == "WARRANTY_DATE_CANNOT_BE_BEFORE_PURCHASE_DATE"


@pytest.mark.asyncio
async def test_activo_con_sede_no_queda_sin_sede(client, auth_headers, domain_seed):
    d, h = domain_seed, auth_headers
    assert (await client.patch(f"{BASE}/core/activos/{d['act_1']}", headers=h,
                               json={"SED_Sede": d["sede"]})).status_code == 200
    r = await client.patch(f"{BASE}/core/activos/{d['act_1']}", headers=h, json={"SED_Sede": None})
    assert r.status_code == 400
    assert r.json()["detail"] == "SEDE_REQUIRED"


# --------------------------------------------------------------------------
# Movimientos
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_movimientos_solo_abre_custodias(client, auth_headers, domain_seed):
    d, h = domain_seed, auth_headers
    for tipo in ("tmo_dev", "tmo_in", "tmo_tra"):
        r = await _asignar(client, h, d, "act_1", tipo=tipo)
        assert r.status_code == 400, (tipo, r.text)
        assert r.json()["detail"] == "MOVEMENT_TYPE_REQUIRES_SPECIFIC_OPERATION"
    assert (await _asignar(client, h, d, "act_1", tipo="tmo_pres")).status_code == 201


@pytest.mark.asyncio
async def test_no_se_entrega_a_persona_inactiva(client, auth_headers, domain_seed, session):
    d, h = domain_seed, auth_headers
    await session.execute(update(Persona).where(Persona.PER_Persona == _u(d["bob"])).values(PER_Estado=False))
    await session.commit()
    r = await _asignar(client, h, d, "act_1", persona="bob")
    assert r.status_code == 400
    assert r.json()["detail"] == "PERSON_INACTIVE"

    assert (await _asignar(client, h, d, "act_1", persona="alice")).status_code == 201
    r = await client.post(f"{BASE}/trazabilidad/transferencia", headers=h, json={
        "ACT_Activo": d["act_1"], "PER_Persona_Destino": d["bob"], "ARE_Area_Destino": d["area"]})
    assert r.status_code == 400
    assert r.json()["detail"] == "PERSON_INACTIVE"


@pytest.mark.asyncio
async def test_transferencia_queda_como_transferencia(client, auth_headers, domain_seed):
    d, h = domain_seed, auth_headers
    assert (await _asignar(client, h, d, "act_1")).status_code == 201
    r = await client.post(f"{BASE}/trazabilidad/transferencia", headers=h, json={
        "ACT_Activo": d["act_1"], "PER_Persona_Destino": d["alice"], "ARE_Area_Destino": d["area"]})
    assert r.status_code == 400
    assert r.json()["detail"] == "TRANSFER_TO_SAME_CUSTODIAN"
    r = await client.post(f"{BASE}/trazabilidad/transferencia", headers=h, json={
        "ACT_Activo": d["act_1"], "PER_Persona_Destino": d["bob"], "ARE_Area_Destino": d["area"]})
    assert r.status_code == 201, r.text
    assert r.json()["tipo_movimiento"]["TMO_Nombre"] == "Transferencia"


# --------------------------------------------------------------------------
# Mantenimiento
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_equipo_en_reparacion_no_se_entrega(client, auth_headers, software_seed):
    d, h = software_seed, auth_headers
    assert (await _abrir_ticket(client, h, d)).status_code == 201
    r = await _asignar(client, h, d, "act_1")
    assert r.status_code in (400, 409)


@pytest.mark.asyncio
async def test_devolver_en_reparacion_conserva_estado(client, auth_headers, software_seed, session):
    d, h = software_seed, auth_headers
    assert (await _asignar(client, h, d, "act_1")).status_code == 201
    # Un equipo asignado puede ir a reparación sin cerrar la custodia.
    assert (await _abrir_ticket(client, h, d)).status_code == 201
    assert await _estado(session, d, "act_1") == d["eop_rep"]
    r = await client.post(f"{BASE}/trazabilidad/devolucion", headers=h, json={"ACT_Activo": d["act_1"]})
    assert r.status_code == 200, r.text
    assert await _estado(session, d, "act_1") == d["eop_rep"]  # sigue en reparación


@pytest.mark.asyncio
async def test_cierre_no_resucita_una_baja(client, auth_headers, software_seed, session):
    d, h = software_seed, auth_headers
    tid = (await _abrir_ticket(client, h, d)).json()["MAN_Mantenimiento"]
    # La baja exige cerrar el ticket antes (se valida ya al solicitarla).
    r = await client.post(f"{BASE}/core/activos/{d['act_1']}/baja", headers=h,
                          json={"motivo": "OBSOLESCENCIA", "destino": "RECICLAJE"})
    assert r.status_code == 409
    assert r.json()["detail"] == "ASSET_HAS_OPEN_MAINTENANCE"
    # Si igualmente quedó de baja (datos heredados), cerrar el ticket no la revierte.
    await session.execute(update(Activo).where(Activo.ACT_Activo == _u(d["act_1"]))
                          .values(EOP_Estado_Operativo=d["eop_baja"]))
    await session.commit()
    r = await client.patch(f"{BASE}/mantenimiento/{tid}/cerrar", headers=h, json={"MAN_Costo_Total": 0})
    assert r.status_code == 200, r.text
    assert await _estado(session, d, "act_1") == d["eop_baja"]


@pytest.mark.asyncio
async def test_no_se_abre_ticket_de_un_activo_de_baja(client, auth_headers, software_seed, jefe_headers):
    d, h = software_seed, auth_headers
    # El DELETE ya no da de baja: toda salida exige solicitud y aprobación.
    r = await client.delete(f"{BASE}/core/activos/{d['act_1']}", headers=h)
    assert r.status_code == 409 and r.json()["detail"] == "USE_DECOMMISSION_REQUEST"
    baja = {"motivo": "OBSOLESCENCIA", "destino": "RECICLAJE"}
    assert (await baja_aprobada(client, h, jefe_headers, d["act_1"], baja)).status_code == 200
    r = await _abrir_ticket(client, h, d)
    assert r.status_code == 400
    assert r.json()["detail"] == "ASSET_STATE_DOES_NOT_ALLOW_MAINTENANCE"
    # Y dar de baja dos veces no genera un segundo evento.
    r = await client.post(f"{BASE}/core/activos/{d['act_1']}/baja", headers=h, json=baja)
    assert r.status_code == 409 and r.json()["detail"] == "ASSET_ALREADY_DECOMMISSIONED"


@pytest.mark.asyncio
async def test_cierre_con_fecha_futura_y_doble_cierre(client, auth_headers, software_seed):
    d, h = software_seed, auth_headers
    tid = (await _abrir_ticket(client, h, d)).json()["MAN_Mantenimiento"]
    futura = (datetime.utcnow() + timedelta(days=2)).isoformat()
    r = await client.patch(f"{BASE}/mantenimiento/{tid}/cerrar", headers=h,
                           json={"MAN_Costo_Total": 0, "MAN_Fecha_Cierre": futura})
    assert r.status_code == 400
    assert r.json()["detail"] == "CLOSE_DATE_CANNOT_BE_IN_THE_FUTURE"
    assert (await client.patch(f"{BASE}/mantenimiento/{tid}/cerrar", headers=h,
                               json={"MAN_Costo_Total": 10})).status_code == 200
    r = await client.patch(f"{BASE}/mantenimiento/{tid}/cerrar", headers=h, json={"MAN_Costo_Total": 20})
    assert r.status_code == 400


# --------------------------------------------------------------------------
# Tablero
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_tablero_cuenta_por_estado(client, auth_headers, domain_seed):
    d, h = domain_seed, auth_headers
    assert (await _asignar(client, h, d, "act_1")).status_code == 201
    stats = (await client.get(f"{BASE}/stats/dashboard", headers=h)).json()
    assert stats["activos_asignados"] == stats["por_estado"].get("Asignado", 0) == 1
    assert stats["activos_stock"] == stats["por_estado"].get("Disponible", 0) + stats["por_estado"].get("En Bodega", 0)
    assert stats["custodias_abiertas"] == 1

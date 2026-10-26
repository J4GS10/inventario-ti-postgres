"""
Fase 4 — trazabilidad y controles.

  - Kardex: cada operación deja su evento (alta, asignación, transferencia,
    devolución, mantenimiento, baja, consumibles, licencias) con quién, desde
    dónde, hacia dónde, motivo y documento; se consulta por objeto y por
    categoría (entradas/salidas, reasignaciones) y se exporta.
  - El kardex es de solo inserción para el rol de las peticiones.
  - Componentes: instalar/retirar con historial; reglas (ciclos, asignados).
  - Baja formal con motivo y destino; libera licencias; componentes primero.
  - Licencias: historial con fecha y motivo del retiro.
  - Préstamos con fecha prevista; devolución con motivo y ubicación.
  - Toma de inventario: lecturas con resultado inmediato y cierre con faltantes.
  - Reportes: custodia a una fecha, entradas y salidas, hoja de vida en PDF,
    bandeja de atención.
  - La migración reconstruye el historial previo.
"""
import uuid
from datetime import date, timedelta

import pytest

from tests.aprobacion_utils import baja_aprobada
from sqlalchemy import select, text

from app.models.kardex import Kardex

BASE = "/api/v1"


def _u(v):
    return uuid.UUID(v) if isinstance(v, str) else v


async def _eventos(client, h, **params):
    r = await client.get(f"{BASE}/inventario/kardex", headers=h, params={"limite": 500, **params})
    assert r.status_code == 200, r.text
    return r.json()["items"]


async def _asignar(client, h, d, activo="act_1", persona="alice", **extra):
    r = await client.post(f"{BASE}/trazabilidad/movimientos", headers=h, json={
        "ACT_Activo": d[activo], "PER_Persona": d[persona], "ARE_Area": d["area"],
        "TMO_Tipo_Movimiento": d["tmo_asg"], **extra})
    assert r.status_code == 201, r.text
    return r.json()


@pytest.mark.asyncio
async def test_vida_completa_de_un_activo_en_el_kardex(client, auth_headers, software_seed, jefe_headers):
    h, d = auth_headers, software_seed
    await client.patch(f"{BASE}/core/activos/{d['act_1']}", headers=h, json={"SED_Sede": d["sede"]})
    await _asignar(client, h, d, MOV_Motivo="Ingreso de personal")
    r = await client.post(f"{BASE}/trazabilidad/transferencia", headers=h, json={
        "ACT_Activo": d["act_1"], "PER_Persona_Destino": d["bob"], "ARE_Area_Destino": d["area"],
        "MOV_Motivo": "Cambio de puesto"})
    assert r.status_code == 201, r.text
    r = await client.post(f"{BASE}/trazabilidad/devolucion", headers=h, json={
        "ACT_Activo": d["act_1"], "MOV_Motivo": "Renuncia", "ARE_Area_Destino": d["area"], "condicion": "CON_DANO"})
    assert r.status_code == 200, r.text
    assert r.json()["siguientes_pasos"][0] == "ABRIR_TICKET_MANTENIMIENTO"
    tid = (await client.post(f"{BASE}/mantenimiento/", headers=h, json={
        "ACT_Activo": d["act_1"], "PER_Persona_Solicita": d["alice"], "TMA_Tipo_Mantenimiento": d["tma_corr"],
        "MAN_Descripcion_Falla": "Pantalla rota", "MAN_Costo_Total": 0})).json()["MAN_Mantenimiento"]
    assert (await client.patch(f"{BASE}/mantenimiento/{tid}/cerrar", headers=h,
                               json={"MAN_Costo_Total": 350, "MAN_Moneda": "GTQ"})).status_code == 200
    r = await baja_aprobada(client, h, jefe_headers, d["act_1"], {
        "motivo": "DANO_IRREPARABLE", "destino": "RECICLAJE", "documento": "Acta de baja 12"})
    assert r.status_code == 200, r.text
    assert r.json()["APR_Estado"] == "APROBADA"
    activo = (await client.get(f"{BASE}/core/activos/{d['act_1']}", headers=h)).json()
    assert activo["ACT_Baja_Motivo"] == "DANO_IRREPARABLE"

    eventos = await _eventos(client, h, activo_id=d["act_1"])
    tipos = [e["KDX_Tipo"] for e in reversed(eventos)]
    assert tipos == ["TRASLADO_SEDE", "ASIGNACION", "TRANSFERENCIA", "DEVOLUCION",
                     "MANTENIMIENTO_ENTRADA", "MANTENIMIENTO_SALIDA", "BAJA"]
    transferencia = next(e for e in eventos if e["KDX_Tipo"] == "TRANSFERENCIA")
    assert transferencia["origen_persona"] == "Alice Test" and transferencia["destino_persona"] == "Bob Test"
    assert transferencia["KDX_Motivo"] == "Cambio de puesto"
    assert transferencia["usuario"] == "sa"
    salida = next(e for e in eventos if e["KDX_Tipo"] == "MANTENIMIENTO_SALIDA")
    assert float(salida["KDX_Costo"]) == 350.0 and salida["KDX_Moneda"] == "GTQ"
    baja = next(e for e in eventos if e["KDX_Tipo"] == "BAJA")
    assert baja["KDX_Detalle"]["destino"] == "RECICLAJE" and baja["KDX_Documento_Ref"] == "Acta de baja 12"
    # Quién la pidió y quién la aprobó quedan en el evento.
    assert baja["KDX_Detalle"]["solicitado_por"] != baja["KDX_Detalle"]["aprobado_por"]

    # Categorías: entradas/salidas y reasignaciones.
    salidas = await _eventos(client, h, categoria="SALIDAS")
    assert [e["KDX_Tipo"] for e in salidas] == ["BAJA"]
    reasignaciones = {e["KDX_Tipo"] for e in await _eventos(client, h, categoria="REASIGNACIONES")}
    assert {"ASIGNACION", "TRANSFERENCIA", "DEVOLUCION"} <= reasignaciones

    # CSV en inglés, con BOM y el tipo traducido.
    r = await client.get(f"{BASE}/inventario/kardex.csv", headers=h, params={"activo_id": d["act_1"], "lang": "en"})
    assert r.status_code == 200
    texto = r.content.decode("utf-8")
    assert texto.startswith("﻿Date and time,Event,")
    assert "Transfer" in texto and "Decommissioning" in texto

    # Hoja de vida en PDF.
    r = await client.get(f"{BASE}/inventario/activos/{d['act_1']}/hoja-de-vida.pdf", headers=h)
    assert r.status_code == 200 and r.content[:4] == b"%PDF"


@pytest.mark.asyncio
async def test_kardex_solo_insercion(session, auth_headers, software_seed, client):
    from app.core.config import settings
    await _asignar(client, auth_headers, software_seed)
    await session.execute(text(f'SET LOCAL ROLE "{settings.DB_RLS_ROLE}"'))
    with pytest.raises(Exception) as exc:
        await session.execute(text('UPDATE "INV_KARDEX" SET "KDX_Motivo" = \'x\''))
    assert "permission denied" in str(exc.value).lower()
    await session.rollback()


@pytest.mark.asyncio
async def test_componentes_instalar_y_retirar(client, auth_headers, domain_seed):
    h, d = auth_headers, domain_seed
    # Un disco (clase Componente) va dentro de la laptop; otra laptop no.
    tipo = (await client.post(f"{BASE}/cat/tipos-activo", headers=h,
                              json={"TAC_Nombre": "Disco", "TAC_Prefijo": "DIS"})).json()
    disco = (await client.post(f"{BASE}/core/activos", headers=h, json={
        "ACT_Serie_Fabricante": "DIS-SER-1", "ACT_Fecha_Compra": "2024-01-10", "MOD_Modelo": d["mod"],
        "TAC_Tipo_Activo": tipo["TAC_Tipo_Activo"], "EOP_Estado_Operativo": d["eop_bod"]})).json()
    r = await client.post(f"{BASE}/core/activos/{d['act_1']}/componentes", headers=h,
                          json={"ACT_Componente": d["act_2"], "motivo": "Otra laptop"})
    assert r.status_code == 422 and r.json()["detail"] == "COMPONENT_TYPE_NOT_ALLOWED"
    r = await client.post(f"{BASE}/core/activos/{d['act_1']}/componentes", headers=h,
                          json={"ACT_Componente": disco["ACT_Activo"], "motivo": "Disco adicional"})
    assert r.status_code == 200, r.text
    assert [c["ACT_Codigo_Interno"] for c in r.json()["actuales"]] == [disco["ACT_Codigo_Interno"]]
    # Un disco no admite componentes (tampoco la laptop dentro de él).
    r = await client.post(f"{BASE}/core/activos/{disco['ACT_Activo']}/componentes", headers=h,
                          json={"ACT_Componente": d["act_1"]})
    assert r.status_code == 422 and r.json()["detail"] == "ASSET_CLASS_NO_COMPONENTS"
    # Un componente instalado no se asigna por separado.
    r = await client.post(f"{BASE}/trazabilidad/movimientos", headers=h, json={
        "ACT_Activo": disco["ACT_Activo"], "PER_Persona": d["alice"], "ARE_Area": d["area"],
        "TMO_Tipo_Movimiento": d["tmo_asg"]})
    assert r.status_code == 409 and r.json()["detail"] == "ASSET_INSTALLED_AS_COMPONENT"
    # La baja del equipo exige retirar (o autorizar el retiro de) sus componentes.
    r = await client.post(f"{BASE}/core/activos/{d['act_1']}/baja", headers=h,
                          json={"motivo": "OBSOLESCENCIA", "destino": "DONACION"})
    assert r.status_code == 409 and r.json()["detail"] == "ASSET_HAS_COMPONENTS:1"

    r = await client.post(f"{BASE}/core/activos/{d['act_1']}/componentes/{disco['ACT_Activo']}/retirar", headers=h,
                          json={"motivo": "Se reasigna a otra estación", "estado_destino": "Disponible"})
    assert r.status_code == 200, r.text
    assert r.json()["actuales"] == []
    assert [e["KDX_Tipo"] for e in r.json()["historial"]] == ["COMPONENTE_RETIRADO", "COMPONENTE_INSTALADO"]


@pytest.mark.asyncio
async def test_especificaciones_con_historial(client, auth_headers, domain_seed, session):
    from app.models.catalogs import TipoEspecificacion
    tes = TipoEspecificacion(TES_Nombre="RAM")
    session.add(tes)
    await session.commit()
    h, d = auth_headers, domain_seed
    r = await client.post(f"{BASE}/core/activos/{d['act_1']}/especificaciones", headers=h,
                          json={"TES_Tipo_Especificacion": tes.TES_Tipo_Especificacion, "ESP_Valor": "8 GB"})
    assert r.status_code == 201, r.text
    esp = r.json()["ESP_Especificacion"]
    assert (await client.patch(f"{BASE}/core/especificaciones/{esp}", headers=h,
                               json={"ESP_Valor": "16 GB", "motivo": "Ampliación de memoria"})).status_code == 204
    cambio = (await _eventos(client, h, activo_id=d["act_1"], tipos=["ESPECIFICACION"]))[0]
    assert cambio["KDX_Detalle"] == {"tipo": tes.TES_Tipo_Especificacion, "antes": "8 GB", "despues": "16 GB"}
    assert cambio["KDX_Motivo"] == "Ampliación de memoria"


@pytest.mark.asyncio
async def test_licencias_historial_y_liberacion_en_baja(client, auth_headers, software_seed, jefe_headers):
    h, d = auth_headers, software_seed
    r = await client.post(f"{BASE}/soft/instalaciones", headers=h,
                          json={"LIC_Licencia": d["lic"], "ACT_Activo": d["act_1"], "INS_Fecha_Instalacion": "2026-09-01"})
    assert r.status_code in (200, 201), r.text
    r = await baja_aprobada(client, h, jefe_headers, d["act_1"], {"motivo": "OBSOLESCENCIA", "destino": "RECICLAJE"})
    assert r.status_code == 200, r.text
    hist = (await client.get(f"{BASE}/soft/licencias/{d['lic']}/historial", headers=h)).json()
    assert hist[0]["INS_Estado"] is False
    assert hist[0]["INS_Motivo_Retiro"] == "Baja del equipo" and hist[0]["INS_Fecha_Retiro"]
    tipos = [e["KDX_Tipo"] for e in await _eventos(client, h, licencia_id=d["lic"])]
    assert tipos == ["LICENCIA_RETIRADA", "LICENCIA_ASIGNADA"]
    r = await client.get(f"{BASE}/inventario/licencias.csv", headers=h, params={"lang": "es"})
    assert r.status_code == 200 and "Office 365" in r.content.decode("utf-8")


@pytest.mark.asyncio
async def test_prestamo_con_fecha_y_alerta_de_vencido(client, auth_headers, domain_seed, session):
    from sqlalchemy import update
    from app.models.traceability import Movimiento
    h, d = auth_headers, domain_seed
    r = await client.post(f"{BASE}/trazabilidad/movimientos", headers=h, json={
        "ACT_Activo": d["act_1"], "PER_Persona": d["alice"], "ARE_Area": d["area"],
        "TMO_Tipo_Movimiento": d["tmo_pres"]})
    assert r.status_code == 400 and r.json()["detail"] == "LOAN_RETURN_DATE_REQUIRED"
    manana = (date.today() + timedelta(days=1)).isoformat()
    mov = await _asignar(client, h, d, TMO_Tipo_Movimiento=d["tmo_pres"], MOV_Fecha_Prevista_Devolucion=manana)
    assert mov["MOV_Fecha_Prevista_Devolucion"] == manana
    # Simular que venció.
    await session.execute(update(Movimiento).where(Movimiento.MOV_Movimiento == _u(mov["MOV_Movimiento"]))
                          .values(MOV_Fecha_Prevista_Devolucion=date.today() - timedelta(days=3)))
    await session.commit()
    atencion = (await client.get(f"{BASE}/inventario/atencion", headers=h)).json()
    vencidos = next(i for i in atencion["items"] if i["clave"] == "PRESTAMO_VENCIDO")
    assert vencidos["total"] == 1 and vencidos["items"][0]["codigo"] == "LAP-001"


@pytest.mark.asyncio
async def test_custodia_a_una_fecha(client, auth_headers, domain_seed):
    h, d = auth_headers, domain_seed
    await _asignar(client, h, d)
    hoy = (await client.get(f"{BASE}/inventario/custodia", headers=h)).json()
    assert [(c["activo_codigo"], c["persona"]) for c in hoy] == [("LAP-001", "Alice Test")]
    ayer = (await client.get(f"{BASE}/inventario/custodia", headers=h,
                             params={"fecha": (date.today() - timedelta(days=2)).isoformat()})).json()
    assert ayer == []
    r = await client.get(f"{BASE}/inventario/custodia.csv", headers=h, params={"lang": "it"})
    assert r.content.decode("utf-8").startswith("﻿Codice,")


@pytest.mark.asyncio
async def test_toma_de_inventario(client, auth_headers, domain_seed):
    h, d = auth_headers, domain_seed
    for act in ("act_1", "act_2"):
        await client.patch(f"{BASE}/core/activos/{d[act]}", headers=h, json={"SED_Sede": d["sede"]})
    toma = (await client.post(f"{BASE}/inventario/tomas", headers=h, json={"SED_Sede": d["sede"]})).json()
    tid = toma["TOM_Toma"]
    r = await client.post(f"{BASE}/inventario/tomas", headers=h, json={"SED_Sede": d["sede"]})
    assert r.status_code == 409  # ya hay una abierta para esa sede

    lectura = (await client.post(f"{BASE}/inventario/tomas/{tid}/lecturas", headers=h, json={"codigo": "lap-001"})).json()
    assert lectura["TLE_Resultado"] == "ENCONTRADO" and lectura["esperados"] == 2 and lectura["leidos"] == 1
    assert (await client.post(f"{BASE}/inventario/tomas/{tid}/lecturas", headers=h,
                              json={"codigo": "LAP-001"})).json()["TLE_Resultado"] == "REPETIDO"
    assert (await client.post(f"{BASE}/inventario/tomas/{tid}/lecturas", headers=h,
                              json={"codigo": "NO-EXISTE-99"})).json()["TLE_Resultado"] == "NO_REGISTRADO"

    cierre = await client.post(f"{BASE}/inventario/tomas/{tid}/cerrar", headers=h)
    assert cierre.status_code == 200, cierre.text
    resumen = cierre.json()["resumen"]
    assert resumen["esperados"] == 2 and resumen["encontrados"] == 1
    assert [f["codigo"] for f in resumen["faltantes"]] == ["LAP-002"]
    assert [n["codigo"] for n in resumen["no_registrados"]] == ["NO-EXISTE-99"]
    faltante = (await _eventos(client, h, activo_id=d["act_2"], tipos=["INVENTARIO_FISICO"]))[0]
    assert faltante["KDX_Detalle"]["resultado"] == "FALTANTE"
    r = await client.get(f"{BASE}/inventario/tomas/{tid}/reporte.csv", headers=h)
    assert "FALTANTE,LAP-002" in r.content.decode("utf-8")


@pytest.mark.asyncio
async def test_consumibles_en_kardex_y_ajuste(client, auth_headers, jefe_headers, domain_seed):
    h = auth_headers
    cid = (await client.post("/api/v1/consumibles", headers=h, json={
        "CON_Nombre": "Cable HDMI", "CON_Unidad": "unidad", "CON_Stock_Actual": 0,
        "CON_Stock_Minimo": 3})).json()["CON_Consumible"]
    r = await client.post(f"/api/v1/consumibles/{cid}/entrada", headers=h,
                          json={"MOC_Cantidad": 5, "precio_unitario": "25.50", "moneda": "GTQ"})
    assert r.status_code == 201, r.text
    r = await client.post(f"/api/v1/consumibles/{cid}/salida", headers=h,
                          json={"MOC_Cantidad": 3, "PER_Persona": domain_seed["alice"]})
    assert r.status_code == 201 and r.json()["bajo_minimo"] is True
    # Un ajuste que baja la existencia se aplica cuando el Jefe de TI lo aprueba.
    r = await client.post(f"/api/v1/consumibles/{cid}/ajuste", headers=h,
                          json={"CON_Stock_Real": 1, "MOC_Motivo": "Conteo físico de bodega"})
    assert r.status_code == 202, r.text
    r = await client.post(f"/api/v1/aprobaciones/{r.json()['APR_Aprobacion']}/aprobar", headers=jefe_headers, json={})
    assert r.status_code == 200, r.text
    eventos = await _eventos(client, h, consumible_id=cid)
    assert [(e["KDX_Tipo"], e["KDX_Saldo"]) for e in reversed(eventos)] == [
        ("CONSUMIBLE_ENTRADA", 5), ("CONSUMIBLE_SALIDA", 2), ("CONSUMIBLE_AJUSTE", 1)]
    assert eventos[1]["destino_persona"] == "Alice Test"
    resumen = (await client.get(f"{BASE}/inventario/entradas-salidas", headers=h)).json()
    entrada = next(t for t in resumen["por_tipo"] if t["tipo"] == "CONSUMIBLE_ENTRADA")
    assert entrada["cantidad"] == 5 and entrada["valor_por_moneda"]["GTQ"] == 127.5


@pytest.mark.asyncio
async def test_reconstruccion_del_historial(session, auth_headers, software_seed, client):
    """La migración carga en el kardex lo que ya existía (marcado como migrado)."""
    from app.services.kardex_migracion import reconstruir
    h, d = auth_headers, software_seed
    await _asignar(client, h, d)
    await client.post(f"{BASE}/trazabilidad/devolucion", headers=h, json={"ACT_Activo": d["act_1"]})
    # Simular una base previa al kardex: se borran los eventos (dueño de las tablas).
    await session.execute(text('ALTER TABLE "INV_KARDEX" DISABLE TRIGGER trg_kardex_append_only'))
    await session.execute(text('DELETE FROM "INV_KARDEX"'))
    await session.execute(text('ALTER TABLE "INV_KARDEX" ENABLE TRIGGER trg_kardex_append_only'))
    await session.commit()
    creados = await session.run_sync(lambda s: reconstruir(s.connection()))
    await session.commit()
    assert creados > 0
    filas = (await session.execute(
        select(Kardex.KDX_Tipo).where(Kardex.ACT_Activo == _u(d["act_1"])).order_by(Kardex.KDX_Fecha)
    )).scalars().all()
    assert filas == ["ALTA", "ASIGNACION", "DEVOLUCION"]
    assert all((await session.execute(select(Kardex.KDX_Migrado))).scalars().all())
    # Idempotente: una segunda ejecución no duplica.
    assert await session.run_sync(lambda s: reconstruir(s.connection())) == 0

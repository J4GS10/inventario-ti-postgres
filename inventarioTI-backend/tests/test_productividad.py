"""
Entrega de puesto y devolución en lote, búsqueda global, kit sugerido,
calidad de datos e importación de activos desde Excel.
"""
import io

import pytest
from sqlalchemy import select

API = "/api/v1"


async def _asignar(client, headers, activo, persona, d):
    r = await client.post(f"{API}/trazabilidad/movimientos", json={
        "ACT_Activo": activo, "PER_Persona": persona, "ARE_Area": d["area"], "TMO_Tipo_Movimiento": d["tmo_asg"],
    }, headers=headers)
    assert r.status_code == 201, r.text


async def _vigentes(client, headers, persona):
    r = await client.get(f"{API}/trazabilidad/persona/{persona}/asignaciones", headers=headers)
    assert r.status_code == 200, r.text
    return {m["ACT_Activo"] for m in r.json() if not m.get("MOV_Fecha_Devolucion")}


# ---------------------------------------------------------------- lote
@pytest.mark.asyncio
async def test_entrega_lote_asigna_todos_y_audita(client, auth_headers, domain_seed, session):
    d = domain_seed
    r = await client.post(f"{API}/trazabilidad/entrega-lote", json={
        "PER_Persona": d["alice"], "ARE_Area": d["area"], "activos": [d["act_1"], d["act_2"]],
        "MOV_Motivo": "Ingreso", "ticket": "26-1",
    }, headers=auth_headers)
    assert r.status_code == 201, r.text
    cuerpo = r.json()
    assert cuerpo["total"] == 2 and len(cuerpo["movimientos"]) == 2
    assert {i["operacion"] for i in cuerpo["items"]} == {"ASIGNACION"}
    assert await _vigentes(client, auth_headers, d["alice"]) == {d["act_1"], d["act_2"]}

    from app.models.governance import AuditoriaSistema
    acciones = (await session.execute(select(AuditoriaSistema.AUD_Accion))).scalars().all()
    assert "ASSIGN_BATCH" in acciones

    # Una sola acta para toda la entrega.
    acta = await client.post(f"{API}/trazabilidad/acta/lote?formato=docx",
                             json={"movimientos_ids": cuerpo["movimientos"]}, headers=auth_headers)
    assert acta.status_code == 200, acta.text


@pytest.mark.asyncio
async def test_entrega_lote_es_atomica(client, auth_headers, domain_seed):
    """Si un equipo no se puede entregar, no se entrega ninguno y se dice cuál falló."""
    d = domain_seed
    await _asignar(client, auth_headers, d["act_1"], d["alice"], d)
    r = await client.post(f"{API}/trazabilidad/entrega-lote", json={
        "PER_Persona": d["alice"], "ARE_Area": d["area"], "activos": [d["act_2"], d["act_1"]],
    }, headers=auth_headers)
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == "BATCH_ITEM_FAILED:LAP-001:ASSET_ALREADY_WITH_PERSON"
    # LAP-002 (procesado antes del error) quedó sin asignar.
    assert await _vigentes(client, auth_headers, d["alice"]) == {d["act_1"]}


@pytest.mark.asyncio
async def test_entrega_lote_transfiere_si_ya_tiene_custodio(client, auth_headers, domain_seed):
    d = domain_seed
    await _asignar(client, auth_headers, d["act_1"], d["bob"], d)
    r = await client.post(f"{API}/trazabilidad/entrega-lote", json={
        "PER_Persona": d["alice"], "ARE_Area": d["area"], "activos": [d["act_1"], d["act_2"]],
    }, headers=auth_headers)
    assert r.status_code == 201, r.text
    ops = {i["codigo"]: i["operacion"] for i in r.json()["items"]}
    assert ops == {"LAP-001": "TRANSFERENCIA", "LAP-002": "ASIGNACION"}
    assert await _vigentes(client, auth_headers, d["bob"]) == set()


@pytest.mark.asyncio
async def test_entrega_lote_rechaza_repetidos(client, auth_headers, domain_seed):
    d = domain_seed
    r = await client.post(f"{API}/trazabilidad/entrega-lote", json={
        "PER_Persona": d["alice"], "ARE_Area": d["area"], "activos": [d["act_1"], d["act_1"]],
    }, headers=auth_headers)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_devolucion_lote(client, auth_headers, domain_seed):
    d = domain_seed
    for a in (d["act_1"], d["act_2"]):
        await _asignar(client, auth_headers, a, d["alice"], d)
    r = await client.post(f"{API}/trazabilidad/devolucion-lote", json={
        "activos": [d["act_1"], d["act_2"]], "MOV_Motivo": "Cambio de puesto", "condicion": "BUENO",
    }, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 2
    assert await _vigentes(client, auth_headers, d["alice"]) == set()

    # Devolver de nuevo falla y nombra el equipo.
    r = await client.post(f"{API}/trazabilidad/devolucion-lote", json={"activos": [d["act_1"]]},
                          headers=auth_headers)
    assert r.status_code == 400
    assert r.json()["detail"] == "BATCH_ITEM_FAILED:LAP-001:ASSET_IS_NOT_ASSIGNED"


# ---------------------------------------------------------------- búsqueda
@pytest.mark.asyncio
async def test_busqueda_global(client, auth_headers, domain_seed):
    d = domain_seed
    await _asignar(client, auth_headers, d["act_1"], d["alice"], d)
    r = await client.get(f"{API}/productividad/buscar", params={"q": "SER-001"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    activos = [x for x in r.json()["resultados"] if x["tipo"] == "activo"]
    assert [a["codigo"] for a in activos] == ["LAP-001"]
    assert activos[0]["custodio"] == "Alice Test"

    r = await client.get(f"{API}/productividad/buscar", params={"q": "alice test"}, headers=auth_headers)
    personas = [x for x in r.json()["resultados"] if x["tipo"] == "persona"]
    assert len(personas) == 1 and personas[0]["id"] == d["alice"]

    r = await client.get(f"{API}/productividad/buscar", params={"q": "a"}, headers=auth_headers)
    assert r.status_code == 422


# ---------------------------------------------------------------- kit y calidad
@pytest.mark.asyncio
async def test_kit_sugerido(client, auth_headers, domain_seed):
    d = domain_seed
    await _asignar(client, auth_headers, d["act_1"], d["bob"], d)
    r = await client.get(f"{API}/productividad/kit-sugerido/{d['alice']}", headers=auth_headers)
    assert r.status_code == 200, r.text
    kit = r.json()
    laptop = next(t for t in kit["tipos"] if t["nombre"] == "Laptop")
    assert laptop["frecuencia"] == 1.0
    assert laptop["disponibles"] == 1          # LAP-002 está en bodega
    assert laptop["ya_tiene"] is False


@pytest.mark.asyncio
async def test_calidad_datos(client, auth_headers, domain_seed):
    r = await client.get(f"{API}/productividad/calidad-datos", headers=auth_headers)
    assert r.status_code == 200, r.text
    v = {x["clave"]: x for x in r.json()["verificaciones"]}
    assert v["correos_provisionales"]["total"] >= 2          # @test.local
    assert v["estado_inconsistente"]["total"] == 0
    assert v["activos_sin_costo"]["total"] == 2
    assert v["modelos_sin_tipo"]["total"] == 1


# ---------------------------------------------------------------- importación
def _xlsx(filas):
    import openpyxl
    libro = openpyxl.Workbook()
    hoja = libro.active
    hoja.title = "INVENTARIO"
    hoja.append(["Inventario"])
    hoja.append([])
    hoja.append(["Código", "Categoría", "Descripción breve", "Marca", "Modelo", "Serie", "Ubicación actual",
                 "Fecha entrada", "ID Factura"])
    for f in filas:
        hoja.append(f)
    salida = io.BytesIO()
    libro.save(salida)
    return salida.getvalue()


def _archivo(contenido):
    return {"archivo": ("inv.xlsx", contenido,
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}


@pytest.mark.asyncio
async def test_importacion_previsualiza_y_confirma(client, auth_headers, domain_seed):
    contenido = _xlsx([
        [None, "Laptop", "Laptop nueva", "Lenovo", "T14", "PF-123", "TI", "2026-05-01", "F-1"],
        ["MON-0009", "Monitor", "Monitor 24", "TestVendor", "P24", "Generico", "TI", None, None],
        [None, "Impresora", "Sin tipo", "HP", "M1", "X-999", "TI", None, None],
        [None, "Laptop", "Repetida", "Lenovo", "T14", "SER-001", "Bodega", None, None],
    ])
    r = await client.post(f"{API}/productividad/importar/activos/previsualizar",
                          files=_archivo(contenido), headers=auth_headers)
    assert r.status_code == 200, r.text
    prev = r.json()
    assert prev["resumen"] == {"filas": 4, "validas": 2, "con_error": 2, "marcas_nuevas": 1, "modelos_nuevos": 2}
    por_fila = {f["fila"]: f for f in prev["filas"]}
    assert "NEW_BRAND" in por_fila[4]["avisos"] and "AUTO_CODE" in por_fila[4]["avisos"]
    assert "NO_SERIAL_USES_CODE" in por_fila[5]["avisos"]
    assert por_fila[6]["errores"] == ["TYPE_NOT_FOUND"]
    assert set(por_fila[7]["errores"]) == {"SERIAL_EXISTS", "AREA_NOT_FOUND"}

    r = await client.post(f"{API}/productividad/importar/activos/confirmar",
                          files=_archivo(contenido), headers=auth_headers)
    assert r.status_code == 400 and r.json()["detail"] == "IMPORT_HAS_ERRORS"

    r = await client.post(f"{API}/productividad/importar/activos/confirmar?omitir_errores=true",
                          files=_archivo(contenido), headers=auth_headers)
    assert r.status_code == 201, r.text
    res = r.json()
    assert res["creados"] == 2 and res["omitidos"] == 2
    assert res["codigos"] == ["LAP00001", "MON-0009"]

    monitor = await client.get(f"{API}/core/activos/by-code/MON-0009", headers=auth_headers)
    assert monitor.status_code == 200
    assert monitor.json()["ACT_Serie_Fabricante"] == "MON-0009"
    assert monitor.json()["ARE_Area_Ubicacion"] == domain_seed["area"]

    # Volver a importar lo mismo: todo choca con lo ya creado.
    r = await client.post(f"{API}/productividad/importar/activos/previsualizar",
                          files=_archivo(contenido), headers=auth_headers)
    assert r.json()["resumen"]["validas"] == 0


@pytest.mark.asyncio
async def test_importacion_archivo_invalido_y_plantilla(client, auth_headers, domain_seed):
    r = await client.post(f"{API}/productividad/importar/activos/previsualizar",
                          files=_archivo(b"no es un excel"), headers=auth_headers)
    assert r.status_code == 400 and r.json()["detail"] == "IMPORT_INVALID_FILE"

    r = await client.get(f"{API}/productividad/importar/activos/plantilla", headers=auth_headers)
    assert r.status_code == 200
    import openpyxl
    libro = openpyxl.load_workbook(io.BytesIO(r.content))
    assert libro["INVENTARIO"]["B1"].value == "Categoría"
    assert "Laptop" in [c.value for c in libro["Listas"]["A"]]


@pytest.mark.asyncio
async def test_importacion_tipo_por_prefijo_y_codigo_equivalente(client, auth_headers, domain_seed):
    """El prefijo del código resuelve el tipo y «LAP-01» equivale a «LAP-001» (ya existe)."""
    contenido = _xlsx([
        ["MON-00077", "Pantalla", "Monitor 27", "TestVendor", "P27", "SN-27-01", "TI", None, None],
        ["LAP-01", "Laptop", "Duplicada", "TestVendor", "Model-X", "SN-DUP-01", "TI", None, None],
    ])
    r = await client.post(f"{API}/productividad/importar/activos/previsualizar",
                          files=_archivo(contenido), headers=auth_headers)
    assert r.status_code == 200, r.text
    por_fila = {f["fila"]: f for f in r.json()["filas"]}
    assert por_fila[4]["errores"] == [] and "TYPE_FROM_CODE" in por_fila[4]["avisos"]
    assert por_fila[4]["categoria"] == "Monitor"
    assert por_fila[5]["errores"] == ["CODE_EXISTS"]

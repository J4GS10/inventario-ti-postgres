"""Matriz de roles por módulo: cada rol solo ve y escribe lo que le corresponde.

- Un rol sin el módulo recibe 403 en la API (no basta con ocultar el menú).
- TECNICO y CONSULTA no reciben costos ni montos, y si los envían se ignoran.
- COMPRAS crea proveedores y órdenes, pero no recibe en bodega ni ve personas.
- CONTABILIDAD lee valores sin datos personales y no modifica nada.
- La búsqueda global y la bandeja de atención no dan indicios de otros módulos.
"""
from datetime import date

import pytest
from sqlalchemy import select

ROLES = ("SUPER_ADMIN", "ADMIN_SEGURIDAD", "JEFE_TI", "ADMIN_TI", "TECNICO", "AUDITOR", "CONSULTA", "COMPRAS",
         "CONTABILIDAD")


async def _headers(client, session, rol: str) -> dict:
    from app.core.security import get_password_hash
    from app.models.organization import Persona, Usuario

    base = (await session.execute(select(Persona).limit(1))).scalar_one()
    usuario = f"u.{rol.lower()}"
    persona = Persona(PER_Primer_Nombre="Rol", PER_Primer_Apellido=rol.title(),
                      PER_Email_Corporativo=f"{usuario}@test.local",
                      DEP_Departamento=base.DEP_Departamento, CAR_Cargo=base.CAR_Cargo)
    session.add(persona)
    await session.flush()
    session.add(Usuario(USU_Username=usuario, USU_Password_Hash=get_password_hash("TestPassw0rd!"),
                        USU_Rol=rol, PER_Persona=persona.PER_Persona, USU_Alcance_Global=True))
    await session.commit()
    r = await client.post("/api/v1/login/access-token", data={"username": usuario, "password": "TestPassw0rd!"},
                          headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
async def por_rol(client, session, domain_seed):
    return {rol: await _headers(client, session, rol) for rol in ROLES}


TI = {"SUPER_ADMIN", "JEFE_TI", "ADMIN_TI"}
INVENTARIO = TI | {"TECNICO", "AUDITOR", "CONSULTA"}
PERSONAS = TI | {"TECNICO", "AUDITOR"}


@pytest.mark.asyncio
async def test_cada_rol_solo_entra_a_sus_modulos(client, por_rol, domain_seed):
    act, alice = domain_seed["act_1"], domain_seed["alice"]
    matriz = [
        ("/api/v1/core/activos", INVENTARIO | {"CONTABILIDAD"}),
        (f"/api/v1/core/activos/{act}/contexto", PERSONAS),
        ("/api/v1/trazabilidad/movimientos", INVENTARIO),
        (f"/api/v1/trazabilidad/persona/{alice}/asignaciones", PERSONAS),
        ("/api/v1/mantenimiento/", INVENTARIO),
        ("/api/v1/inventario/kardex", INVENTARIO),
        ("/api/v1/inventario/custodia", INVENTARIO),
        ("/api/v1/inventario/entradas-salidas", INVENTARIO | {"CONTABILIDAD"}),
        ("/api/v1/inventario/tomas", INVENTARIO),
        ("/api/v1/consumibles", INVENTARIO | {"CONTABILIDAD", "COMPRAS"}),
        ("/api/v1/soft/licencias", PERSONAS | {"CONTABILIDAD"}),
        (f"/api/v1/soft/personas/{alice}/instalaciones", PERSONAS),
        ("/api/v1/compras/ordenes", TI | {"AUDITOR", "COMPRAS", "CONTABILIDAD"}),
        ("/api/v1/compras/proveedores", TI | {"AUDITOR", "COMPRAS", "CONTABILIDAD"}),
        ("/api/v1/aprobaciones", TI | {"AUDITOR"}),
        ("/api/v1/stats/dashboard", INVENTARIO | {"CONTABILIDAD"}),
        ("/api/v1/org/personas", PERSONAS | {"ADMIN_SEGURIDAD"}),
        ("/api/v1/org/departamentos", PERSONAS | {"ADMIN_SEGURIDAD"}),
        ("/api/v1/org/sedes", set(ROLES)),
        (f"/api/v1/productividad/personas/{alice}/ficha", PERSONAS),
        ("/api/v1/productividad/calidad-datos", TI | {"AUDITOR"}),
        ("/api/v1/cat/marcas", set(ROLES) - {"ADMIN_SEGURIDAD"}),
        ("/api/v1/export/activos.csv", TI | {"AUDITOR", "CONTABILIDAD"}),
        ("/api/v1/export/movimientos.csv", TI | {"AUDITOR"}),
        ("/api/v1/export/ordenes.csv", TI | {"AUDITOR", "COMPRAS", "CONTABILIDAD"}),
        ("/api/v1/inventario/licencias.csv", TI | {"AUDITOR", "CONTABILIDAD"}),
    ]
    errores = []
    for ruta, permitidos in matriz:
        for rol, h in por_rol.items():
            codigo = (await client.get(ruta, headers=h)).status_code
            if (codigo != 403) != (rol in permitidos):
                errores.append(f"{rol} {ruta} -> {codigo}")
    assert not errores, "\n".join(errores)


@pytest.mark.asyncio
async def test_tecnico_y_consulta_no_reciben_ni_escriben_costos(client, por_rol, domain_seed, session):
    act = domain_seed["act_1"]
    sa = por_rol["SUPER_ADMIN"]
    r = await client.patch(f"/api/v1/core/activos/{act}", headers=sa, json={"ACT_Costo": "1500.00"})
    assert r.status_code == 200, r.text

    assert (await client.get(f"/api/v1/core/activos/{act}", headers=por_rol["ADMIN_TI"])).json()["ACT_Costo"] == "1500.00"
    assert (await client.get(f"/api/v1/core/activos/{act}", headers=por_rol["CONTABILIDAD"])).json()["ACT_Costo"]
    for rol in ("TECNICO", "CONSULTA"):
        h = por_rol[rol]
        detalle = (await client.get(f"/api/v1/core/activos/{act}", headers=h)).json()
        assert "ACT_Costo" not in detalle and detalle["ACT_Codigo_Interno"] == "LAP-001"
        lista = (await client.get("/api/v1/core/activos", headers=h)).json()
        assert lista and all("ACT_Costo" not in a for a in lista)
        tablero = (await client.get("/api/v1/stats/dashboard", headers=h)).json()
        assert "costo_inventario_por_moneda" not in tablero and "costo_mantenimiento_por_moneda" not in tablero
        assert "activos_totales" in tablero
        kardex = (await client.get("/api/v1/inventario/kardex", headers=h)).json()
        assert all("KDX_Costo" not in e and "KDX_Saldo_Valor" not in e for e in kardex["items"])
    # Consulta no ve licencias ni en el tablero.
    assert not any(k.startswith("licencias") for k in
                   (await client.get("/api/v1/stats/dashboard", headers=por_rol["CONSULTA"])).json())

    # El técnico edita el activo: el costo que envía se ignora y no borra el registrado.
    r = await client.patch(f"/api/v1/core/activos/{act}", headers=por_rol["TECNICO"],
                           json={"ACT_Costo": "1.00", "ACT_Hostname": "laptop-uno"})
    assert r.status_code == 200, r.text
    detalle = (await client.get(f"/api/v1/core/activos/{act}", headers=sa)).json()
    assert detalle["ACT_Costo"] == "1500.00" and detalle["ACT_Hostname"] == "laptop-uno"


@pytest.mark.asyncio
async def test_compras_registra_ordenes_pero_no_recibe(client, por_rol, domain_seed):
    h = por_rol["COMPRAS"]
    prv = await client.post("/api/v1/compras/proveedores", headers=h, json={"PRV_Nombre": "Distribuidora QA"})
    assert prv.status_code == 201, prv.text
    orden = await client.post("/api/v1/compras/ordenes", headers=h, json={
        "OCO_Numero": "OC-ROL-1", "OCO_Fecha": date.today().isoformat(), "PRV_Proveedor": prv.json()["PRV_Proveedor"],
        "SED_Sede": domain_seed["sede"],
        "lineas": [{"OCL_Descripcion": "Laptop", "OCL_Cantidad": 1, "OCL_Precio_Unitario": "900", "OCL_Tipo": "ACTIVO"}],
    })
    assert orden.status_code == 201, orden.text
    oid = orden.json()["OCO_Orden"]
    assert orden.json()["lineas"][0]["OCL_Precio_Unitario"]

    # La recepción la confirma TI; cerrar y borrar proveedores también.
    assert (await client.post(f"/api/v1/compras/ordenes/{oid}/recibir", headers=h, json={})).status_code == 403
    assert (await client.post(f"/api/v1/compras/ordenes/{oid}/cerrar", headers=h,
                              json={"motivo": "No llegará"})).status_code == 403
    assert (await client.delete(f"/api/v1/compras/proveedores/{prv.json()['PRV_Proveedor']}",
                                headers=h)).status_code == 403
    # Contabilidad la consulta, pero no modifica.
    c = por_rol["CONTABILIDAD"]
    assert (await client.get(f"/api/v1/compras/ordenes/{oid}", headers=c)).status_code == 200
    assert (await client.post("/api/v1/compras/proveedores", headers=c, json={"PRV_Nombre": "X SA"})).status_code == 403
    assert (await client.patch(f"/api/v1/compras/ordenes/{oid}/estado", headers=c,
                               json={"OCO_Estado": "CANCELADA"})).status_code == 403
    # El técnico ya no ve compras.
    assert (await client.get("/api/v1/compras/ordenes", headers=por_rol["TECNICO"])).status_code == 403


@pytest.mark.asyncio
async def test_busqueda_y_bandeja_sin_indicios_de_otros_modulos(client, por_rol, domain_seed):
    sa = por_rol["SUPER_ADMIN"]
    prv = await client.post("/api/v1/compras/proveedores", headers=sa, json={"PRV_Nombre": "Proveedor Test"})
    await client.post("/api/v1/compras/ordenes", headers=sa, json={
        "OCO_Numero": "OC-TEST-9", "OCO_Fecha": date.today().isoformat(), "PRV_Proveedor": prv.json()["PRV_Proveedor"],
        "SED_Sede": domain_seed["sede"], "lineas": []})

    async def tipos(rol: str) -> set[str]:
        r = await client.get("/api/v1/productividad/buscar?q=test", headers=por_rol[rol])
        assert r.status_code == 200, r.text
        return {x["tipo"] for x in r.json()["resultados"]}

    assert await tipos("SUPER_ADMIN") >= {"persona", "orden"}
    assert await tipos("COMPRAS") == {"orden"}
    assert "persona" not in await tipos("CONTABILIDAD") and "persona" not in await tipos("CONSULTA")
    assert "orden" not in await tipos("TECNICO")

    # Contabilidad ve el activo, pero no a su custodio.
    r = await client.get("/api/v1/productividad/buscar?q=LAP-001", headers=por_rol["CONTABILIDAD"])
    assert [x["custodio"] for x in r.json()["resultados"] if x["tipo"] == "activo"] == [None]

    assert (await client.get("/api/v1/inventario/atencion", headers=por_rol["CONSULTA"])).json()["items"] == []
    claves = {i["clave"] for i in (await client.get("/api/v1/inventario/atencion",
                                                     headers=por_rol["COMPRAS"])).json()["items"]}
    assert claves <= {"ORDEN_PARCIAL", "GARANTIA_POR_VENCER", "STOCK_BAJO"}
    assert (await client.get("/api/v1/inventario/atencion", headers=por_rol["ADMIN_SEGURIDAD"])).status_code == 403


@pytest.mark.asyncio
async def test_roles_de_administracion_se_asignan_y_validan(client, por_rol, domain_seed):
    r = await client.post("/api/v1/org/usuarios", headers=por_rol["ADMIN_SEGURIDAD"], json={
        "USU_Username": "compras.ana", "USU_Password": "Compras#2026x", "USU_Rol": "COMPRAS",
        "sedes": [domain_seed["sede"]], "PER_Persona": domain_seed["bob"]})
    assert r.status_code == 201, r.text
    assert r.json()["USU_Rol"] == "COMPRAS"

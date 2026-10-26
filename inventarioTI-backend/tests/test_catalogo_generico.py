"""Servicio genérico de catálogos: CRUD, filas de sistema, reorden y fusión de duplicados."""
from datetime import date

import pytest
from sqlalchemy import select

from app.models.catalogs import Marca, Modelo
from app.models.core import Activo
from app.models.governance import AuditoriaSistema
from app.models.software import Software

API = "/api/v1/catalogos"
pytestmark = pytest.mark.asyncio


async def _cuenta(client, session, sa_user, usuario: str, rol: str, *, sede: int | None = None) -> dict:
    """Crea una cuenta con rol (y sede, si no es global) e inicia su sesión."""
    from app.core.security import get_password_hash
    from app.models.organization import Persona, Usuario, UsuarioSede

    base = await session.get(Persona, sa_user.PER_Persona)
    persona = Persona(PER_Primer_Nombre=usuario, PER_Primer_Apellido="Test",
                      PER_Email_Corporativo=f"{usuario}@test.local",
                      DEP_Departamento=base.DEP_Departamento, CAR_Cargo=base.CAR_Cargo)
    session.add(persona)
    await session.flush()
    cuenta = Usuario(USU_Username=usuario, USU_Password_Hash=get_password_hash("TestPassw0rd!"),
                     USU_Rol=rol, PER_Persona=persona.PER_Persona, USU_Alcance_Global=sede is None)
    session.add(cuenta)
    await session.flush()
    if sede is not None:
        session.add(UsuarioSede(USU_Usuario=cuenta.USU_Usuario, SED_Sede=sede))
    await session.commit()
    r = await client.post("/api/v1/login/access-token", data={"username": usuario, "password": "TestPassw0rd!"},
                          headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_lista_catalogos_y_filas_de_sistema(client, auth_headers):
    r = await client.get(API, headers=auth_headers)
    assert r.status_code == 200
    claves = {c["clave"]: c for c in r.json()}
    assert {"motivos-movimiento", "motivos-baja", "destinos-baja", "unidades-medida", "tipos-area",
            "tipos-falla", "acciones-mantenimiento", "tipos-documento", "marcas"} <= set(claves)
    assert claves["motivos-movimiento"]["grupo"] == "MOVIMIENTOS"

    r = await client.get(f"{API}/motivos-baja", headers=auth_headers)
    motivos = r.json()
    assert [m["codigo"] for m in motivos][:2] == ["OBSOLESCENCIA", "DANO_IRREPARABLE"]
    assert all(m["sistema"] for m in motivos)
    otro = next(m for m in motivos if m["codigo"] == "OTRO")
    assert otro["extras"]["MBA_Pide_Detalle"] is True

    r = await client.get(f"{API}/no-existe", headers=auth_headers)
    assert r.status_code == 404 and r.json()["detail"] == "CATALOG_NOT_FOUND"


async def test_crear_editar_y_desactivar(client, auth_headers):
    r = await client.post(f"{API}/motivos-baja", headers=auth_headers,
                          json={"nombre": "Siniestro cubierto por seguro", "descripcion": "Con póliza"})
    assert r.status_code == 201, r.text
    item = r.json()
    # Código interno automático (la baja guarda el código en el kardex), dentro
    # del largo de la columna que lo guarda (ACT_Baja_Motivo: 30).
    assert item["codigo"] == "SINIESTRO_CUBIERTO_POR_SEGU" and item["sistema"] is False
    assert item["orden"] > 70

    # Duplicado sin distinguir mayúsculas ni espacios.
    r = await client.post(f"{API}/motivos-baja", headers=auth_headers, json={"nombre": "  siniestro CUBIERTO por seguro "})
    assert r.status_code == 409 and r.json()["detail"] == "CATALOG_ITEM_ALREADY_EXISTS"

    r = await client.patch(f"{API}/motivos-baja/{item['id']}", headers=auth_headers,
                           json={"nombre": "Siniestro con seguro", "extras": {"MBA_Pide_Detalle": True}})
    assert r.status_code == 200, r.text
    assert r.json()["nombre"] == "Siniestro con seguro" and r.json()["extras"]["MBA_Pide_Detalle"] is True
    assert r.json()["codigo"] == item["codigo"]  # el código no cambia al renombrar

    r = await client.patch(f"{API}/motivos-baja/{item['id']}", headers=auth_headers, json={"activo": False})
    assert r.status_code == 200 and r.json()["activo"] is False
    activos = (await client.get(f"{API}/motivos-baja", headers=auth_headers)).json()
    assert item["id"] not in [m["id"] for m in activos]
    todos = (await client.get(f"{API}/motivos-baja", params={"inactivos": True}, headers=auth_headers)).json()
    assert item["id"] in [m["id"] for m in todos]


async def test_filas_de_sistema_protegidas(client, auth_headers):
    motivos = (await client.get(f"{API}/motivos-baja", headers=auth_headers)).json()
    robo = next(m for m in motivos if m["codigo"] == "ROBO")
    r = await client.patch(f"{API}/motivos-baja/{robo['id']}", headers=auth_headers, json={"nombre": "Hurto"})
    assert r.status_code == 409 and r.json()["detail"] == "CATALOG_SYSTEM_ITEM"
    r = await client.patch(f"{API}/motivos-baja/{robo['id']}", headers=auth_headers, json={"activo": False})
    assert r.status_code == 409 and r.json()["detail"] == "CATALOG_SYSTEM_ITEM"
    # La descripción y el orden sí se pueden ajustar.
    r = await client.patch(f"{API}/motivos-baja/{robo['id']}", headers=auth_headers,
                           json={"descripcion": "Con denuncia", "orden": 5})
    assert r.status_code == 200 and r.json()["descripcion"] == "Con denuncia"


async def test_validacion_de_campos_propios(client, auth_headers):
    r = await client.post(f"{API}/motivos-movimiento", headers=auth_headers,
                          json={"nombre": "Auditoría", "extras": {"MMO_Aplica_A": ["ASIGNACION", "ROBAR"]}})
    assert r.status_code == 422 and r.json()["detail"] == "CATALOG_FIELD_INVALID:MMO_Aplica_A"
    r = await client.post(f"{API}/motivos-movimiento", headers=auth_headers,
                          json={"nombre": "Auditoría", "extras": {"UME_Simbolo": "x"}})
    assert r.status_code == 422 and r.json()["detail"] == "CATALOG_FIELD_NOT_ALLOWED:UME_Simbolo"
    r = await client.post(f"{API}/motivos-movimiento", headers=auth_headers,
                          json={"nombre": "Auditoría", "extras": {"MMO_Aplica_A": ["PRESTAMO", "ASIGNACION"]}})
    assert r.status_code == 201 and r.json()["extras"]["MMO_Aplica_A"] == ["ASIGNACION", "PRESTAMO"]


async def test_reordenar(client, auth_headers):
    tipos = (await client.get(f"{API}/tipos-area", headers=auth_headers)).json()
    ids = [t["id"] for t in reversed(tipos)]
    r = await client.post(f"{API}/tipos-area/reordenar", headers=auth_headers, json={"ids": ids})
    assert r.status_code == 204
    despues = (await client.get(f"{API}/tipos-area", headers=auth_headers)).json()
    assert [t["id"] for t in despues] == ids


async def test_solo_administradores_escriben(client, session, sa_user):
    h = await _cuenta(client, session, sa_user, "consulta1", "CONSULTA")
    assert (await client.get(f"{API}/tipos-falla", headers=h)).status_code == 200
    r = await client.post(f"{API}/tipos-falla", headers=h, json={"nombre": "Ventilador"})
    assert r.status_code == 403


async def test_fusionar_marcas_mueve_modelos_activos_y_software(client, auth_headers, domain_seed, session):
    kingston = Marca(MAR_Nombre="Kingston")
    kingstom = Marca(MAR_Nombre="Kingstom")
    session.add_all([kingston, kingstom])
    await session.flush()
    destino_dt = Modelo(MOD_Nombre="DataTraveler", MAR_Marca=kingston.MAR_Marca)
    origen_dt = Modelo(MOD_Nombre="datatraveler ", MAR_Marca=kingstom.MAR_Marca)   # duplicado del destino
    origen_a400 = Modelo(MOD_Nombre="A400", MAR_Marca=kingstom.MAR_Marca)        # solo existe en el origen
    session.add_all([destino_dt, origen_dt, origen_a400])
    await session.flush()
    usb = Activo(ACT_Codigo_Interno="USB-001", ACT_Serie_Fabricante="USB-S1", ACT_Fecha_Compra=date(2024, 1, 1), MOD_Modelo=origen_dt.MOD_Modelo,
                 TAC_Tipo_Activo=domain_seed["tac_lap"], EOP_Estado_Operativo=domain_seed["eop_disp"])
    ssd = Activo(ACT_Codigo_Interno="SSD-001", ACT_Serie_Fabricante="SSD-S1", ACT_Fecha_Compra=date(2024, 1, 1), MOD_Modelo=origen_a400.MOD_Modelo,
                 TAC_Tipo_Activo=domain_seed["tac_lap"], EOP_Estado_Operativo=domain_seed["eop_disp"])
    sof = Software(SOF_Nombre="Kingston SSD Manager", SOF_Fabricante="Kingstom", MAR_Marca=kingstom.MAR_Marca)
    session.add_all([usb, ssd, sof])
    await session.commit()
    o, d = kingstom.MAR_Marca, kingston.MAR_Marca
    usb_id, sof_id = usb.ACT_Activo, sof.SOF_Software
    dt_destino, dt_origen, a400 = destino_dt.MOD_Modelo, origen_dt.MOD_Modelo, origen_a400.MOD_Modelo

    r = await client.get(f"{API}/marcas/{o}/fusionar/{d}", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["conteos"] == {"INV_MODELO.MAR_Marca": 2, "INV_SOFTWARE.MAR_Marca": 1, "INV_ACTIVO.MAR_Marca": 2}

    r = await client.post(f"{API}/marcas/{o}/fusionar/{d}", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["modelos_unidos"] == 1

    session.expire_all()
    # El activo del modelo duplicado pasa al modelo del destino; el otro modelo cambia de marca.
    assert (await session.get(Activo, usb_id)).MOD_Modelo == dt_destino
    assert await session.get(Modelo, dt_origen) is None
    assert (await session.get(Modelo, a400)).MAR_Marca == d
    s = await session.get(Software, sof_id)
    assert s.MAR_Marca == d and s.SOF_Fabricante == "Kingston"
    # El origen queda inactivo con la referencia al destino.
    m = await session.get(Marca, o)
    assert m.MAR_Activo is False and m.MAR_Fusionado_En == d and m.MAR_Fusionado_Fecha is not None
    aud = (await session.execute(
        select(AuditoriaSistema).where(AuditoriaSistema.AUD_Accion == "CATALOG_MERGE")
    )).scalar_one()
    assert aud.AUD_Snapshot_JSON["origen_nombre"] == "Kingstom" and aud.AUD_Snapshot_JSON["destino"] == d

    # Un registro fusionado no se reactiva ni se vuelve a fusionar.
    r = await client.patch(f"{API}/marcas/{o}", headers=auth_headers, json={"activo": True})
    assert r.status_code == 409 and r.json()["detail"] == "CATALOG_ITEM_MERGED"
    r = await client.post(f"{API}/marcas/{o}/fusionar/{d}", headers=auth_headers)
    assert r.status_code == 409 and r.json()["detail"] == "CATALOG_ITEM_MERGED"


async def test_fusion_reglas(client, auth_headers):
    motivos = (await client.get(f"{API}/motivos-movimiento", headers=auth_headers)).json()
    otro = next(m for m in motivos if m["codigo"] == "OTRO")
    renov = next(m for m in motivos if m["codigo"] == "RENOVACION")
    nuevo = (await client.post(f"{API}/motivos-movimiento", headers=auth_headers, json={"nombre": "Renovacion"})).json()

    r = await client.post(f"{API}/motivos-movimiento/{nuevo['id']}/fusionar/{nuevo['id']}", headers=auth_headers)
    assert r.status_code == 422 and r.json()["detail"] == "CATALOG_MERGE_SAME_ITEM"
    # Un registro de sistema no se absorbe (la lógica depende de su código).
    r = await client.post(f"{API}/motivos-movimiento/{otro['id']}/fusionar/{renov['id']}", headers=auth_headers)
    assert r.status_code == 409 and r.json()["detail"] == "CATALOG_SYSTEM_ITEM"
    # Nunca hacia un destino inactivo.
    destino = (await client.post(f"{API}/motivos-movimiento", headers=auth_headers, json={"nombre": "Viejo"})).json()
    await client.patch(f"{API}/motivos-movimiento/{destino['id']}", headers=auth_headers, json={"activo": False})
    r = await client.post(f"{API}/motivos-movimiento/{nuevo['id']}/fusionar/{destino['id']}", headers=auth_headers)
    assert r.status_code == 409 and r.json()["detail"] == "CATALOG_MERGE_TARGET_INACTIVE"
    # Hacia uno de sistema, sí.
    r = await client.post(f"{API}/motivos-movimiento/{nuevo['id']}/fusionar/{renov['id']}", headers=auth_headers)
    assert r.status_code == 200, r.text


async def test_fusion_exige_alcance_global(client, session, sa_user, domain_seed, auth_headers):
    h = await _cuenta(client, session, sa_user, "admin.sede", "ADMIN_TI", sede=domain_seed["sede"])
    a = (await client.post(f"{API}/tipos-falla", headers=h, json={"nombre": "Bisagra"})).json()
    b = (await client.post(f"{API}/tipos-falla", headers=h, json={"nombre": "Bisagras"})).json()
    r = await client.post(f"{API}/tipos-falla/{b['id']}/fusionar/{a['id']}", headers=h)
    assert r.status_code == 403 and r.json()["detail"] == "CATALOG_MERGE_REQUIRES_GLOBAL_SCOPE"
    r = await client.post(f"{API}/tipos-falla/{b['id']}/fusionar/{a['id']}", headers=auth_headers)
    assert r.status_code == 200


def test_rls_sin_columna_de_licencia_en_adjuntos():
    """Las migraciones anteriores a A1 reinstalan RLS sin INV_ADJUNTO.LIC_Licencia (regresión del despliegue)."""
    from app.db.rls import _policies
    assert "LIC_Licencia" not in _policies(False)["INV_ADJUNTO"][0][2]
    assert "LIC_Licencia" in _policies(True)["INV_ADJUNTO"][0][2]

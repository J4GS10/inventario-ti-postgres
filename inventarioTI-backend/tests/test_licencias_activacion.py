"""
Licencias con activación variable (plan §3.4): cuentas por persona, product
keys por equipo, servidor concurrente, serial + key, por sitio y un método
creado por el usuario; secretos cifrados, revelar auditado, rotación,
salida del empleado y entrega de puesto con licencias.
"""
from datetime import date

import pytest
from sqlalchemy import select

from app.models.core import Activo
from app.models.governance import AuditoriaSistema
from app.models.kardex import Kardex
from app.models.software import LicenciaClave, Software

API = "/api/v1"
pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _cifrado_real(monkeypatch):
    """Como en producción (donde FIELD_ENCRYPTION_KEY es obligatorio): secretos cifrados con Fernet."""
    from cryptography.fernet import Fernet
    from app.core import security
    from app.core.config import settings
    monkeypatch.setattr(settings, "FIELD_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(security, "_fernet", None)
    yield
    security._fernet = None


async def _cat(client, h, clave, codigo) -> int:
    items = (await client.get(f"{API}/catalogos/{clave}", headers=h)).json()
    return next(i["id"] for i in items if i["codigo"] == codigo)


async def _licencia(client, h, sof: int, modelo: str, metodo: str, forma: str, total: int, credenciales=None) -> dict:
    r = await client.post(f"{API}/soft/licencias", headers=h, json={
        "SOF_Software": sof, "LIC_Cantidad_Total": total,
        "MCO_Modelo_Comercial": await _cat(client, h, "modelos-comerciales", modelo),
        "MAC_Metodo_Activacion": await _cat(client, h, "metodos-activacion", metodo),
        "FCO_Forma_Conteo": await _cat(client, h, "formas-conteo", forma),
        "LIC_Credenciales": credenciales or [],
    })
    assert r.status_code == 201, r.text
    return r.json()


async def _software(session, nombre: str) -> int:
    sw = Software(SOF_Nombre=nombre, SOF_Fabricante="QA")
    session.add(sw)
    await session.commit()
    return sw.SOF_Software


async def _activos(session, seed, n: int, prefijo: str) -> list[str]:
    ids = []
    for i in range(n):
        a = Activo(ACT_Codigo_Interno=f"{prefijo}-{i}", ACT_Serie_Fabricante=f"{prefijo}-S{i}",
                   ACT_Fecha_Compra=date(2024, 1, 1), MOD_Modelo=seed["mod"], TAC_Tipo_Activo=seed["tac_lap"],
                   EOP_Estado_Operativo=seed["eop_disp"])
        session.add(a)
        await session.flush()
        ids.append(str(a.ACT_Activo))
    await session.commit()
    return ids


async def _instalar(client, h, lic: int, *, activo=None, persona=None):
    return await client.post(f"{API}/soft/instalaciones", headers=h, json={
        "LIC_Licencia": lic, "ACT_Activo": activo, "PER_Persona": persona})


async def test_microsoft_365_cuentas_por_persona(client, auth_headers, domain_seed, session):
    sof = await _software(session, "Microsoft 365")
    cuentas = [{"usuario": f"m365.{i}@lombardi.test", "secreto": f"Clave-Secreta-{i}!"} for i in range(10)]
    lic = await _licencia(client, auth_headers, sof, "SUSCRIPCION", "CUENTA", "PERSONA", 10, cuentas)

    # El listado no trae los secretos.
    r = await client.get(f"{API}/soft/licencias/{lic['LIC_Licencia']}/credenciales", headers=auth_headers)
    assert r.status_code == 200 and len(r.json()) == 10
    assert "Clave-Secreta" not in r.text and all(c["tiene_secreto"] for c in r.json())
    # Ni la base los guarda en claro.
    filas = (await session.execute(select(LicenciaClave.LCL_Secreto_Enc))).scalars().all()
    assert filas and all("Clave-Secreta" not in (f or "") for f in filas)

    # Por persona: se asigna a una persona (con su cuenta), nunca a un equipo.
    r = await _instalar(client, auth_headers, lic["LIC_Licencia"], activo=domain_seed["act_1"])
    assert r.status_code == 400 and r.json()["detail"] == "LICENSE_COUNTS_BY_PERSON"
    r = await _instalar(client, auth_headers, lic["LIC_Licencia"], persona=domain_seed["alice"])
    assert r.status_code == 201, r.text
    assert r.json()["LCL_Licencia_Clave"] is not None

    # Ni el CSV de licencias ni la cabecera revelan nada.
    r = await client.get(f"{API}/inventario/licencias.csv", headers=auth_headers)
    assert "Clave-Secreta" not in r.text


async def test_validacion_por_metodo(client, auth_headers, session):
    sof = await _software(session, "Windows 11 Pro")
    lic = await _licencia(client, auth_headers, sof, "OEM", "PRODUCT_KEY", "EQUIPO", 3)
    url = f"{API}/soft/licencias/{lic['LIC_Licencia']}/credenciales"
    r = await client.post(url, headers=auth_headers, json={"credenciales": [{"usuario": "a@b.c", "clave": "AAAAA-11111"}]})
    assert r.status_code == 422 and r.json()["detail"] == "LICENSE_FIELD_NOT_ALLOWED:usuario"
    r = await client.post(url, headers=auth_headers, json={"credenciales": [{"referencia": "sin key"}]})
    assert r.status_code == 422 and r.json()["detail"] == "LICENSE_FIELD_REQUIRED:clave"
    r = await client.post(url, headers=auth_headers, json={"credenciales": [{"clave": "AAAAA-11111-BBBBB"}]})
    assert r.status_code == 201 and r.json()[0]["clave_mascara"] == "•••• BBBBB"
    # La misma key no se registra dos veces (ni en otra licencia).
    otra = await _licencia(client, auth_headers, sof, "OEM", "PRODUCT_KEY", "EQUIPO", 1)
    r = await client.post(f"{API}/soft/licencias/{otra['LIC_Licencia']}/credenciales", headers=auth_headers,
                          json={"credenciales": [{"clave": "AAAAA-11111-BBBBB"}]})
    assert r.status_code == 409 and r.json()["detail"] == "LICENSE_KEY_ALREADY_EXISTS"
    # No más credenciales que cupos comprados.
    r = await client.post(url, headers=auth_headers, json={"credenciales": [{"clave": f"K-{i}"} for i in range(3)]})
    assert r.status_code == 409 and r.json()["detail"] == "LICENSE_CREDENTIALS_EXCEED_SEATS:3"


async def test_windows_por_equipo_y_antivirus_serial_key(client, auth_headers, domain_seed, session):
    sof = await _software(session, "Antivirus")
    lic = await _licencia(client, auth_headers, sof, "VOLUMEN", "SERIAL_KEY", "EQUIPO", 2,
                          [{"serial": "AV-001", "clave": "AV-KEY-001"}, {"serial": "AV-002", "clave": "AV-KEY-002"}])
    r = await _instalar(client, auth_headers, lic["LIC_Licencia"], persona=domain_seed["alice"])
    assert r.status_code == 400 and r.json()["detail"] == "LICENSE_COUNTS_BY_DEVICE"
    r = await _instalar(client, auth_headers, lic["LIC_Licencia"], activo=domain_seed["act_1"])
    assert r.status_code == 201 and r.json()["LCL_Licencia_Clave"] is not None


async def test_servidor_concurrente_tres_usos(client, auth_headers, domain_seed, session):
    sof = await _software(session, "ETABS")
    lic = await _licencia(client, auth_headers, sof, "PERPETUA", "SERVIDOR", "CONCURRENTE", 3,
                          [{"servidor": "licencias.lombardi.local:27000"}])
    activos = await _activos(session, domain_seed, 4, "ING")
    for a in activos[:3]:
        r = await _instalar(client, auth_headers, lic["LIC_Licencia"], activo=a)
        assert r.status_code == 201, r.text
        assert r.json()["LCL_Licencia_Clave"] is None  # la credencial del servidor se comparte
    r = await _instalar(client, auth_headers, lic["LIC_Licencia"], activo=activos[3])
    assert r.status_code == 409 and r.json()["detail"] == "NO_LICENSE_SEATS_AVAILABLE"


async def test_por_sitio_no_descuenta_cupos(client, auth_headers, domain_seed, session):
    sof = await _software(session, "Lector PDF")
    lic = await _licencia(client, auth_headers, sof, "GRATUITA", "SIN_CREDENCIAL", "SITIO", 1)
    for a in await _activos(session, domain_seed, 3, "PDF"):
        assert (await _instalar(client, auth_headers, lic["LIC_Licencia"], activo=a)).status_code == 201
    r = await client.get(f"{API}/soft/licencias/{lic['LIC_Licencia']}", headers=auth_headers)
    assert r.json()["LIC_Cantidad_Usada"] == 0
    r = await client.post(f"{API}/soft/licencias/{lic['LIC_Licencia']}/credenciales", headers=auth_headers,
                          json={"credenciales": [{"clave": "X"}]})
    assert r.status_code == 422 and r.json()["detail"] == "LICENSE_METHOD_HAS_NO_CREDENTIALS"


async def test_metodo_nuevo_sin_cambiar_codigo(client, auth_headers, session):
    r = await client.post(f"{API}/catalogos/metodos-activacion", headers=auth_headers, json={
        "nombre": "Código de suscripción + PIN",
        "extras": {"MAC_Pide_Serial": True, "MAC_Pide_Secreto": True,
                   "MAC_Etiqueta_Serial": "Código de suscripción", "MAC_Etiqueta_Secreto": "PIN"},
    })
    assert r.status_code == 201, r.text
    metodo = r.json()
    sof = await _software(session, "Servicio QA")
    r = await client.post(f"{API}/soft/licencias", headers=auth_headers, json={
        "SOF_Software": sof, "LIC_Cantidad_Total": 2,
        "MCO_Modelo_Comercial": await _cat(client, auth_headers, "modelos-comerciales", "SUSCRIPCION"),
        "MAC_Metodo_Activacion": metodo["id"],
        "FCO_Forma_Conteo": await _cat(client, auth_headers, "formas-conteo", "PERSONA"),
        "LIC_Credenciales": [{"serial": "SUB-001"}],
    })
    assert r.status_code == 422 and r.json()["detail"] == "LICENSE_FIELD_REQUIRED:secreto"
    r = await client.post(f"{API}/soft/licencias", headers=auth_headers, json={
        "SOF_Software": sof, "LIC_Cantidad_Total": 2,
        "MCO_Modelo_Comercial": await _cat(client, auth_headers, "modelos-comerciales", "SUSCRIPCION"),
        "MAC_Metodo_Activacion": metodo["id"],
        "FCO_Forma_Conteo": await _cat(client, auth_headers, "formas-conteo", "PERSONA"),
        "LIC_Credenciales": [{"serial": "SUB-001", "secreto": "4321"}],
    })
    assert r.status_code == 201, r.text
    # La forma de conteo es cerrada: la lógica depende de sus cuatro códigos.
    r = await client.post(f"{API}/catalogos/formas-conteo", headers=auth_headers, json={"nombre": "Por proyecto"})
    assert r.status_code == 409 and r.json()["detail"] == "CATALOG_CLOSED"


async def test_revelar_auditado_y_rotar(client, auth_headers, domain_seed, session):
    from tests.test_catalogo_generico import _cuenta
    sof = await _software(session, "Adobe")
    lic = await _licencia(client, auth_headers, sof, "SUSCRIPCION", "CUENTA", "PERSONA", 1,
                          [{"usuario": "adobe@lombardi.test", "secreto": "Primera-Clave1"}])
    cred = (await client.get(f"{API}/soft/licencias/{lic['LIC_Licencia']}/credenciales", headers=auth_headers)).json()[0]
    cid = cred["LCL_Licencia_Clave"]

    tecnico = await _cuenta(client, session, await _sa(session), "tecnico1", "TECNICO")
    assert (await client.post(f"{API}/soft/credenciales/{cid}/revelar", headers=tecnico)).status_code == 403

    r = await client.post(f"{API}/soft/credenciales/{cid}/revelar", headers=auth_headers)
    assert r.status_code == 200 and r.json()["secreto"] == "Primera-Clave1" and r.json()["ocultar_en_segundos"] == 30
    aud = (await session.execute(select(AuditoriaSistema).where(
        AuditoriaSistema.AUD_Accion == "LICENSE_SECRET_VIEWED"))).scalar_one()
    assert "Primera-Clave1" not in str(aud.AUD_Snapshot_JSON)
    assert (await session.execute(select(Kardex).where(Kardex.KDX_Tipo == "LICENCIA_SECRETO_VISTO"))).scalar_one()

    r = await client.post(f"{API}/soft/credenciales/{cid}/rotar", headers=auth_headers, json={"clave": "X"})
    assert r.status_code == 422 and r.json()["detail"] == "LICENSE_FIELD_NOT_ALLOWED:clave"
    r = await client.post(f"{API}/soft/credenciales/{cid}/rotar", headers=auth_headers, json={"secreto": "Segunda-Clave2"})
    assert r.status_code == 204
    r = await client.post(f"{API}/soft/credenciales/{cid}/revelar", headers=auth_headers)
    assert r.json()["secreto"] == "Segunda-Clave2"
    assert (await session.execute(select(Kardex).where(Kardex.KDX_Tipo == "LICENCIA_SECRETO_CAMBIADO"))).scalar_one()


async def _sa(session):
    from app.models.organization import Usuario
    return (await session.execute(select(Usuario).where(Usuario.USU_Username == "sa"))).scalar_one()


async def test_salida_libera_cuentas_y_pide_cambio_de_clave(client, auth_headers, domain_seed, session):
    sof = await _software(session, "Microsoft 365 E3")
    lic = await _licencia(client, auth_headers, sof, "SUSCRIPCION", "CUENTA", "PERSONA", 2,
                          [{"usuario": "bob@lombardi.test", "secreto": "Clave-Bob-1"}])
    r = await _instalar(client, auth_headers, lic["LIC_Licencia"], persona=domain_seed["bob"])
    assert r.status_code == 201
    r = await client.post(f"{API}/trazabilidad/persona/{domain_seed['bob']}/offboarding", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["cuentas_por_cambiar"] == 1

    cred = (await client.get(f"{API}/soft/licencias/{lic['LIC_Licencia']}/credenciales", headers=auth_headers)).json()[0]
    assert cred["estado"] == "DISPONIBLE" and cred["requiere_cambio"] is True
    bandeja = (await client.get(f"{API}/inventario/atencion", headers=auth_headers)).json()["items"]
    pendiente = next(i for i in bandeja if i["clave"] == "CUENTA_POR_CAMBIAR")
    assert pendiente["items"][0]["usuario"] == "bob@lombardi.test"

    # Al cambiar la clave, el pendiente desaparece.
    await client.post(f"{API}/soft/credenciales/{cred['LCL_Licencia_Clave']}/rotar", headers=auth_headers,
                      json={"secreto": "Clave-Nueva-2"})
    bandeja = (await client.get(f"{API}/inventario/atencion", headers=auth_headers)).json()["items"]
    assert not any(i["clave"] == "CUENTA_POR_CAMBIAR" for i in bandeja)


async def test_entrega_de_puesto_con_licencia(client, auth_headers, domain_seed, session):
    sof = await _software(session, "Microsoft 365 Business")
    lic = await _licencia(client, auth_headers, sof, "SUSCRIPCION", "CUENTA", "PERSONA", 1,
                          [{"usuario": "alice@lombardi.test", "secreto": "Clave-Alice"}])
    r = await client.post(f"{API}/trazabilidad/entrega-lote", headers=auth_headers, json={
        "PER_Persona": domain_seed["alice"], "ARE_Area": domain_seed["area"],
        "activos": [domain_seed["act_1"]], "licencias": [lic["LIC_Licencia"]],
    })
    assert r.status_code == 201, r.text
    assert r.json()["licencias"][0]["LCL_Licencia_Clave"] is not None
    # Sin cupos: toda la entrega se revierte (el equipo tampoco queda asignado).
    r = await client.post(f"{API}/trazabilidad/entrega-lote", headers=auth_headers, json={
        "PER_Persona": domain_seed["bob"], "ARE_Area": domain_seed["area"],
        "activos": [domain_seed["act_2"]], "licencias": [lic["LIC_Licencia"]],
    })
    assert r.status_code == 409 and r.json()["detail"].startswith(f"BATCH_ITEM_FAILED:LIC-{lic['LIC_Licencia']}:")
    act2 = (await client.get(f"{API}/core/activos/{domain_seed['act_2']}/contexto", headers=auth_headers)).json()
    assert not act2.get("custodio")


async def test_tipo_anterior_sigue_funcionando(client, auth_headers, software_seed):
    r = await client.post(f"{API}/soft/licencias", headers=auth_headers, json={
        "SOF_Software": software_seed["sof"], "TLI_Tipo_Licencia": software_seed["tli"], "LIC_Cantidad_Total": 2,
        "LIC_Claves": [{"LCL_Clave_Activacion": "OLD-1"}, {"LCL_Clave_Activacion": "OLD-22222"}],
    })
    assert r.status_code == 201, r.text
    r = await client.get(f"{API}/soft/licencias/{r.json()['LIC_Licencia']}/claves", headers=auth_headers)
    assert "OLD-22222" not in r.text  # el listado anterior también se enmascara
    r = await client.post(f"{API}/soft/licencias", headers=auth_headers, json={
        "SOF_Software": software_seed["sof"], "LIC_Cantidad_Total": 1})
    assert r.status_code == 422


async def test_licencias_disponibles_por_forma(client, auth_headers, domain_seed, session):
    sof = await _software(session, "Office por persona")
    lic = await _licencia(client, auth_headers, sof, "SUSCRIPCION", "CUENTA", "PERSONA", 1,
                          [{"usuario": "x@lombardi.test", "secreto": "x-1"}])
    otra = await _licencia(client, auth_headers, sof, "OEM", "PRODUCT_KEY", "EQUIPO", 1)
    r = await client.get(f"{API}/soft/licencias-disponibles", params={"forma": "PERSONA"}, headers=auth_headers)
    ids = [x["LIC_Licencia"] for x in r.json()]
    assert lic["LIC_Licencia"] in ids and otra["LIC_Licencia"] not in ids
    # Sin cupos libres deja de ofrecerse.
    await _instalar(client, auth_headers, lic["LIC_Licencia"], persona=domain_seed["alice"])
    r = await client.get(f"{API}/soft/licencias-disponibles", params={"forma": "PERSONA"}, headers=auth_headers)
    assert lic["LIC_Licencia"] not in [x["LIC_Licencia"] for x in r.json()]


async def test_alta_de_software_queda_guardada(client, auth_headers, session):
    """La alta se confirma en la base (se lee desde otra sesión), con el fabricante como marca."""
    r = await client.post(f"{API}/soft/software", headers=auth_headers,
                          json={"SOF_Nombre": "Visor QA", "SOF_Fabricante": "Microsoft"})
    assert r.status_code == 201, r.text
    fila = (await session.execute(select(Software).where(Software.SOF_Nombre == "Visor QA"))).scalar_one_or_none()
    assert fila is not None and fila.SOF_Fabricante == "Microsoft"

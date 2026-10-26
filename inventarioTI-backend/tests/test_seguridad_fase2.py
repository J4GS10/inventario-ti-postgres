"""
Fase 2 — seguridad (regresiones de la QA del 2026-09-28).

  - A10: ADMIN_SEGURIDAD no ve inventario por /org.
  - M7: ADMIN_SEGURIDAD no cambia el rol de una cuenta con alcance global.
  - M8: el rol restringido de PostgreSQL no puede alterar rol/alcance/sedes.
  - A11: el anti-replay TOTP es atómico (verificaciones simultáneas).
  - M9: reusar un TOTP válido no suma al bloqueo; un bloqueo vencido reinicia el contador.
  - M10: el challenge 2FA es de un solo uso y se invalida al cambiar la contraseña.
  - M11: una cuenta bloqueada no se distingue de una inexistente sin la contraseña.
  - A12: el contenido del adjunto debe coincidir con su extensión; MIME del servidor.
  - A13: el código 2FA no va en el asunto del correo.
  - Las respuestas de login no se guardan en caché.
"""
import asyncio
import time
import uuid
from datetime import datetime, timedelta

import pyotp
import pytest
from sqlalchemy import select, text, update

from app.core.security import get_password_hash
from app.models.organization import Persona, Usuario, UsuarioSede

FORM = {"Content-Type": "application/x-www-form-urlencoded"}
PWD = "Clave-Segura-2026"
BASE = "/api/v1"


async def _mk_user(session, username, role, *, sedes=(), global_=False):
    ref = (await session.execute(select(Persona))).scalars().first()
    per = Persona(PER_Primer_Nombre=username.title(), PER_Primer_Apellido="Fase2",
                  PER_Email_Corporativo=f"{username}@empresa.local",
                  DEP_Departamento=ref.DEP_Departamento, CAR_Cargo=ref.CAR_Cargo,
                  SED_Sede=sedes[0] if sedes else None)
    session.add(per)
    await session.flush()
    usu = Usuario(USU_Username=username, USU_Password_Hash=get_password_hash(PWD), USU_Rol=role,
                  PER_Persona=per.PER_Persona, USU_Alcance_Global=global_)
    session.add(usu)
    await session.flush()
    for s in sedes:
        session.add(UsuarioSede(USU_Usuario=usu.USU_Usuario, SED_Sede=s))
    await session.commit()
    return usu, per


async def _login(client, username, password=PWD):
    return await client.post(f"{BASE}/login/access-token",
                             data={"username": username, "password": password}, headers=FORM)


async def _h(client, username):
    r = await _login(client, username)
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _con_totp(client, username):
    """Enrola TOTP para `username` y devuelve el secreto."""
    h = await _h(client, username)
    secret = (await client.post(f"{BASE}/me/2fa/totp/setup", headers=h)).json()["secret"]
    r = await client.post(f"{BASE}/me/2fa/totp/activate", headers=h, json={"code": pyotp.TOTP(secret).now()})
    assert r.status_code == 200, r.text
    return secret


def _siguiente(secret, pasos=1):
    return pyotp.TOTP(secret).at(time.time() + 30 * pasos)


# --------------------------------------------------------------------------
# Separación de funciones y jerarquía
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_admin_seguridad_no_ve_inventario_por_org(client, session, domain_seed):
    await _mk_user(session, "seg_org", "ADMIN_SEGURIDAD")
    h = await _h(client, "seg_org")
    assert (await client.get(f"{BASE}/org/departamentos/resumen", headers=h)).status_code == 403
    assert (await client.get(f"{BASE}/org/departamentos/1/detalle", headers=h)).status_code == 403
    ctx = await client.get(f"{BASE}/org/personas/{domain_seed['alice']}/contexto", headers=h)
    assert ctx.status_code == 200, ctx.text
    assert ctx.json()["activos_vigentes"] == []
    assert ctx.json()["area_sugerida"] is None


@pytest.mark.asyncio
async def test_seguridad_no_cambia_rol_de_cuenta_global(client, session, domain_seed):
    await _mk_user(session, "seg_rol", "ADMIN_SEGURIDAD")
    objetivo, _ = await _mk_user(session, "consulta_global", "CONSULTA", global_=True)
    h = await _h(client, "seg_rol")
    r = await client.patch(f"{BASE}/org/usuarios/{objetivo.USU_Usuario}", headers=h, json={"USU_Rol": "ADMIN_TI"})
    assert r.status_code == 403
    assert r.json()["detail"] == "ONLY_SUPER_ADMIN_CAN_CHANGE_ROLE_OF_GLOBAL_ACCOUNT"
    # Quitando el global en el mismo cambio sí puede (queda acotado a sedes).
    r = await client.patch(f"{BASE}/org/usuarios/{objetivo.USU_Usuario}", headers=h, json={
        "USU_Rol": "ADMIN_TI", "USU_Alcance_Global": False, "sedes": [domain_seed["sede"]]})
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_rol_restringido_no_altera_identidades(session, domain_seed):
    """Aunque una petición sufriera una inyección SQL, no podría darse alcance global."""
    from app.core.config import settings
    tec, _ = await _mk_user(session, "tec_sql", "TECNICO", sedes=[domain_seed["sede"]])
    uid = tec.USU_Usuario
    for sql in (
        f'UPDATE "INV_USUARIO" SET "USU_Alcance_Global" = true WHERE "USU_Usuario" = \'{uid}\'',
        f'UPDATE "INV_USUARIO" SET "USU_Rol" = \'SUPER_ADMIN\' WHERE "USU_Usuario" = \'{uid}\'',
        f'INSERT INTO "INV_USUARIO_SEDE" ("USU_Usuario", "SED_Sede") VALUES (\'{uid}\', {domain_seed["sede"]})',
    ):
        await session.execute(text(f'SET LOCAL ROLE "{settings.DB_RLS_ROLE}"'))
        await session.execute(text("SELECT set_config('app.usuario', :u, true)"), {"u": str(uid)})
        with pytest.raises(Exception) as exc:
            await session.execute(text(sql))
        assert "permission denied" in str(exc.value).lower()
        await session.rollback()
    # El autoservicio sí puede actualizar columnas no sensibles.
    await session.execute(text(f'SET LOCAL ROLE "{settings.DB_RLS_ROLE}"'))
    await session.execute(text("SELECT set_config('app.usuario', :u, true)"), {"u": str(uid)})
    await session.execute(text(
        f'UPDATE "INV_USUARIO" SET "USU_Intentos_Fallidos" = 0 WHERE "USU_Usuario" = \'{uid}\''))
    await session.rollback()


# --------------------------------------------------------------------------
# MFA y login
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_totp_simultaneo_abre_una_sola_sesion(client, session, domain_seed):
    await _mk_user(session, "mfa_carrera", "TECNICO", global_=True)
    secret = await _con_totp(client, "mfa_carrera")
    codigo = _siguiente(secret)
    challenges = [(await _login(client, "mfa_carrera")).json()["challenge_token"] for _ in range(4)]
    respuestas = await asyncio.gather(*[
        client.post(f"{BASE}/login/2fa/verify", json={"challenge_token": c, "code": codigo})
        for c in challenges
    ])
    assert sum(1 for r in respuestas if r.status_code == 200) == 1, [r.text for r in respuestas]


@pytest.mark.asyncio
async def test_reuso_de_totp_no_bloquea(client, session, domain_seed):
    usu, _ = await _mk_user(session, "mfa_reuso", "TECNICO", global_=True)
    uid = usu.USU_Usuario
    secret = await _con_totp(client, "mfa_reuso")
    codigo = _siguiente(secret)
    ch = (await _login(client, "mfa_reuso")).json()["challenge_token"]
    assert (await client.post(f"{BASE}/login/2fa/verify", json={"challenge_token": ch, "code": codigo})).status_code == 200
    for _ in range(6):  # más que el umbral de bloqueo
        ch = (await _login(client, "mfa_reuso")).json()["challenge_token"]
        r = await client.post(f"{BASE}/login/2fa/verify", json={"challenge_token": ch, "code": codigo})
        assert r.status_code == 400
        assert r.json()["detail"] == "TOTP_ALREADY_USED"
    session.expire_all()
    fresco = (await session.execute(select(Usuario).where(Usuario.USU_Usuario == uid))).scalar_one()
    assert not fresco.USU_Intentos_Fallidos
    assert fresco.USU_Bloqueado_Hasta is None


@pytest.mark.asyncio
async def test_challenge_de_un_solo_uso_y_ligado_a_la_clave(client, session, domain_seed):
    usu, _ = await _mk_user(session, "mfa_ch", "TECNICO", global_=True)
    secret = await _con_totp(client, "mfa_ch")
    ch = (await _login(client, "mfa_ch")).json()["challenge_token"]
    assert (await client.post(f"{BASE}/login/2fa/verify",
                              json={"challenge_token": ch, "code": _siguiente(secret, 1)})).status_code == 200
    r = await client.post(f"{BASE}/login/2fa/verify", json={"challenge_token": ch, "code": _siguiente(secret, 2)})
    assert r.status_code == 401

    # Un challenge emitido antes de un cambio de contraseña deja de servir.
    ch = (await _login(client, "mfa_ch")).json()["challenge_token"]
    await session.execute(update(Usuario).where(Usuario.USU_Usuario == usu.USU_Usuario)
                          .values(USU_Password_Hash=get_password_hash("Otra-Clave-2026-x")))
    await session.commit()
    r = await client.post(f"{BASE}/login/2fa/verify", json={"challenge_token": ch, "code": _siguiente(secret, 3)})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_bloqueo_no_revela_la_cuenta(client, session, domain_seed):
    usu, _ = await _mk_user(session, "bloq_enum", "CONSULTA", global_=True)
    await session.execute(update(Usuario).where(Usuario.USU_Usuario == usu.USU_Usuario).values(
        USU_Bloqueado_Hasta=datetime.utcnow() + timedelta(minutes=10), USU_Intentos_Fallidos=5))
    await session.commit()
    mala = await _login(client, "bloq_enum", "clave-equivocada")
    inexistente = await _login(client, "no_existe_nadie", "clave-equivocada")
    assert mala.status_code == inexistente.status_code == 400
    assert mala.json() == inexistente.json()
    # Con la contraseña correcta se informa el bloqueo.
    assert (await _login(client, "bloq_enum")).status_code == 429


@pytest.mark.asyncio
async def test_bloqueo_vencido_reinicia_contador(client, session, domain_seed):
    usu, _ = await _mk_user(session, "bloq_venc", "CONSULTA", global_=True)
    uid = usu.USU_Usuario
    await session.execute(update(Usuario).where(Usuario.USU_Usuario == usu.USU_Usuario).values(
        USU_Bloqueado_Hasta=datetime.utcnow() - timedelta(minutes=1), USU_Intentos_Fallidos=5))
    await session.commit()
    assert (await _login(client, "bloq_venc", "clave-equivocada")).status_code == 400
    session.expire_all()
    fresco = (await session.execute(select(Usuario).where(Usuario.USU_Usuario == uid))).scalar_one()
    assert fresco.USU_Intentos_Fallidos == 1  # no vuelve a bloquear con un solo fallo
    assert fresco.USU_Bloqueado_Hasta is None


@pytest.mark.asyncio
async def test_login_sin_cache(client, sa_user):
    r = await client.post(f"{BASE}/login/access-token",
                          data={"username": "sa", "password": "TestPassw0rd!"}, headers=FORM)
    assert r.headers.get("cache-control") == "no-store"


def test_codigo_2fa_no_va_en_el_asunto():
    from app.core.email import _render
    asunto, cuerpo = _render("2fa_code", {"code": "482913", "minutos": 10})
    assert "482913" not in asunto
    assert "482913" in cuerpo


# --------------------------------------------------------------------------
# Adjuntos
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_adjunto_valida_contenido_y_mime(client, auth_headers, domain_seed):
    act = domain_seed["act_1"]
    url = f"{BASE}/adjuntos/activos/{act}"
    for nombre, contenido, mime in (
        ("factura.pdf", b"MZ\x90\x00ejecutable", "application/pdf"),
        ("foto.jpg", b"<html><script>alert(1)</script></html>", "image/jpeg"),
        ("notas.txt", b"<svg onload=alert(1)>", "text/plain"),
    ):
        r = await client.post(url, headers=auth_headers, files={"file": (nombre, contenido, mime)},
                              data={"categoria": "otro"})
        assert r.status_code == 400, (nombre, r.text)
        assert r.json()["detail"] == "FILE_CONTENT_MISMATCH"

    # Un PDF real con un MIME inventado se guarda con el MIME del servidor y un nombre saneado.
    r = await client.post(url, headers=auth_headers,
                          files={"file": ("../../etc/factura.pdf", b"%PDF-1.7 real", "text/html")},
                          data={"categoria": "factura"})
    assert r.status_code == 201, r.text
    assert r.json()["ADJ_Nombre_Original"] == "factura.pdf"
    dl = await client.get(f"{BASE}/adjuntos/{r.json()['ADJ_Adjunto']}/download", headers=auth_headers)
    assert dl.headers["content-type"] == "application/pdf"
    assert "sandbox" in dl.headers["content-security-policy"]
    assert dl.headers["x-content-type-options"] == "nosniff"


# --------------------------------------------------------------------------
# Duplicados (QA1 A9/M2)
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_duplicados_que_solo_cambian_mayusculas_o_espacios(client, auth_headers, domain_seed):
    h = auth_headers
    assert (await client.post(f"{BASE}/cat/marcas", headers=h, json={"MAR_Nombre": "Lenovo"})).status_code == 201
    for variante in ("lenovo", "  LENOVO  "):
        r = await client.post(f"{BASE}/cat/marcas", headers=h, json={"MAR_Nombre": variante})
        assert r.status_code in (400, 409), (variante, r.text)
        assert r.json()["detail"] == "BRAND_ALREADY_EXISTS"
    # Un nombre formado solo por espacios no es un nombre.
    assert (await client.post(f"{BASE}/cat/marcas", headers=h, json={"MAR_Nombre": "      "})).status_code == 422


@pytest.mark.asyncio
async def test_correo_de_persona_sin_distinguir_mayusculas(client, auth_headers, domain_seed):
    base = {"PER_Primer_Nombre": "Ana", "PER_Primer_Apellido": "Dup",
            "DEP_Departamento": 1, "CAR_Cargo": 1}
    r = await client.post(f"{BASE}/org/personas", headers=auth_headers,
                          json={**base, "PER_Email_Corporativo": "Ana.Dup@Empresa.local"})
    assert r.status_code == 201, r.text
    assert r.json()["PER_Email_Corporativo"] == "ana.dup@empresa.local"
    r = await client.post(f"{BASE}/org/personas", headers=auth_headers,
                          json={**base, "PER_Email_Corporativo": "ANA.DUP@empresa.local"})
    assert r.status_code in (400, 409)
    assert r.json()["detail"] == "EMAIL_ALREADY_EXISTS"


@pytest.mark.asyncio
async def test_serie_duplicada_con_espacios(client, auth_headers, domain_seed):
    d = domain_seed
    r = await client.post(f"{BASE}/core/activos", headers=auth_headers, json={
        "ACT_Serie_Fabricante": " ser-001 ", "ACT_Codigo_Interno": "LAP-DUP-1",
        "ACT_Fecha_Compra": "2026-01-10", "MOD_Modelo": d["mod"], "TAC_Tipo_Activo": d["tac_lap"],
        "EOP_Estado_Operativo": d["eop_disp"]})
    assert r.status_code in (400, 409)
    assert r.json()["detail"] == "SERIAL_NUMBER_ALREADY_EXISTS"

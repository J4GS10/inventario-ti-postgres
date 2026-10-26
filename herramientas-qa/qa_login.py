"""
Login por API contra el stack de revisión (https://localhost:8443) con MFA TOTP.

Varios procesos comparten la cuenta `sa`: como el TOTP no admite reusar un
código (anti-replay), si el código ya se usó se espera a la siguiente ventana.

Uso:
    from qa_login import BASE, cliente_autenticado, credenciales
    c = cliente_autenticado()                    # httpx.Client con Authorization
    c.get(f"{BASE}/core/activos")

Credenciales en salidas/credenciales_qa.json (generado al enrolar sa; no se versiona).
"""
import json
import os
import time

import httpx
import pyotp

AQUI = os.path.dirname(os.path.abspath(__file__))
ORIGEN = "https://localhost:8443"
BASE = f"{ORIGEN}/api/v1"
CRED = os.path.join(AQUI, "salidas", "credenciales_qa.json")


def credenciales() -> dict:
    with open(CRED, encoding="utf-8") as f:
        return json.load(f)


def _ventana_libre(usuario: str) -> None:
    """
    Reserva una ventana TOTP de 30 s para `usuario` entre procesos.

    Cada código TOTP solo se acepta una vez (anti-replay) y cada rechazo cuenta
    para el bloqueo de la cuenta, así que dos procesos nunca deben enviar el
    mismo código: se usa un candado de archivo y se anota la última ventana usada.
    """
    os.makedirs(os.path.join(AQUI, "salidas"), exist_ok=True)
    candado = os.path.join(AQUI, "salidas", f".totp_{usuario}.lock")
    marca = os.path.join(AQUI, "salidas", f".totp_{usuario}.ultima")
    while True:
        try:
            fd = os.open(candado, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            # Candado huérfano (proceso muerto): se libera tras 90 s.
            try:
                if time.time() - os.path.getmtime(candado) > 90:
                    os.remove(candado)
            except OSError:
                pass
            time.sleep(1)
    os.close(fd)
    try:
        ultima = int(open(marca).read().strip()) if os.path.exists(marca) else -1
        while int(time.time()) // 30 <= ultima:
            time.sleep(1)
        open(marca, "w").write(str(int(time.time()) // 30))
    finally:
        os.remove(candado)


def login(client: httpx.Client, usuario: str, clave: str, totp_secret: str | None = None) -> dict:
    """Devuelve el JSON del login final (access_token, restriction...)."""
    if totp_secret:
        _ventana_libre(usuario)  # antes de pedir el challenge: el código se usa enseguida
    r = client.post(f"{BASE}/login/access-token", data={"username": usuario, "password": clave})
    r.raise_for_status()
    body = r.json()
    challenge = body.get("challenge_token")
    if not challenge:
        return body
    if not totp_secret:
        raise RuntimeError(f"{usuario} requiere 2FA y no se indicó secreto TOTP")
    v = client.post(f"{BASE}/login/2fa/verify",
                    json={"challenge_token": challenge, "code": pyotp.TOTP(totp_secret).now()})
    if v.status_code == 200:
        return v.json()
    # No se reintenta: cada rechazo acerca la cuenta al bloqueo.
    raise RuntimeError(f"No se pudo completar el 2FA de {usuario}: {v.status_code} {v.text}")


def codigo_totp_ui(usuario: str, totp_secret: str) -> str:
    """Código TOTP para escribir en el navegador, sin chocar con otros procesos."""
    _ventana_libre(usuario)
    return pyotp.TOTP(totp_secret).now()


def cliente_autenticado(usuario: str | None = None, clave: str | None = None,
                        totp_secret: str | None = None) -> httpx.Client:
    """httpx.Client ya autenticado (por defecto como sa)."""
    if usuario is None:
        cred = credenciales()
        usuario, clave, totp_secret = cred["usuario"], cred["clave"], cred["totp_secret"]
    c = httpx.Client(verify=False, timeout=60)
    body = login(c, usuario, clave, totp_secret)
    c.headers["Authorization"] = f"Bearer {body['access_token']}"
    return c

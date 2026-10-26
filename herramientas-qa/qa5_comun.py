"""Utilidades comunes de QA5: un solo login por cuenta, caché de token y renovación con /login/refresh.

Reglas (coordinación con otros agentes que usan `sa`):
- Se reutiliza el access token en caché mientras sea válido.
- Si expiró, se renueva con el refresh token (cookie `rtk`) en lugar de un nuevo login.
- Nunca se reintenta un 2FA fallido (qa_login.login ya no reintenta).
- Si la cuenta aparece bloqueada (ACCOUNT_LOCKED) se espera a que expire el bloqueo
  y se anota en salidas/qa5/observaciones_login.txt.
"""
import json
import os
import time

import httpx

from qa_login import BASE, credenciales, login

AQUI = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(AQUI, "salidas", "qa5")
os.makedirs(OUT, exist_ok=True)
CACHE = os.path.join(OUT, "_tokens.json")


def _cache() -> dict:
    try:
        with open(CACHE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _guardar(usuario, access, rtk):
    cache = _cache()
    cache[usuario] = {"access": access, "rtk": rtk}
    with open(CACHE, "w", encoding="utf-8") as f:
        json.dump(cache, f)


def _nota(texto):
    with open(os.path.join(OUT, "observaciones_login.txt"), "a", encoding="utf-8") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + texto + "\n")


def cliente(usuario=None, clave=None, totp=None) -> httpx.Client:
    if usuario is None:
        cr = credenciales()
        usuario, clave, totp = cr["usuario"], cr["clave"], cr["totp_secret"]
    c = httpx.Client(verify=False, timeout=120)
    ent = _cache().get(usuario)
    if isinstance(ent, dict):
        c.headers["Authorization"] = f"Bearer {ent['access']}"
        if c.get(f"{BASE}/me").status_code == 200:
            return c
        c.headers.pop("Authorization", None)
        if ent.get("rtk"):
            r = c.post(f"{BASE}/login/refresh", json={"refresh_token": ent["rtk"]})
            if r.status_code == 200 and r.json().get("access_token"):
                tok = r.json()["access_token"]
                c.headers["Authorization"] = f"Bearer {tok}"
                _guardar(usuario, tok, c.cookies.get("rtk") or ent["rtk"])
                return c
    while True:
        try:
            body = login(c, usuario, clave, totp)
            break
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429 and "ACCOUNT_LOCKED" in e.response.text:
                _nota(f"{usuario} bloqueada (ACCOUNT_LOCKED); se espera a que expire el bloqueo")
                time.sleep(60)
            elif e.response.status_code == 429:
                time.sleep(15)
            else:
                raise
    c.headers["Authorization"] = f"Bearer {body['access_token']}"
    _guardar(usuario, body["access_token"], c.cookies.get("rtk"))
    return c


def datos() -> dict:
    with open(os.path.join(OUT, "datos.json"), encoding="utf-8") as f:
        return json.load(f)

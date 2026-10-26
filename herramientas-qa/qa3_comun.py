"""
QA3 — Sesión compartida de `sa` para todos los scripts qa3_*.

Regla: iniciar sesión con sa lo menos posible. El access token y el refresh
token (cookie `rtk`) se guardan en salidas/qa3/.sa_sesion.json; si el access
expiró se usa /login/refresh (rotación) y solo si eso falla se hace UN login
(qa_login serializa el TOTP entre procesos y nunca reintenta).
"""
import json
import os
import warnings

import httpx

warnings.filterwarnings("ignore")
from qa_login import BASE, cliente_autenticado  # noqa: E402

AQUI = os.path.dirname(os.path.abspath(__file__))
SESION = os.path.join(AQUI, "salidas", "qa3", ".sa_sesion.json")


def _guardar(c: httpx.Client) -> None:
    rtk = next((ck.value for ck in c.cookies.jar if ck.name == "rtk"), None)
    os.makedirs(os.path.dirname(SESION), exist_ok=True)
    with open(SESION, "w", encoding="utf-8") as f:
        json.dump({"access": c.headers.get("Authorization", "").split(" ")[-1], "rtk": rtk}, f)


class _SaClient(httpx.Client):
    """httpx.Client que renueva el access token con /login/refresh ante un 401."""

    def request(self, method, url, **kw):  # noqa: D401
        r = super().request(method, url, **kw)
        if r.status_code == 401 and "/login/" not in str(url) and _refrescar(self):
            r = super().request(method, url, **kw)
        return r


def _refrescar(c: httpx.Client) -> bool:
    try:
        datos = json.load(open(SESION, encoding="utf-8"))
    except Exception:
        return False
    if not datos.get("rtk"):
        return False
    r = httpx.Client.request(c, "POST", f"{BASE}/login/refresh", json={"refresh_token": datos["rtk"]})
    if r.status_code != 200:
        return False
    c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
    nuevo = r.cookies.get("rtk") or next((ck.value for ck in c.cookies.jar if ck.name == "rtk"), None)
    with open(SESION, "w", encoding="utf-8") as f:
        json.dump({"access": r.json()["access_token"], "rtk": nuevo or datos["rtk"]}, f)
    return True


def sa_cliente() -> httpx.Client:
    c = _SaClient(verify=False, timeout=90)
    try:
        datos = json.load(open(SESION, encoding="utf-8"))
        c.headers["Authorization"] = f"Bearer {datos['access']}"
        if c.get(f"{BASE}/me").status_code == 200:
            return c
    except Exception:
        pass
    # Un único login de sa (serializado por qa_login, sin reintentos).
    base = cliente_autenticado()
    c.headers["Authorization"] = base.headers["Authorization"]
    for ck in base.cookies.jar:
        c.cookies.jar.set_cookie(ck)
    _guardar(c)
    return c

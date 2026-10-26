"""
QA1 — Sesión de `sa` reutilizable entre ejecuciones.

Reglas (cuenta compartida con otros agentes):
- Se inicia sesión con sa lo menos posible: el access token y el refresh
  (cookie `rtk`) se guardan en salidas/qa1/.sesion_sa.json y se reutilizan.
- Si el access token expira se usa /login/refresh (rotación), nunca un login nuevo.
- El login (con 2FA) se intenta UNA sola vez vía qa_login (serializado entre
  procesos). Nunca se reintenta un 2FA fallido.
"""
import json
import os

import httpx

from qa_login import BASE, cliente_autenticado

AQUI = os.path.dirname(os.path.abspath(__file__))
F = os.path.join(AQUI, "salidas", "qa1", ".sesion_sa.json")


def _guardar(c: httpx.Client) -> None:
    os.makedirs(os.path.dirname(F), exist_ok=True)
    rtk = next((ck.value for ck in c.cookies.jar if ck.name == "rtk"), None)
    with open(F, "w", encoding="utf-8") as f:
        json.dump({"access": c.headers.get("Authorization", ""), "rtk": rtk}, f)


def refrescar(c: httpx.Client) -> bool:
    """Rota el refresh y actualiza el access token. True si funcionó."""
    try:
        d = json.load(open(F, encoding="utf-8"))
    except Exception:
        return False
    rtk = next((ck.value for ck in c.cookies.jar if ck.name == "rtk"), None) or d.get("rtk")
    if not rtk:
        return False
    c.cookies.clear()
    r = c.post(f"{BASE}/login/refresh", json={"refresh_token": rtk})
    if r.status_code != 200:
        return False
    c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
    _guardar(c)
    return True


def cliente_sa() -> httpx.Client:
    c = httpx.Client(verify=False, timeout=60)
    if os.path.exists(F):
        d = json.load(open(F, encoding="utf-8"))
        c.headers["Authorization"] = d.get("access", "")
        if c.get(f"{BASE}/cat/marcas").status_code == 200:
            return c
        if refrescar(c):
            return c
    # Único intento de login (qa_login serializa la ventana TOTP y no reintenta).
    c = cliente_autenticado()
    _guardar(c)
    return c

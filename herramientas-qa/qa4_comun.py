"""
QA4 — utilidades comunes: cuentas de prueba qa4_*, login sin reintentos
peligrosos, registro de casos y evidencias en salidas/qa4/.
"""
import json
import os
import time

import httpx
import pyotp

AQUI = os.path.dirname(os.path.abspath(__file__))
ORIGEN = "https://localhost:8443"
API = f"{ORIGEN}/api/v1"
SAL = os.path.join(AQUI, "salidas", "qa4")
CUENTAS = os.path.join(SAL, "cuentas.json")
os.makedirs(SAL, exist_ok=True)


class _Cliente(httpx.Client):
    """Reintenta 429 RATE_LIMIT_EXCEEDED (limites por IP compartidos con otros agentes).
    Nunca reintenta 429 ACCOUNT_LOCKED ni otros codigos."""
    reintentar_rl = True

    def send(self, request, **kw):
        for _ in range(10):
            r = super().send(request, **kw)
            if not (self.reintentar_rl and r.status_code == 429 and b"RATE_LIMIT_EXCEEDED" in r.read()):
                return r
            time.sleep(15)
        return r


def cliente(**kw) -> httpx.Client:
    return _Cliente(verify=False, timeout=60, **kw)


def cargar_cuentas() -> dict:
    if os.path.exists(CUENTAS):
        with open(CUENTAS, encoding="utf-8") as f:
            return json.load(f)
    return {}


def guardar_cuentas(d: dict) -> None:
    with open(CUENTAS, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)


def esperar_ventana_fresca(margen: int = 2) -> None:
    """Espera al inicio de una ventana TOTP nueva (evita choques de anti-replay)."""
    resto = 30 - (int(time.time()) % 30)
    if resto < 25:
        time.sleep(resto + margen)


def login_totp_una_vez(c: httpx.Client, usuario: str, clave: str, secreto: str | None) -> dict:
    """Login con un único intento de 2FA (no reintenta: no suma fallos al bloqueo)."""
    r = c.post(f"{API}/login/access-token", data={"username": usuario, "password": clave})
    if r.status_code != 200:
        raise RuntimeError(f"login {usuario}: {r.status_code} {r.text}")
    body = r.json()
    if not body.get("challenge_token"):
        return body
    if not secreto:
        raise RuntimeError(f"{usuario} pide 2FA sin secreto")
    v = c.post(f"{API}/login/2fa/verify", json={"challenge_token": body["challenge_token"],
                                                "code": pyotp.TOTP(secreto).now()})
    if v.status_code != 200:
        raise RuntimeError(f"2FA {usuario}: {v.status_code} {v.text}")
    return v.json()


def autenticado(usuario: str) -> tuple[httpx.Client, dict]:
    """Cliente autenticado para una cuenta qa4_* (espera ventana TOTP si tiene MFA)."""
    cu = cargar_cuentas()[usuario]
    c = cliente()
    if cu.get("totp"):
        esperar_ventana_fresca()
    body = login_totp_una_vez(c, usuario, cu["clave"], cu.get("totp"))
    c.headers["Authorization"] = f"Bearer {body['access_token']}"
    return c, body


class Registro:
    """Acumula casos y guarda la evidencia en JSON."""

    def __init__(self, nombre: str):
        self.nombre = nombre
        self.casos: list[dict] = []

    def caso(self, cid: str, titulo: str, ok: bool, obtenido, esperado: str = "", extra=None):
        d = {"id": cid, "caso": titulo, "ok": bool(ok), "esperado": esperado,
             "obtenido": obtenido, "extra": extra}
        self.casos.append(d)
        print(f"{'OK ' if ok else 'XX '} {cid} {titulo} -> {str(obtenido)[:220]}", flush=True)
        self.guardar()
        return ok

    def guardar(self):
        with open(os.path.join(SAL, f"{self.nombre}.json"), "w", encoding="utf-8") as f:
            json.dump(self.casos, f, ensure_ascii=False, indent=1, default=str)


def resumen(r: httpx.Response, n: int = 200) -> str:
    return f"{r.status_code} {r.text[:n]}"

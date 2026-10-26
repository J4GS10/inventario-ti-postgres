"""E2E contra el stack de producción: Caddy (TLS) → nginx → gunicorn x4 → Postgres/Redis/MailHog."""
import asyncio
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

import httpx
import jwt

BASE = "https://localhost:8443"
API = f"{BASE}/api/v1"
MAILHOG = "http://localhost:8025/api/v2/messages"
ENV = dict(l.split("=", 1) for l in open(sys.argv[1], encoding="utf-8").read().splitlines() if "=" in l and not l.startswith("#"))
PROJECT = ["docker", "compose", "-p", "inv_e2e", "--env-file", sys.argv[1], "--profile", "dev"]
USER, PWD = "sa", ENV["SUPER_ADMIN_PASSWORD"]

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}{('  -> ' + str(detail)) if detail and not ok else ''}", flush=True)


def new_client():
    return httpx.AsyncClient(verify=False, timeout=30)


async def login(c, headers=None):
    r = await c.post(f"{API}/login/access-token", data={"username": USER, "password": PWD}, headers=headers or {})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def bearer(t):
    return {"Authorization": f"Bearer {t}"}


def compose(*args):
    subprocess.run([*PROJECT, *args], check=True, capture_output=True, cwd=".")


async def mailhog_count(c):
    r = await c.get(MAILHOG)
    return r.json()["total"]


async def main():
    async with new_client() as c:
        # ---------------- Proxy / cabeceras ----------------
        r = await c.get(f"{BASE}/")
        h = r.headers
        check("SPA servida por Caddy→nginx (200 HTML)", r.status_code == 200 and "text/html" in h.get("content-type", ""))
        check("HSTS presente", "max-age=31536000" in h.get("strict-transport-security", ""))
        check("CSP presente", "default-src 'self'" in h.get("content-security-policy", ""))
        check("Permissions-Policy permite cámara (escáner)", "camera=(self)" in h.get("permissions-policy", ""), h.get("permissions-policy"))
        check("Sin versión de servidor expuesta", "nginx/" not in h.get("server", "") and "server" not in {k.lower() for k in h.keys()} or h.get("server", "").lower() in ("", "caddy"), h.get("server"))
        check("HTML sin caché", "no-store" in h.get("cache-control", ""))
        import re
        asset = re.search(r'src="(/assets/[^"]+\.js)"', r.text)
        if asset:
            ra = await c.get(BASE + asset.group(1))
            check("Assets con caché inmutable", "immutable" in ra.headers.get("cache-control", ""))
        r = await c.get(f"{BASE}/metrics")
        check("/metrics bloqueado en el edge", r.status_code == 403)
        r = await c.get(f"{BASE}/health/full")
        check("/health/full bloqueado en el edge", r.status_code == 403)
        r = await c.get(f"{BASE}/dashboard")
        check("Rutas SPA hacen fallback a index.html", r.status_code == 200 and "<div id=\"root\">" in r.text)

        # ---------------- Login y cookie ----------------
        r = await c.post(f"{API}/login/access-token", data={"username": USER, "password": PWD},
                         headers={"X-Real-IP": "6.6.6.6", "X-Forwarded-For": "6.6.6.6"})
        check("Login vía proxy", r.status_code == 200, r.text[:200])
        access = r.json()["access_token"]
        sc = r.headers.get("set-cookie", "").lower()
        check("Cookie refresh: HttpOnly+Secure+SameSite=Strict", all(x in sc for x in ("httponly", "secure", "samesite=strict")), sc)
        check("Cookie refresh: de sesión (sin Max-Age)", "max-age" not in sc, sc)
        check("Cookie refresh: limitada a /api/v1/login", "path=/api/v1/login" in sc, sc)
        r = await c.get(f"{API}/me", headers=bearer(access))
        check("/me con access token", r.status_code == 200)

        r = await c.get(f"{API}/gov/auditoria?accion=LOGIN_SUCCESS&limit=1", headers=bearer(access))
        items = r.json().get("items") or r.json().get("data") or []
        ip = (items[0].get("AUD_IP_Origen") if items else None)
        check("IP de auditoría no falsificable (X-Real-IP del cliente ignorado)", ip and ip != "6.6.6.6", ip)

        # ---------------- Refresh / rotación ----------------
        r = await c.post(f"{API}/login/refresh")
        check("Refresh con cookie vía proxy", r.status_code == 200, r.text[:200])
        rtk = c.cookies.get("rtk", path="/api/v1/login") or c.cookies.get("rtk")
        claims = jwt.decode(rtk, options={"verify_signature": False})
        check("Refresh expira por inactividad (≤ 30 min)", claims["exp"] - claims["iat"] <= 30 * 60 + 5)

        async with new_client() as c2:
            results_conc = await asyncio.gather(*[
                c2.post(f"{API}/login/refresh", json={"refresh_token": rtk}) for _ in range(6)
            ])
        codes = sorted(x.status_code for x in results_conc)
        check("6 refresh concurrentes (4 workers + Postgres): 1 gana, 0 errores 500",
              codes.count(200) == 1 and 500 not in codes and 502 not in codes, codes)

    # ---------------- Logout ----------------
    async with new_client() as c:
        access = await login(c)
        r = await c.post(f"{API}/login/logout")  # como el SPA: sin Authorization
        check("Logout solo con cookie → 204", r.status_code == 204)
        r = await c.post(f"{API}/login/refresh")
        check("Tras logout, el refresh queda revocado", r.status_code == 401)

    async with new_client() as c:
        await login(c)
        rtk = c.cookies.get("rtk")
        exp_access = jwt.encode({"sub": USER, "role": "SUPER_ADMIN", "type": "access", "jti": "x" * 32,
                                 "iat": int(time.time()) - 3600, "exp": int(time.time()) - 60},
                                ENV["SECRET_KEY"], algorithm="HS256")
        r = await c.post(f"{API}/login/logout", headers=bearer(exp_access))
        check("Logout por inactividad (access vencido) → 204", r.status_code == 204)
        async with new_client() as c2:
            r = await c2.post(f"{API}/login/refresh", json={"refresh_token": rtk})
        check("…y el refresh también quedó revocado", r.status_code == 401)

    # ---------------- Logout global ----------------
    async with new_client() as pc, new_client() as movil:
        pc_access = await login(pc)
        movil_access = await login(movil)
        r = await movil.post(f"{API}/login/logout-all", headers=bearer(movil_access))
        check("Cerrar todas las sesiones → 204", r.status_code == 204)
        r = await pc.get(f"{API}/me", headers=bearer(pc_access))
        check("Otro dispositivo pierde el access (caché Redis incluida)", r.status_code == 401)
        r = await pc.post(f"{API}/login/refresh")
        check("Otro dispositivo pierde el refresh", r.status_code == 401)
        access = await login(pc)
        r = await pc.get(f"{API}/me", headers=bearer(access))
        check("Reingreso inmediato funciona", r.status_code == 200)

    # ---------------- Detección de reuso (espera la ventana de gracia) ----------------
    async with new_client() as c:
        await login(c)
        old = c.cookies.get("rtk")
        r = await c.post(f"{API}/login/refresh")
        new_access = r.json()["access_token"]
        print("      (esperando 32 s: ventana de gracia de reuso)", flush=True)
        await asyncio.sleep(32)
        async with new_client() as attacker:
            r = await attacker.post(f"{API}/login/refresh", json={"refresh_token": old})
        check("Reuso de refresh robado → 401", r.status_code == 401)
        r = await c.get(f"{API}/me", headers=bearer(new_access))
        check("…y se revocan TODAS las sesiones del usuario", r.status_code == 401)

    # ---------------- Endpoint largo (AD) enrutado ----------------
    async with new_client() as c:
        access = await login(c)
        r = await c.post(f"{API}/directorio/sincronizar", headers=bearer(access))
        check("Ruta /directorio/sincronizar llega al backend (400 AD_DISABLED)", r.status_code == 400 and "AD_DISABLED" in r.text, r.text[:120])

        # ---------------- Cola de correos ----------------
        before = await mailhog_count(c)
        # Correo encolable: cambio de contraseña (aviso de seguridad al dueño).
        # Se usa reset de lockout indirecto: cambiamos y restauramos la contraseña.
        r = await c.get(f"{API}/gov/email-outbox", headers=bearer(access))
        check("API de la cola responde", r.status_code == 200, r.text[:120])

        compose("stop", "mailhog")
        tmp_pwd = "Otra-Passw0rd-Segura!"
        r = await c.post(f"{API}/me/password", json={"current_password": PWD, "new_password": tmp_pwd}, headers=bearer(access))
        check("Cambio de contraseña (dispara correo)", r.status_code == 204, r.text[:200])
        await asyncio.sleep(3)
        access = (await c.post(f"{API}/login/access-token", data={"username": USER, "password": tmp_pwd})).json()["access_token"]
        r = await c.get(f"{API}/gov/email-outbox", headers=bearer(access))
        s = r.json()
        check("SMTP caído: correo queda PENDIENTE en la cola", s["pendientes"] >= 1, s)

        compose("restart", "backend")
        for _ in range(60):
            try:
                if (await c.get(f"{BASE}/health")).status_code == 200:
                    break
            except Exception:
                pass
            await asyncio.sleep(2)
        compose("start", "mailhog")
        print("      (esperando el backoff de 60 s para el reintento)", flush=True)
        delivered = False
        for _ in range(30):
            await asyncio.sleep(5)
            try:
                # MailHog guarda en memoria: al reiniciarlo se vacía, así que
                # basta con que aparezca el aviso de cambio de contraseña.
                msgs = (await c.get(MAILHOG)).json().get("items", [])
                if any("contrasena fue cambiada" in (m["Content"]["Headers"].get("Subject") or [""])[0].lower() for m in msgs):
                    delivered = True
                    break
            except Exception:
                pass
        check("Tras reiniciar el backend y volver el SMTP, el correo se entrega", delivered)
        access = (await c.post(f"{API}/login/access-token", data={"username": USER, "password": tmp_pwd})).json()["access_token"]
        r = await c.post(f"{API}/me/password", json={"current_password": tmp_pwd, "new_password": PWD}, headers=bearer(access))
        s = (await c.get(f"{API}/gov/email-outbox", headers=bearer((await c.post(f"{API}/login/access-token", data={"username": USER, "password": PWD})).json()["access_token"]))).json()
        check("Cola sin fallidos", s["fallidos"] == 0, s)

        # ---------------- Rate limit compartido entre workers (Redis) ----------------
        async with new_client() as c3:
            codes = [(await c3.post(f"{API}/login/refresh", json={"refresh_token": "x"})).status_code for _ in range(35)]
        check("Rate limit de refresh (30/min en e2e) aplica entre los 4 workers", 429 in codes, codes)

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} verificaciones OK")
    if failed:
        print("FALLARON:", *failed, sep="\n  - ")
        sys.exit(1)


asyncio.run(main())

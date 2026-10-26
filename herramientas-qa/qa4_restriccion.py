"""
QA4 — Barrido de TODAS las rutas de la API (salidas/qa4/rutas.json, extraídas de
app.openapi() dentro del contenedor) con:
  a) sin token            → se espera 401 salvo las rutas públicas;
  b) sesión restringida mfa_enroll (qa4_admti, ADMIN_TI sin MFA);
  c) sesión restringida pwd_change (qa4_temp tras un reset de contraseña).
Con sesión restringida solo el autoservicio (/me, /me/password, /me/2fa/*) puede
responder distinto de 403. Un 422 en una ruta protegida delata que la
autenticación PASÓ (FastAPI valida el cuerpo después de las dependencias).
Se usan ids inexistentes y cuerpos vacíos: no modifica datos.
"""
import json
import os
import re

from qa4_comun import API, ORIGEN, SAL, Registro, autenticado, cargar_cuentas, cliente, guardar_cuentas, login_totp_una_vez

R = Registro("restriccion")
rutas = json.load(open(os.path.join(SAL, "rutas.json"), encoding="utf-8"))

PUBLICAS = re.compile(r"^(/health|/health/full|/metrics|/)$|^/api/v1/(login/(access-token|2fa/verify|refresh|logout|sso/.*|password-reset/.*)|gov/config)$")
AUTOSERVICIO = re.compile(r"^/api/v1/(me|me/password|me/2fa.*|me/sessions.*|login/logout-all)$")
NO_TOCAR = {"/api/v1/login/logout-all", "/api/v1/login/logout", "/api/v1/login/refresh"}  # cerraría las sesiones de la cuenta de prueba en mitad del barrido


def url_de(path: str) -> str:
    def rep(m):
        nombre = m.group(1).lower()
        if nombre in ("provider", "evento", "lang", "formato", "tipo"):
            return "x"
        if "id" in nombre or nombre in ("session_id",):
            return "00000000-0000-0000-0000-000000000000"
        return "999999999"
    return ORIGEN + re.sub(r"\{([^}]+)\}", rep, path)


def barrer(c, etiqueta: str, esperado_detail: str | None):
    fallos, total = [], 0
    for metodos, path in rutas:
        if path in NO_TOCAR:
            continue
        for m in metodos:
            if m in ("HEAD", "OPTIONS"):
                continue
            url = url_de(path)
            # Con ids UUID de ceros, las rutas con {id:int} devuelven 422 por el path, no por auth:
            # se prueba también con entero para distinguir.
            r = c.request(m, url, json={} if m in ("POST", "PUT", "PATCH") else None)
            if r.status_code == 422 and "00000000-0000" in url:
                r = c.request(m, url.replace("00000000-0000-0000-0000-000000000000", "999999999"),
                              json={} if m in ("POST", "PUT", "PATCH") else None)
            total += 1
            publico = bool(PUBLICAS.match(path))
            auto = bool(AUTOSERVICIO.match(path))
            if esperado_detail is None:  # sin token
                ok = publico or r.status_code == 401
            else:
                if auto or publico:
                    ok = r.status_code < 500
                else:
                    ok = r.status_code == 403 and esperado_detail in r.text
            if not ok:
                fallos.append({"metodo": m, "ruta": path, "status": r.status_code, "body": r.text[:160]})
    return total, fallos


# a) Sin token
c = cliente()
total, fallos = barrer(c, "anon", None)
R.caso("RES-01", f"Barrido sin token de {total} operaciones: todas 401 salvo públicas", not fallos,
       f"{len(fallos)} no-401", "401 en todas las rutas no públicas", fallos)

# b) mfa_enroll: qa4_admti (ADMIN_TI sin MFA)
cu = cargar_cuentas()
c = cliente()
b = login_totp_una_vez(c, "qa4_admti", cu["qa4_admti"]["clave"], None)
R.caso("RES-02", "ADMIN_TI sin MFA recibe sesión restringida", b.get("restriction") == "MFA_ENROLLMENT_REQUIRED",
       b.get("restriction"), "restriction=MFA_ENROLLMENT_REQUIRED")
c.headers["Authorization"] = f"Bearer {b['access_token']}"
# El refresh no levanta la restricción
r = c.post(f"{API}/login/refresh")
if r.status_code == 200:
    c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
R.caso("RES-04", "Refresh de sesión mfa_enroll sigue restringido",
       r.status_code == 200 and r.json().get("restriction") == "MFA_ENROLLMENT_REQUIRED",
       f"{r.status_code} restriction={r.json().get('restriction') if r.status_code == 200 else r.text}")

total, fallos = barrer(c, "mfa", "MFA_ENROLLMENT_REQUIRED")
R.caso("RES-03", f"Sesión mfa_enroll: {total} operaciones, solo autoservicio accesible", not fallos,
       f"{len(fallos)} rutas no bloqueadas", "403 MFA_ENROLLMENT_REQUIRED fuera del autoservicio", fallos)
# c) pwd_change: reset de contraseña de qa4_temp por qa4_seg (ADMIN_SEGURIDAD)
seg, _ = autenticado("qa4_seg")
r = seg.post(f"{API}/org/usuarios/{cu['qa4_temp']['id']}/password/reset")
tmp = r.json().get("temporary_password") if r.status_code == 200 else None
R.caso("RES-05", "Reset de contraseña por ADMIN_SEGURIDAD devuelve temporal con Cache-Control no-store",
       bool(tmp) and "no-store" in r.headers.get("cache-control", ""), f"{r.status_code} cache={r.headers.get('cache-control')} len={len(tmp or '')}")
cu["qa4_temp"]["clave"] = tmp
cu["qa4_temp"]["temporal_actual"] = tmp
guardar_cuentas(cu)
c = cliente()
b = login_totp_una_vez(c, "qa4_temp", tmp, None)
R.caso("RES-06", "Login con temporal → restricción PASSWORD_CHANGE_REQUIRED",
       b.get("restriction") == "PASSWORD_CHANGE_REQUIRED", b.get("restriction"))
c.headers["Authorization"] = f"Bearer {b['access_token']}"
total, fallos = barrer(c, "pwd", "PASSWORD_CHANGE_REQUIRED")
R.caso("RES-07", f"Sesión pwd_change: {total} operaciones, solo autoservicio accesible", not fallos,
       f"{len(fallos)} rutas no bloqueadas", "403 PASSWORD_CHANGE_REQUIRED fuera del autoservicio", fallos)
# Autoservicio que NO debería necesitar una sesión con contraseña temporal
for m, p in [("POST", "/me/2fa/totp/setup"), ("GET", "/me/sessions")]:
    r = c.request(m, API + p)
    R.caso(f"RES-08{p}", f"Con contraseña temporal pendiente: {m} {p}", True, f"{r.status_code} {r.text[:80]}",
           "Informativo: el autoservicio es accesible con la sesión restringida")

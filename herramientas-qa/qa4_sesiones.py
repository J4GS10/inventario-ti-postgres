"""
QA4 — Sesiones: lista, cierre individual (propio, ajeno y por administrador),
cierre global, dispositivo nuevo, refresh con rotacion y deteccion de reuso,
sesion absoluta, cookies, y efecto de desactivacion/cambio de contrasena.
Cuentas: qa4_ses, qa4_consulta, qa4_consulta3, qa4_seg. Evidencia: salidas/qa4/sesiones.json
"""
import os
import time

import jwt

from qa4_comun import AQUI, API, Registro, autenticado, cargar_cuentas, cliente, login_totp_una_vez, resumen

R = Registro("sesiones")
cu = cargar_cuentas()
ENV = dict(l.split("=", 1) for l in open(os.path.join(AQUI, "revision.env"), encoding="utf-8").read().splitlines()
           if "=" in l and not l.startswith("#"))
UA_WIN = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
UA_MAC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) Gecko/20100101 Firefox/128.0"
GRACE = int(ENV.get("REFRESH_REUSE_GRACE_SECONDS", "30"))


def entrar(usr, ua):
    c = cliente(headers={"User-Agent": ua})
    b = login_totp_una_vez(c, usr, cu[usr]["clave"], cu[usr].get("totp"))
    c.headers["Authorization"] = f"Bearer {b['access_token']}"
    return c, b


# ---------- Cookie y respuesta de login ----------
pc = cliente(headers={"User-Agent": UA_WIN})
login_resp = pc.post(f"{API}/login/access-token", data={"username": "qa4_ses", "password": cu["qa4_ses"]["clave"]})
sc = login_resp.headers.get("set-cookie", "").lower()
R.caso("SES-01", "Cookie de refresh HttpOnly + Secure + SameSite=Strict, de sesion y limitada a /api/v1/login",
       all(x in sc for x in ("httponly", "secure", "samesite=strict", "path=/api/v1/login")) and "max-age" not in sc, sc[:200])
R.caso("SES-02", "Respuesta de login con tokens lleva Cache-Control: no-store",
       "no-store" in login_resp.headers.get("cache-control", "").lower(),
       f"cache-control={login_resp.headers.get('cache-control')}", "no-store recomendado (RFC 6749 5.1)")
R.caso("SES-03", "El refresh token NO viaja en el JSON", "refresh_token" not in login_resp.text, list(login_resp.json().keys()))
pc.headers["Authorization"] = f"Bearer {login_resp.json()['access_token']}"

# ---------- Lista y dispositivo nuevo ----------
mac, _ = entrar("qa4_ses", UA_MAC)
lista = pc.get(f"{API}/me/sessions").json()
disp = {s["dispositivo"] for s in lista}
R.caso("SES-04", "Lista de sesiones propias: dispositivo, IP y una sola marcada como actual",
       len(lista) >= 2 and sum(1 for s in lista if s["actual"]) == 1 and "Firefox · macOS" in disp,
       [(s["dispositivo"], s["ip"], s["actual"]) for s in lista])

# ---------- Cierre individual propio ----------
otra = next(s for s in lista if not s["actual"])
d = pc.delete(f"{API}/me/sessions/{otra['id']}")
r_mac = mac.get(f"{API}/me")
R.caso("SES-05", "Cerrar una sesion propia invalida su access token de inmediato (sin esperar TTL)",
       d.status_code == 204 and r_mac.status_code == 401, f"delete={d.status_code}; mac /me={resumen(r_mac, 60)}")
r_mac_ref = mac.post(f"{API}/login/refresh")
R.caso("SES-06", "La sesion cerrada tampoco puede refrescar", r_mac_ref.status_code == 401, resumen(r_mac_ref, 60))
# La sesion actual sigue viva
R.caso("SES-07", "Cerrar otra sesion no afecta a la actual", pc.get(f"{API}/me").status_code == 200,
       resumen(pc.get(f"{API}/me"), 40))

# ---------- IDOR sobre sesiones: cerrar la sesion de OTRA cuenta ----------
c3, _ = autenticado("qa4_consulta3")
ses_c3 = c3.get(f"{API}/me/sessions").json()
ajena = ses_c3[0]["id"]
attack = pc.delete(f"{API}/me/sessions/{ajena}")
R.caso("SES-08", "IDOR: un usuario NO puede cerrar la sesion de otro por id (404, no 204)",
       attack.status_code == 404 and c3.get(f"{API}/me").status_code == 200,
       f"delete ajena={attack.status_code}; victima sigue viva={c3.get(f'{API}/me').status_code}")

# ---------- Refresh: rotacion y reuso ----------
rc = cliente()
b = login_totp_una_vez(rc, "qa4_consulta", cu["qa4_consulta"]["clave"], None)
old_rtk = rc.cookies.get("rtk")
r1 = rc.post(f"{API}/login/refresh")
new_access = r1.json()["access_token"]
new_rtk = rc.cookies.get("rtk")
R.caso("SES-09", "Refresh rota el token (nuevo refresh distinto y solo en cookie)",
       r1.status_code == 200 and new_rtk != old_rtk and "refresh_token" not in r1.text, f"{r1.status_code} rotado={new_rtk != old_rtk}")
claims = jwt.decode(new_rtk, options={"verify_signature": False})
R.caso("SES-10", "El refresh caduca por inactividad (<= SESSION_IDLE_TIMEOUT, 30 min)",
       claims["exp"] - claims["iat"] <= 30 * 60 + 5, f"vida={claims['exp'] - claims['iat']}s")
# Reuso dentro de la gracia -> solo 401, sin matar sesiones
reuse = cliente()
rr = reuse.post(f"{API}/login/refresh", json={"refresh_token": old_rtk})
still = rc.get(f"{API}/me", headers={"Authorization": f"Bearer {new_access}"})
R.caso("SES-11", "Reuso del refresh viejo dentro de la ventana de gracia: 401 y la sesion buena sigue",
       rr.status_code == 401 and still.status_code == 200, f"reuso={rr.status_code}; sesion buena={still.status_code}")
# Reuso fuera de gracia -> mata TODAS
time.sleep(GRACE + 3)
rr2 = reuse.post(f"{API}/login/refresh", json={"refresh_token": old_rtk})
dead = rc.get(f"{API}/me", headers={"Authorization": f"Bearer {new_access}"})
r_ref = rc.post(f"{API}/login/refresh", json={"refresh_token": new_rtk})
R.caso("SES-12", "Reuso pasada la gracia: robo -> se revocan TODAS las sesiones del usuario (REFRESH_TOKEN_REUSE)",
       rr2.status_code == 401 and dead.status_code == 401 and r_ref.status_code == 401,
       f"reuso={rr2.status_code}; access previo={dead.status_code}; refresh legitimo={r_ref.status_code}")

# ---------- Cierre global ----------
g1, _ = entrar("qa4_consulta", UA_WIN)
g2, _ = entrar("qa4_consulta", UA_MAC)
lo = g2.post(f"{API}/login/logout-all")
R.caso("SES-13", "logout-all cierra todas las sesiones del usuario", lo.status_code == 204 and g1.get(f"{API}/me").status_code == 401,
       f"logout-all={lo.status_code}; otro dispositivo={g1.get(f'{API}/me').status_code}")
re_in = cliente()
bb = login_totp_una_vez(re_in, "qa4_consulta", cu["qa4_consulta"]["clave"], None)
R.caso("SES-14", "Reingreso inmediato tras el cierre global funciona",
       re_in.get(f"{API}/me", headers={"Authorization": f"Bearer {bb['access_token']}"}).status_code == 200, "200 esperado")

# ---------- Cierre por administrador (IAM) ----------
victima, _ = entrar("qa4_consulta3", UA_WIN)
seg, _ = autenticado("qa4_seg")
adm_list = seg.get(f"{API}/org/usuarios/{cu['qa4_consulta3']['id']}/sessions").json()
sid = adm_list[0]["id"] if adm_list else None
close = seg.delete(f"{API}/org/usuarios/{cu['qa4_consulta3']['id']}/sessions/{sid}") if sid else None
R.caso("SES-15", "ADMIN_SEGURIDAD lista y cierra la sesion de una cuenta; el usuario pierde acceso",
       sid and close.status_code == 204 and victima.get(f"{API}/me").status_code == 401,
       f"sesiones={len(adm_list)}; close={getattr(close, 'status_code', None)}; victima={victima.get(f'{API}/me').status_code}")

# ---------- Efecto de cambio de contrasena sobre sesiones vivas ----------
u1, _ = entrar("qa4_ses", UA_WIN)
u2, _ = entrar("qa4_ses", UA_MAC)
nueva = cu["qa4_ses"]["clave"] + "9z"
chg = u2.post(f"{API}/me/password", json={"current_password": cu["qa4_ses"]["clave"], "new_password": nueva})
if chg.status_code == 204:
    cu["qa4_ses"]["clave"] = nueva
    from qa4_comun import guardar_cuentas
    guardar_cuentas(cu)
R.caso("SES-16", "Cambiar la contrasena revoca TODAS las sesiones (incluida la que la cambio)",
       chg.status_code == 204 and u1.get(f"{API}/me").status_code == 401 and u2.get(f"{API}/me").status_code == 401,
       f"cambio={chg.status_code}; otra={u1.get(f'{API}/me').status_code}; propia={u2.get(f'{API}/me').status_code}")
print("sesiones OK")

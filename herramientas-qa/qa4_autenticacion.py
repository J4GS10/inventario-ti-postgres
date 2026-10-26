"""
QA4 — Login, enumeración, bloqueo por intentos (contraseña y 2FA), TOTP
anti-replay (también en concurrencia), códigos de recuperación, challenge 2FA,
MFA por correo. Solo cuentas qa4_*. Evidencia: salidas/qa4/autenticacion.json
"""
import hashlib
import statistics
import subprocess
import threading
import time

import pyotp

from qa4_comun import API, Registro, autenticado, cargar_cuentas, cliente, esperar_ventana_fresca, guardar_cuentas, login_totp_una_vez, resumen

R = Registro("autenticacion")
cu = cargar_cuentas()


def psql(sql: str) -> str:
    """Lectura (solo SELECT) en la base del stack de revisión, para evidencias."""
    out = subprocess.run(["docker", "exec", "inv_ui-db-1", "psql", "-U", "invuser", "-d", "inventario",
                          "-t", "-A", "-F", "|", "-c", sql], capture_output=True, text=True)
    return out.stdout.strip()


def estado(usr: str) -> str:
    return psql(f"select \"USU_Intentos_Fallidos\",\"USU_Bloqueado_Hasta\" from \"INV_USUARIO\" where \"USU_Username\"='{usr}'")


def post_login(c, u, p):
    t = time.perf_counter()
    r = c.post(f"{API}/login/access-token", data={"username": u, "password": p})
    return r, (time.perf_counter() - t) * 1000


c = cliente()

# ---------- AUT-01: mensajes genéricos y tiempo ----------
r1, _ = post_login(c, "qa4_zz_noexiste", "Cualquiera12345")
r2, _ = post_login(c, "qa4_lock", "Incorrecta12345")
R.caso("AUT-01", "Usuario inexistente y contraseña errónea dan la misma respuesta",
       (r1.status_code, r1.text) == (r2.status_code, r2.text), f"inexistente={resumen(r1)} | existente={resumen(r2)}",
       "Mismo código y mensaje genérico")
t_no, t_si = [], []
for i in range(6):
    t_no.append(post_login(c, f"qa4_zz_noexiste{i}", "Cualquiera12345")[1])
for i in range(3):  # 3 más → qa4_lock suma 4 fallos (queda en 4, sin bloquear)
    t_si.append(post_login(c, "qa4_lock", "Incorrecta12345")[1])
R.caso("AUT-02", "Tiempo de respuesta: inexistente vs contraseña errónea (hash dummy)",
       abs(statistics.median(t_no) - statistics.median(t_si)) < 60,
       f"mediana inexistente={statistics.median(t_no):.0f} ms, existente={statistics.median(t_si):.0f} ms",
       "Diferencia < 60 ms")

# ---------- AUT-03: bloqueo por contraseña ----------
r5, _ = post_login(c, "qa4_lock", "Incorrecta12345")  # 5º fallo → bloquea
R.caso("AUT-03", "5º fallo de contraseña bloquea la cuenta", "|" in estado("qa4_lock") and estado("qa4_lock").split("|")[1] != "",
       f"respuesta 5º={resumen(r5)}; BD={estado('qa4_lock')}", "USU_Bloqueado_Hasta fijado a +15 min")
r6, _ = post_login(c, "qa4_lock", cu["qa4_lock"]["clave"])
R.caso("AUT-04", "Cuenta bloqueada rechaza incluso la contraseña correcta", r6.status_code == 429,
       resumen(r6), "429 ACCOUNT_LOCKED")
r7, _ = post_login(c, "qa4_lock", "OtraIncorrecta99")
r8, _ = post_login(c, "qa4_zz_noexiste", "OtraIncorrecta99")
R.caso("AUT-05", "Enumeración vía bloqueo: cuenta existente bloqueada vs inexistente",
       (r7.status_code, r7.text) == (r8.status_code, r8.text),
       f"existente bloqueada={resumen(r7)} | inexistente={resumen(r8)}",
       "Misma respuesta (si no, un atacante distingue cuentas reales con 5 intentos)")

# ---------- AUT-06..: 2FA bloqueo (qa4_mfa2) ----------
esperar_ventana_fresca()
r = c.post(f"{API}/login/access-token", data={"username": "qa4_mfa2", "password": cu["qa4_mfa2"]["clave"]})
ch = r.json().get("challenge_token")
R.caso("AUT-06", "Contraseña correcta con MFA → challenge sin tokens", bool(ch) and "access_token" not in r.json(),
       resumen(r, 120))
codigos = []
for i in range(5):
    v = c.post(f"{API}/login/2fa/verify", json={"challenge_token": ch, "code": f"{(123450 + i) % 1000000:06d}"})
    codigos.append(v.status_code)
R.caso("AUT-07", "5 códigos 2FA erróneos bloquean la cuenta", codigos[-1] == 429 and estado("qa4_mfa2").split("|")[1] != "",
       f"códigos={codigos}; BD={estado('qa4_mfa2')}", "400 x4 y 429 ACCOUNT_LOCKED al 5º")
v = c.post(f"{API}/login/2fa/verify", json={"challenge_token": ch, "code": pyotp.TOTP(cu["qa4_mfa2"]["totp"]).now()})
R.caso("AUT-08", "Con la cuenta bloqueada, el TOTP correcto también se rechaza", v.status_code == 429, resumen(v))
v = c.post(f"{API}/login/2fa/verify", json={"challenge_token": ch, "code": cu["qa4_mfa2"]["recovery"][0]})
R.caso("AUT-09", "Con la cuenta bloqueada, un código de recuperación también se rechaza", v.status_code == 429, resumen(v))

# ---------- AUT-10..: anti-replay TOTP (qa4_mfa) ----------
esperar_ventana_fresca()
totp = pyotp.TOTP(cu["qa4_mfa"]["totp"])
codigo = totp.now()
a = cliente()
ra = a.post(f"{API}/login/access-token", data={"username": "qa4_mfa", "password": cu["qa4_mfa"]["clave"]}).json()
va = a.post(f"{API}/login/2fa/verify", json={"challenge_token": ra["challenge_token"], "code": codigo})
b = cliente()
rb = b.post(f"{API}/login/access-token", data={"username": "qa4_mfa", "password": cu["qa4_mfa"]["clave"]}).json()
vb = b.post(f"{API}/login/2fa/verify", json={"challenge_token": rb["challenge_token"], "code": codigo})
R.caso("AUT-10", "Anti-replay: el mismo TOTP no sirve dos veces", va.status_code == 200 and vb.status_code == 400,
       f"1º={va.status_code} 2º={resumen(vb, 80)}")
R.caso("AUT-11", "El replay rechazado NO debería contar como intento fallido (lo sufre el usuario legítimo)",
       estado("qa4_mfa").split("|")[0] == "0", f"BD tras replay: intentos|bloqueo = {estado('qa4_mfa')}",
       "Intentos=0 (un código válido ya usado no es un ataque de adivinación)")

# Replay concurrente: dos verificaciones simultáneas con el mismo código nuevo
esperar_ventana_fresca()
chs = []
for _ in range(4):
    x = cliente()
    chs.append((x, x.post(f"{API}/login/access-token", data={"username": "qa4_mfa", "password": cu["qa4_mfa"]["clave"]}).json()["challenge_token"]))
codigo = totp.now()
res = [None] * len(chs)
barrera = threading.Barrier(len(chs))


def go(i):
    x, t = chs[i]
    barrera.wait()
    res[i] = x.post(f"{API}/login/2fa/verify", json={"challenge_token": t, "code": codigo}).status_code


hs = [threading.Thread(target=go, args=(i,)) for i in range(len(chs))]
[h.start() for h in hs]
[h.join() for h in hs]
R.caso("AUT-12", "Anti-replay en concurrencia: 4 verificaciones simultáneas del mismo TOTP", res.count(200) == 1,
       f"códigos={res}", "Exactamente un 200")

# ---------- AUT-13: el challenge sobrevive a un cambio de contraseña ----------
esperar_ventana_fresca()
x = cliente()
ch_viejo = x.post(f"{API}/login/access-token", data={"username": "qa4_mfa", "password": cu["qa4_mfa"]["clave"]}).json()["challenge_token"]
# El titular cambia su contraseña desde otra sesión (con recuperación, para no gastar TOTP)
y = cliente()
chy = y.post(f"{API}/login/access-token", data={"username": "qa4_mfa", "password": cu["qa4_mfa"]["clave"]}).json()["challenge_token"]
rec = cu["qa4_mfa"]["recovery"].pop(0)
vy = y.post(f"{API}/login/2fa/verify", json={"challenge_token": chy, "code": rec})
guardar_cuentas(cu)
y.headers["Authorization"] = f"Bearer {vy.json()['access_token']}"
nueva = cu["qa4_mfa"]["clave"][::-1] + "Q7"
pc = y.post(f"{API}/me/password", json={"current_password": cu["qa4_mfa"]["clave"], "new_password": nueva})
if pc.status_code == 204:
    cu["qa4_mfa"]["clave"] = nueva
    guardar_cuentas(cu)
time.sleep(31 - int(time.time()) % 30)
vx = x.post(f"{API}/login/2fa/verify", json={"challenge_token": ch_viejo, "code": totp.now()})
R.caso("AUT-13", "Challenge 2FA emitido ANTES de cambiar la contraseña ya no debe servir",
       vx.status_code != 200, f"cambio={pc.status_code}; verify con challenge viejo={resumen(vx, 80)}",
       "401 (el challenge debería invalidarse al cambiar la contraseña / ser de un solo uso)")

# ---------- AUT-14: código de recuperación de un solo uso ----------
z = cliente()
chz = z.post(f"{API}/login/access-token", data={"username": "qa4_mfa", "password": cu["qa4_mfa"]["clave"]}).json()["challenge_token"]
v1 = z.post(f"{API}/login/2fa/verify", json={"challenge_token": chz, "code": rec})
R.caso("AUT-14", "Código de recuperación ya usado se rechaza", v1.status_code == 400, resumen(v1, 80))
rec2 = cu["qa4_mfa"]["recovery"].pop(0)
guardar_cuentas(cu)
v2 = z.post(f"{API}/login/2fa/verify", json={"challenge_token": chz, "code": " " + rec2.lower() + " "})
R.caso("AUT-15", "Código de recuperación válido (normaliza mayúsculas/espacios) funciona una vez", v2.status_code == 200, resumen(v2, 60))
v3 = z.post(f"{API}/login/2fa/verify", json={"challenge_token": chz, "code": rec2})
R.caso("AUT-16", "El mismo challenge + el mismo código de recuperación no se reutilizan", v3.status_code == 400, resumen(v3, 80))
# Reutilizar el challenge con OTRO factor válido: ¿el challenge es de un solo uso?
rec3 = cu["qa4_mfa"]["recovery"].pop(0)
guardar_cuentas(cu)
v4 = z.post(f"{API}/login/2fa/verify", json={"challenge_token": chz, "code": rec3})
R.caso("AUT-17", "Challenge ya canjeado no debería abrir una segunda sesión", v4.status_code != 200, resumen(v4, 60),
       "401 (challenge de un solo uso)")
print("estado qa4_mfa:", estado("qa4_mfa"))

# ---------- AUT-18..: MFA por correo (qa4_email) ----------
e, be = autenticado("qa4_email")
r = e.post(f"{API}/me/2fa/email/setup")
uid = cu["qa4_email"]["id"]


def hash_otp_activo():
    return psql(f"select \"TFC_Code_Hash\" from \"SYS_2FA_CODE\" where \"USU_Usuario\"='{uid}' and not \"TFC_Usado\" order by \"TFC_Creado_En\" desc limit 1")


def romper(h):
    t = time.perf_counter()
    for n in range(1000000):
        s = f"{n:06d}"
        if hashlib.sha256(s.encode()).hexdigest() == h:
            return s, time.perf_counter() - t
    return None, time.perf_counter() - t


h = hash_otp_activo()
codigo, seg = romper(h) if h else (None, 0)
R.caso("AUT-18", "OTP por correo guardado como SHA-256 sin sal: se invierte desde la BD en segundos",
       codigo is None, f"setup={r.status_code}; hash={h[:16]}…; invertido='{codigo}' en {seg:.1f}s",
       "Un volcado de SYS_2FA_CODE no debería revelar códigos vigentes (HMAC con clave o KDF)")
if codigo:
    act = e.post(f"{API}/me/2fa/email/activate", json={"code": codigo})
    R.caso("AUT-19", "Activación de MFA por correo con el código enviado", act.status_code == 200, resumen(act, 60))
    if act.status_code == 200:
        cu["qa4_email"]["recovery"] = act.json()["recovery_codes"]
        cu["qa4_email"]["mfa_email"] = True
        guardar_cuentas(cu)
    # Login con MFA por correo
    k = cliente()
    rk = k.post(f"{API}/login/access-token", data={"username": "qa4_email", "password": cu["qa4_email"]["clave"]}).json()
    h2 = hash_otp_activo()
    cod2, _ = romper(h2)
    malo = k.post(f"{API}/login/2fa/verify", json={"challenge_token": rk["challenge_token"], "code": "000000" if cod2 != "000000" else "111111"})
    ok = k.post(f"{API}/login/2fa/verify", json={"challenge_token": rk["challenge_token"], "code": cod2})
    R.caso("AUT-20", "Login con MFA por correo: método EMAIL, código erróneo 400 y correcto 200",
           rk.get("method") == "EMAIL" and malo.status_code == 400 and ok.status_code == 200,
           f"method={rk.get('method')} malo={malo.status_code} bueno={ok.status_code}")
    k2 = cliente()
    rk2 = k2.post(f"{API}/login/access-token", data={"username": "qa4_email", "password": cu["qa4_email"]["clave"]}).json()
    re_ = k2.post(f"{API}/login/2fa/verify", json={"challenge_token": rk2["challenge_token"], "code": cod2})
    R.caso("AUT-21", "El código de correo ya usado no sirve en un login nuevo", re_.status_code == 400, resumen(re_, 60))
    print("estado qa4_email:", estado("qa4_email"))

# ---------- AUT-22: cuenta desactivada ----------
seg, _ = autenticado("qa4_seg")
d = seg.delete(f"{API}/org/usuarios/{cu['qa4_lock2']['id']}")
r_ok, _ = post_login(c, "qa4_lock2", cu["qa4_lock2"]["clave"])
r_bad, _ = post_login(c, "qa4_lock2", "Incorrecta12345")
R.caso("AUT-22", "Cuenta desactivada: con contraseña correcta INACTIVE_USER; con errónea, mensaje genérico",
       r_ok.status_code == 400 and "INACTIVE_USER" in r_ok.text and "INCORRECT_USERNAME_OR_PASSWORD" in r_bad.text,
       f"desactivar={d.status_code}; correcta={resumen(r_ok, 60)}; errónea={resumen(r_bad, 60)}",
       "Solo revela el estado a quien conoce la contraseña")

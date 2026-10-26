"""
QA4 — Politica de contrasena: longitud minima 10, clases, lista de comunes,
secuencias, datos personales del titular, historial (ultimas 5), reuso de la
actual, y contrasena temporal con cambio obligatorio. Cuenta qa4_pwd.
Evidencia: salidas/qa4/password.json
"""
from qa4_comun import API, Registro, autenticado, cargar_cuentas, cliente, guardar_cuentas, login_totp_una_vez, resumen

R = Registro("password")
import json as _j, os as _o
from qa4_comun import SAL as _S
_prev = _o.path.join(_S, "password.json")
if _o.path.exists(_prev):
    R.casos = [x for x in _j.load(open(_prev, encoding="utf-8")) if x["id"] in ("PWD-20", "PWD-21", "PWD-22", "PWD-23")]
cu = cargar_cuentas()

c, _ = autenticado("qa4_pwd")
actual = cu["qa4_pwd"]["clave"]


def intentar(nueva, cur=None):
    return c.post(f"{API}/me/password", json={"current_password": cur or actual, "new_password": nueva})


# La cuenta se llama qa4_pwd; su persona es "Prueba QacuatroH", correo qa4.pwd@lombardi.dataset.local
CASOS = [
    ("PWD-01", "Menos de 10 caracteres se rechaza", "Ab1!xY2z", False, "min_length"),
    ("PWD-02", "Sin mayuscula se rechaza", "abc123456!def", False, "require_upper"),
    ("PWD-03", "Sin minuscula se rechaza", "ABC123456!DEF", False, "require_lower"),
    ("PWD-04", "Sin digito se rechaza", "AbcdefghIJ!k", False, "require_digit"),
    ("PWD-05", "Contrasena comun ('Password2026') se rechaza", "Password2026", False, "too_common"),
    ("PWD-06", "Comun con leet ('P@ssw0rd2026') se rechaza", "P@ssw0rd2026", False, "too_common"),
    ("PWD-07", "Palabra bloqueada de la empresa ('Lombardi2026!') se rechaza", "Lombardi2026!", False, "contains_blocked_word"),
    ("PWD-08", "Secuencia de teclado ('Qwerty12345!') se rechaza", "Qwerty12345!", False, "sequential"),
    ("PWD-09", "Secuencia numerica ('Abcd012345!X') se rechaza", "Abcd012345!X", False, "sequential"),
    ("PWD-10", "Caracteres repetidos ('Aaaa1111bbbb') se rechaza", "Aaaa1111bbbb", False, "repeated"),
    ("PWD-11", "Contiene el nombre de usuario ('qa4_pwd') se rechaza", "Xqa4_pwdZ99a", False, "contains_username"),
    ("PWD-12", "Contiene el apellido del titular ('QacuatroH') se rechaza", "QacuatroH99xkZ", False, "contains_personal_data"),
    ("PWD-13", "Contiene el nombre del titular ('Prueba') se rechaza", "Prueba99xkZq", False, "contains_personal_data"),
]
for cid, titulo, pw, debe_ok, marca in CASOS:
    r = intentar(pw)
    if debe_ok is None:
        R.caso(cid, titulo, True, resumen(r, 120), "informativo")
        continue
    ok = (r.status_code == 400) and (marca in r.text)
    R.caso(cid, titulo, ok, resumen(r, 160), f"400 con '{marca}'")

# ---------- Contrasena valida y luego historial ----------
buenas = ["Zorro-Mesa-91xQ", "Trueno-Lago-72kP", "Nieve-Roca-33vT", "Brisa-Coral-88mN",
          "Faro-Duna-57bR", "Cima-Vela-46jW"]
r = intentar(buenas[0])
R.caso("PWD-14", "Contrasena robusta y sin datos personales se acepta", r.status_code == 204, resumen(r, 120))
if r.status_code == 204:
    actual = buenas[0]
    cu["qa4_pwd"]["clave"] = actual
    guardar_cuentas(cu)
    # Re-login (el cambio revoco los tokens)
    c = cliente()
    b = login_totp_una_vez(c, "qa4_pwd", actual, None)
    c.headers["Authorization"] = f"Bearer {b['access_token']}"

    def intentar2(nueva):
        return c.post(f"{API}/me/password", json={"current_password": actual, "new_password": nueva})

    # Reutilizar la actual
    r = intentar2(actual)
    R.caso("PWD-15", "No se puede fijar la misma contrasena actual", r.status_code == 400 and "SAME_AS_OLD" in r.text, resumen(r, 100))
    # Contrasena actual erronea
    r = c.post(f"{API}/me/password", json={"current_password": "ErroneaTotal99", "new_password": buenas[1]})
    R.caso("PWD-16", "Cambio con contrasena actual incorrecta se rechaza", r.status_code == 400 and "INCORRECT_CURRENT" in r.text, resumen(r, 100))
    # Rotar 5 veces para llenar el historial
    hist_ok = True
    for nb in buenas[1:6]:
        rr = intentar2(nb)
        if rr.status_code != 204:
            hist_ok = False
            break
        actual = nb
        cu["qa4_pwd"]["clave"] = actual
        guardar_cuentas(cu)
        c = cliente()
        b = login_totp_una_vez(c, "qa4_pwd", actual, None)
        c.headers["Authorization"] = f"Bearer {b['access_token']}"

        def intentar2(nueva, _c=c, _a=actual):  # noqa
            return _c.post(f"{API}/me/password", json={"current_password": _a, "new_password": nueva})
    R.caso("PWD-17", "Rotacion de 5 contrasenas nuevas (llenar historial)", hist_ok, f"actual={actual}")
    # Intentar volver a la 2a mas reciente del historial (buenas[1]) -> reuso
    r = intentar2(buenas[1])
    R.caso("PWD-18", "Reutilizar una de las ultimas 5 contrasenas se rechaza (historial)",
           r.status_code == 400 and "REUSED" in r.text, resumen(r, 100), "400 PASSWORD_REUSED:5")
    # buenas[0] ya salio de la ventana de 5 -> deberia aceptarse
    r = intentar2(buenas[0])
    R.caso("PWD-19", "Una contrasena que ya salio de la ventana de historial (>5) se acepta",
           r.status_code == 204, resumen(r, 100), "204 (buenas[0] ya no esta en las ultimas 5)")
    if r.status_code == 204:
        cu["qa4_pwd"]["clave"] = buenas[0]
        guardar_cuentas(cu)

# ---------- Contrasena temporal: cambio obligatorio ----------
# qa4_temp quedo con temporal (RES-05). Verificamos que no puede usar la API hasta cambiarla.
tmp = cu["qa4_temp"].get("temporal_actual")
if tmp:
    t = cliente()
    b = login_totp_una_vez(t, "qa4_temp", tmp, None)
    t.headers["Authorization"] = f"Bearer {b['access_token']}"
    bloqueado = t.get(f"{API}/org/personas")
    R.caso("PWD-20", "Sesion con contrasena temporal no puede usar la API de negocio",
           b.get("restriction") == "PASSWORD_CHANGE_REQUIRED" and bloqueado.status_code == 403,
           f"restriction={b.get('restriction')}; /org/personas={bloqueado.status_code}")
    # Cambiarla desde la sesion restringida
    nueva = "Puente-Roble-64xZ"
    r = t.post(f"{API}/me/password", json={"current_password": tmp, "new_password": nueva})
    R.caso("PWD-21", "Se puede cambiar la contrasena temporal desde la sesion restringida", r.status_code == 204, resumen(r, 100))
    if r.status_code == 204:
        cu["qa4_temp"]["clave"] = nueva
        cu["qa4_temp"].pop("temporal_actual", None)
        guardar_cuentas(cu)
        t2 = cliente()
        b2 = login_totp_una_vez(t2, "qa4_temp", nueva, None)
        R.caso("PWD-22", "Tras cambiar la temporal, la sesion ya no esta restringida y la API responde",
               b2.get("restriction") is None and t2.get(f"{API}/org/personas", headers={"Authorization": f"Bearer {b2['access_token']}"}).status_code == 200,
               f"restriction={b2.get('restriction')}")
    # La temporal ya no sirve
    r = cliente().post(f"{API}/login/access-token", data={"username": "qa4_temp", "password": tmp})
    R.caso("PWD-23", "La contrasena temporal ya usada/cambiada no permite iniciar sesion",
           r.status_code in (400, 429), resumen(r, 100))
print("password OK")

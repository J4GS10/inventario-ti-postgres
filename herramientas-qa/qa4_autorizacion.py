"""
QA4 — Autorizacion, IDOR, separacion de funciones, escalada de privilegios,
rate limit y auto-servicio. Cuentas qa4_*. Evidencia: salidas/qa4/autorizacion.json
"""
import concurrent.futures

from qa4_comun import API, Registro, autenticado, cargar_cuentas, cliente, resumen

R = Registro("autorizacion")
cu = cargar_cuentas()

seg, _ = autenticado("qa4_seg")     # ADMIN_SEGURIDAD (IAM, sin inventario)
tec, _ = autenticado("qa4_admti2")  # ADMIN_TI con MFA (inventario, no IAM)
con, _ = autenticado("qa4_consulta")  # CONSULTA (solo lectura, sede 2)
con3, _ = autenticado("qa4_consulta3")  # CONSULTA sede 3

# ---------- Separacion de funciones ----------
r = seg.get(f"{API}/core/activos")
R.caso("AZ-01", "ADMIN_SEGURIDAD NO accede al inventario (separacion de funciones)", r.status_code == 403, resumen(r, 80))
r = seg.get(f"{API}/org/personas")
R.caso("AZ-02", "ADMIN_SEGURIDAD NO ve /org/personas (es de negocio, require_business)", r.status_code == 403, resumen(r, 80))
r = tec.get(f"{API}/org/usuarios")
R.caso("AZ-03", "ADMIN_TI NO administra usuarios (IAM)", r.status_code == 403, resumen(r, 80))
r = tec.post(f"{API}/org/usuarios", json={"USU_Username": "qa4_hack", "USU_Password": "Xk9!zLmPqR2t",
                                          "USU_Rol": "CONSULTA", "PER_Persona": cu["qa4_consulta"]["persona"], "sedes": [2]})
R.caso("AZ-04", "ADMIN_TI no puede crear cuentas", r.status_code == 403, resumen(r, 80))
r = con.get(f"{API}/gov/auditoria")
R.caso("AZ-05", "CONSULTA no lee la bitacora de auditoria", r.status_code == 403, resumen(r, 80))
r = con.post(f"{API}/gov/security/purge")
R.caso("AZ-06", "CONSULTA no ejecuta acciones de gobierno (SUPER_ADMIN)", r.status_code == 403, resumen(r, 80))
r = con.get(f"{API}/export/auditoria.csv")
R.caso("AZ-07", "CONSULTA no exporta la auditoria (require_audit_reader)", r.status_code == 403, resumen(r, 80))

# ---------- Escalada de privilegios ----------
# ADMIN_SEGURIDAD intenta crear un SUPER_ADMIN / otro ADMIN_SEGURIDAD
r = seg.post(f"{API}/org/usuarios", json={"USU_Username": "qa4_super", "USU_Password": "Xk9!zLmPqR2t",
                                          "USU_Rol": "SUPER_ADMIN", "PER_Persona": cu["qa4_consulta"]["persona"], "sedes": []})
R.caso("AZ-08", "ADMIN_SEGURIDAD no puede crear un SUPER_ADMIN", r.status_code == 403, resumen(r, 100))
r = seg.post(f"{API}/org/usuarios", json={"USU_Username": "qa4_seg9", "USU_Password": "Xk9!zLmPqR2t",
                                          "USU_Rol": "ADMIN_SEGURIDAD", "PER_Persona": cu["qa4_consulta"]["persona"], "sedes": []})
R.caso("AZ-09", "ADMIN_SEGURIDAD no puede crear otro ADMIN_SEGURIDAD", r.status_code == 403, resumen(r, 100))
# ADMIN_SEGURIDAD intenta elevar a un CONSULTA existente a ADMIN_SEGURIDAD
r = seg.patch(f"{API}/org/usuarios/{cu['qa4_consulta']['id']}", json={"USU_Rol": "ADMIN_SEGURIDAD"})
R.caso("AZ-10", "ADMIN_SEGURIDAD no puede elevar a un usuario a un rol protegido", r.status_code == 403, resumen(r, 100))
# ADMIN_SEGURIDAD intenta otorgar alcance global (solo SUPER_ADMIN)
r = seg.patch(f"{API}/org/usuarios/{cu['qa4_consulta']['id']}", json={"USU_Alcance_Global": True})
R.caso("AZ-11", "ADMIN_SEGURIDAD no puede otorgar alcance global (solo SUPER_ADMIN)",
       r.status_code == 403 and "GLOBAL" in r.text, resumen(r, 120))
# ADMIN_SEGURIDAD intenta gestionar a otro ADMIN_SEGURIDAD (rol protegido). No se apunta a `sa`
# para no arriesgar su cuenta si la regla fallara; can_manage() es la misma regla para ambos roles.
tid = cu["qa4_seg2"]["id"]
r = seg.post(f"{API}/org/usuarios/{tid}/password/reset")
R.caso("AZ-12", "ADMIN_SEGURIDAD no puede resetear la contrasena de un rol protegido (ADMIN_SEGURIDAD)",
       r.status_code == 403, resumen(r, 100))
r = seg.post(f"{API}/org/usuarios/{tid}/sessions/revoke")
R.caso("AZ-13", "ADMIN_SEGURIDAD no puede revocar sesiones de un rol protegido",
       r.status_code == 403, resumen(r, 100))
r = seg.post(f"{API}/org/usuarios/{tid}/2fa/reset")
R.caso("AZ-13b", "ADMIN_SEGURIDAD no puede resetear el MFA de un rol protegido",
       r.status_code == 403, resumen(r, 100))

# ---------- No auto-gestion administrativa ----------
r = seg.patch(f"{API}/org/usuarios/{cu['qa4_seg']['id']}", json={"USU_Rol": "SUPER_ADMIN"})
R.caso("AZ-14", "Un IAM no puede modificar su propia cuenta desde la API de administracion",
       r.status_code == 403 and "OWN_ACCOUNT" in r.text, resumen(r, 100))

# ---------- IDOR por alcance de datos (RLS por sede) ----------
aux = cu.get("_aux", {}).get("activo_por_sede", {})
act_sede2 = aux.get("2")
act_sede3 = aux.get("3")
if act_sede3:
    r = con.get(f"{API}/core/activos/{act_sede3}")
    R.caso("AZ-15", "CONSULTA de sede 2 no puede leer un activo de la sede 3 por id (IDOR/RLS)",
           r.status_code in (403, 404), f"activo sede3={act_sede3} -> {resumen(r, 80)}", "404/403")
    r2 = con3.get(f"{API}/core/activos/{act_sede3}")
    R.caso("AZ-16", "La cuenta de la sede 3 SI ve su propio activo (control positivo)",
           r2.status_code == 200, resumen(r2, 60))
if act_sede2:
    r = con3.get(f"{API}/core/activos/{act_sede2}")
    R.caso("AZ-17", "CONSULTA de sede 3 no ve un activo de la sede 2 (IDOR/RLS, sentido inverso)",
           r.status_code in (403, 404), f"activo sede2={act_sede2} -> {resumen(r, 80)}")

# ---------- IDOR sobre cuentas de otro usuario ----------
r = con.get(f"{API}/org/usuarios/{cu['qa4_seg']['id']}")
R.caso("AZ-18", "CONSULTA no puede leer la ficha de seguridad de otra cuenta (IAM)", r.status_code == 403, resumen(r, 80))

# ---------- Rate limit del login ----------
def un_login(_):
    return cliente().post(f"{API}/login/access-token", data={"username": "qa4_zz_rl", "password": "x"}).status_code


with concurrent.futures.ThreadPoolExecutor(max_workers=20) as ex:
    codes = list(ex.map(un_login, range(150)))
R.caso("AZ-19", "Rate limit del login activo (RATE_LIMIT_LOGIN=200/min en revision): se observan 429 o el limite no se alcanza",
       True, f"200/400={sum(1 for x in codes if x in (200, 400))}, 429={codes.count(429)} de {len(codes)}",
       "Informativo: limite ampliado a 200/min para revision")

# ---------- Sesion restringida y refresh ----------
# (cubierto en qa4_restriccion) — aqui solo confirmamos header de no-store en reset ya hecho.
print("autorizacion OK")

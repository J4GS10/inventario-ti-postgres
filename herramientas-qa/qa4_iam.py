"""
QA4 — Gobierno de identidades (jerarquía de roles), IDOR por id, cierre de
sesiones (propio, ajeno y por administrador), cierre global, historial y
política de contraseñas, y bitácora sin secretos. Solo cuentas qa4_*.
Evidencia: salidas/qa4/iam.json
"""
import json
import subprocess
import time

from qa4_comun import API, Registro, autenticado, cargar_cuentas, cliente, guardar_cuentas, login_totp_una_vez, resumen

R = Registro("iam")
cu = cargar_cuentas()


def psql(sql):
    return subprocess.run(["docker", "exec", "inv_ui-db-1", "psql", "-U", "invuser", "-d", "inventario", "-t", "-A",
                           "-F", "|", "-c", sql], capture_output=True, text=True).stdout.strip()


seg, _ = autenticado("qa4_seg")
seg_id, seg2_id = cu["qa4_seg"]["id"], cu["qa4_seg2"]["id"]
cons_id = cu["qa4_consulta"]["id"]

# ---------- Jerarquía ----------
per = cu["qa4_email"]["persona"]
r = seg.post(f"{API}/org/usuarios", json={"USU_Username": "qa4_escalada", "USU_Password": "Zr8#kWq2mPx!",
                                          "USU_Rol": "SUPER_ADMIN", "PER_Persona": per})
R.caso("IAM-01", "ADMIN_SEGURIDAD no puede crear un SUPER_ADMIN", r.status_code == 403, resumen(r, 80))
for cid, (m, p, body) in {
    "IAM-02": ("PATCH", f"/org/usuarios/{seg2_id}", {"USU_Estado": False}),
    "IAM-03": ("POST", f"/org/usuarios/{seg2_id}/password/reset", None),
    "IAM-04": ("POST", f"/org/usuarios/{seg2_id
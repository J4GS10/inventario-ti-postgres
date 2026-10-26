"""QA7 — Cortacircuitos del correo tras credenciales rechazadas.
Usa un SMTP de prueba (dc01.empresa.local:2525 dentro de qa7_samba) que anuncia AUTH y
responde 535 a toda credencial.

Uso (cwd=herramientas-qa): python -u qa7_cortacircuitos.py
"""
import json
import subprocess
import time

from qa7_comun import esperar_correos, get, guardar, integ_base, mh_borrar, psql, req, cliente

R: dict = {}
c = cliente()
MALO = integ_base(smtp_host="dc01.empresa.local", smtp_puerto=2525, cuenta_password="Clave-Rechazada-Qa7!")
BUENO = integ_base(cuenta_password=None)


def ahora():
    return psql("select (now() at time zone 'utc')::text")


def filas(desde):
    s = psql('select "EOB_Id","EOB_Estado","EOB_Intentos","EOB_Proximo_Intento",left("EOB_Ultimo_Error",80),"EOB_Enviado_En" '
             f'from "SYS_EMAIL_OUTBOX" where "EOB_Plantilla"=\'garantia_por_vencer\' and "EOB_Creado_En">=\'{desde}\' order by "EOB_Creado_En"')
    return [dict(zip(["id", "estado", "intentos", "proximo", "error", "enviado"], l.split("|"))) for l in s.splitlines()]


def disparar():
    return req(c, "POST", "/compras/garantias/notificar", params={"dias": 7}).status_code


def auth_intentos():
    out = subprocess.run(["docker", "exec", "qa7_samba", "sh", "-c", "grep -c '^CMD AUTH' /tmp/smtp_auth.log || true"],
                         capture_output=True, text=True).stdout.strip()
    return int(out or 0)


# B1: prueba desde la UI con clave rechazada (no debería pausar, según el código: use_breaker=False)
r = req(c, "POST", "/directorio/integracion/probar-correo", json={"config": MALO, "destinatario": "qa7.prueba@empresa.local"})
R["B1_probar_clave_mala"] = r.json()
R["B1_bloqueo_tras_prueba"] = get(c, "/directorio/integracion").json()["bloqueo"]

# B2: guardar la clave mala y generar correos
t0 = ahora()
R["B2_guardar"] = req(c, "PUT", "/directorio/integracion", json=MALO).status_code
a0 = auth_intentos()
disparar()
time.sleep(10)
R["B2_fila_tras_fallo"] = filas(t0)
R["B2_bloqueo"] = [get(c, "/directorio/integracion").json()["bloqueo"]["smtp_hasta"] for _ in range(6)]
R["B2_hora_utc"] = ahora()
disparar()
disparar()
time.sleep(40)
R["B3_filas_durante_pausa"] = filas(t0)
R["B3_intentos_auth_al_servidor"] = auth_intentos() - a0

# B4: "Reanudar ahora" sin cambiar la clave → ¿vuelve a intentar con la clave mala?
R["B4_desbloquear"] = req(c, "POST", "/directorio/integracion/desbloquear").status_code
R["B4_bloqueo_tras_desbloquear_x8"] = [get(c, "/directorio/integracion").json()["bloqueo"]["smtp_hasta"] for _ in range(8)]
a1 = auth_intentos()
disparar()
time.sleep(40)
R["B4_filas"] = filas(t0)
R["B4_intentos_auth_tras_reanudar"] = auth_intentos() - a1

# B5: corregir la configuración (MailHog) → ¿cuándo salen los correos retenidos?
mh_borrar()
t_fix = time.time()
R["B5_guardar_buena"] = req(c, "PUT", "/directorio/integracion", json=BUENO).status_code
R["B5_hora_utc_correccion"] = ahora()
entregados = {}
fin = time.time() + 240
while time.time() < fin:
    fs = filas(t0)
    for f in fs:
        if f["estado"] == "ENVIADO" and f["id"] not in entregados:
            entregados[f["id"]] = round(time.time() - t_fix)
    if fs and all(f["estado"] == "ENVIADO" for f in fs):
        break
    time.sleep(10)
R["B5_segundos_hasta_envio"] = entregados
R["B5_filas_final"] = filas(t0)
guardar("cortacircuitos.json", R)
print(json.dumps(R, ensure_ascii=False, indent=1, default=str))

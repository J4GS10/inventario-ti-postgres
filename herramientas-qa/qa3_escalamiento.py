"""
QA3 — Escalamiento de privilegios y operaciones sobre la cuenta propia.

- ADMIN_TI intenta otorgarse SUPER_ADMIN / alcance global / crear admins.
- ADMIN_SEGURIDAD intenta otorgar global o roles protegidos, gestionar a otro
  ADMIN_SEGURIDAD y operar sobre su propia cuenta.
- Cuentas de inventario sobre su propia cuenta (/me): asignación masiva,
  desactivar MFA obligatorio, sesiones de otra cuenta.
- Token manipulado; cambio de alcance con efecto inmediato.
Resultados: salidas/qa3/escalamiento_resultados.json

Uso: python qa3_escalamiento.py
"""
import base64
import json
import os
import time
import warnings

import httpx

warnings.filterwarnings("ignore")
from qa_login import BASE, login  # noqa: E402
from qa3_comun import sa_cliente  # noqa: E402

AQUI = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(AQUI, "salidas", "qa3")
ESTF = os.path.join(OUT, "estado.json")
EST = json.load(open(ESTF, encoding="utf-8"))
U = EST["usuarios"]
SED = EST["sedes"]
RES = []
TEMP = "Qa3-Temporal#Rv2609"


def reg(actor, caso, esperado, r, ok=None, detalle=None):
    obtenido = r.status_code if isinstance(r, httpx.Response) else r
    if ok is None:
        ok = str(obtenido) in str(esperado).split("/")
    det = detalle if detalle is not None else (r.text[:250] if isinstance(r, httpx.Response) else "")
    RES.append({"actor": actor, "caso": caso, "esperado": esperado, "obtenido": obtenido, "ok": bool(ok),
                "detalle": str(det)[:400]})
    print(f"{'OK ' if ok else 'XX '} {actor:10} {caso:70} {obtenido} {'' if ok else str(det)[:200]}", flush=True)


def cli(clave):
    u = U[clave]
    c = httpx.Client(verify=False, timeout=60)
    c.headers["Authorization"] = "Bearer " + login(c, u["username"], u["clave"], u.get("totp"))["access_token"]
    return c


def main():
    sa = sa_cliente()
    # Segundo ADMIN_SEGURIDAD (par de qa3_seg) creado por sa.
    if "qa3_seg2" not in U:
        per = sa.post(f"{BASE}/org/personas", json={
            "PER_Primer_Nombre": "Cuenta", "PER_Primer_Apellido": "Qa3segdos",
            "PER_Email_Corporativo": f"qa3_seg2.{EST['suf']}@lombardi-qa.com",
            "DEP_Departamento": 2, "CAR_Cargo": 2, "SED_Sede": SED["N"]["sede"]}).json()
        u2 = sa.post(f"{BASE}/org/usuarios", json={"USU_Username": f"qa3_seg2_{EST['suf']}", "USU_Password": TEMP,
                                                   "USU_Rol": "ADMIN_SEGURIDAD", "PER_Persona": per["PER_Persona"]}).json()
        U["qa3_seg2"] = {"username": u2["USU_Username"], "id": u2["USU_Usuario"], "rol": "ADMIN_SEGURIDAD",
                         "persona": per["PER_Persona"], "clave": TEMP, "totp": None}
        json.dump(EST, open(ESTF, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    seg2 = U["qa3_seg2"]["id"]

    # ── ADMIN_TI ─────────────────────────────────────────────────────────
    ti = cli("qa3_ti_n")
    me = U["qa3_ti_n"]["id"]
    reg("ti_n", "PATCH propia cuenta USU_Rol=SUPER_ADMIN", "403",
        ti.patch(f"{BASE}/org/usuarios/{me}", json={"USU_Rol": "SUPER_ADMIN"}))
    reg("ti_n", "PATCH propia cuenta USU_Alcance_Global=true", "403",
        ti.patch(f"{BASE}/org/usuarios/{me}", json={"USU_Alcance_Global": True}))
    reg("ti_n", "PATCH propia cuenta sedes=[N,S,E]", "403",
        ti.patch(f"{BASE}/org/usuarios/{me}", json={"sedes": [s["sede"] for s in SED.values()]}))
    reg("ti_n", "POST /org/usuarios crear SUPER_ADMIN", "403", ti.post(f"{BASE}/org/usuarios", json={
        "USU_Username": "qa3_x_sa", "USU_Password": TEMP, "USU_Rol": "SUPER_ADMIN", "PER_Persona": SED["N"]["persona"]}))
    reg("ti_n", "PATCH otra cuenta (tec_n) a global", "403",
        ti.patch(f"{BASE}/org/usuarios/{U['qa3_tec_n']['id']}", json={"USU_Alcance_Global": True}))
    reg("ti_n", "GET /org/usuarios (listar cuentas)", "403", ti.get(f"{BASE}/org/usuarios"))
    reg("ti_n", "POST reset contraseña de otra cuenta", "403",
        ti.post(f"{BASE}/org/usuarios/{U['qa3_tec_n']['id']}/password/reset"))
    reg("ti_n", "PUT /gov/config (config global)", "403", ti.put(f"{BASE}/gov/config", json={}))
    reg("ti_n", "POST /me/2fa/disable (MFA obligatorio de su rol)", "403/400",
        ti.post(f"{BASE}/me/2fa/disable", json={"password": U["qa3_ti_n"]["clave"]}))
    r = ti.patch(f"{BASE}/org/personas/{U['qa3_ti_n']['persona']}", json={"SED_Sede": SED["E"]["sede"]})
    reg("ti_n", "mover su propia persona a sede ajena (E)", "403", r)
    reg("ti_n", "POST /geo/sedes (crear sede, solo global)", "403",
        ti.post(f"{BASE}/geo/sedes", json={"SED_Nombre": "QA3-X", "MUN_Municipio": 1}))
    # ADMIN_TI global
    tig = cli("qa3_ti_g")
    reg("ti_g", "PATCH propia cuenta USU_Rol=SUPER_ADMIN", "403",
        tig.patch(f"{BASE}/org/usuarios/{U['qa3_ti_g']['id']}", json={"USU_Rol": "SUPER_ADMIN"}))
    reg("ti_g", "PATCH con_n → alcance global", "403",
        tig.patch(f"{BASE}/org/usuarios/{U['qa3_con_n']['id']}", json={"USU_Alcance_Global": True}))

    # ── ADMIN_SEGURIDAD ─────────────────────────────────────────────────
    seg = cli("qa3_seg")
    yo = U["qa3_seg"]["id"]
    tgt = U["qa3_con_n"]["id"]
    reg("seg", "otorgar alcance global a con_n", "403",
        seg.patch(f"{BASE}/org/usuarios/{tgt}", json={"USU_Alcance_Global": True}))
    reg("seg", "otorgar USU_Rol=SUPER_ADMIN a con_n", "403",
        seg.patch(f"{BASE}/org/usuarios/{tgt}", json={"USU_Rol": "SUPER_ADMIN"}))
    reg("seg", "otorgar USU_Rol=ADMIN_SEGURIDAD a con_n", "403",
        seg.patch(f"{BASE}/org/usuarios/{tgt}", json={"USU_Rol": "ADMIN_SEGURIDAD"}))
    reg("seg", "crear cuenta CONSULTA con alcance global", "403", seg.post(f"{BASE}/org/usuarios", json={
        "USU_Username": f"qa3_x_glob_{EST['suf']}", "USU_Password": TEMP, "USU_Rol": "CONSULTA",
        "PER_Persona": SED["S"]["persona"], "USU_Alcance_Global": True}))
    reg("seg", "crear cuenta ADMIN_SEGURIDAD", "403", seg.post(f"{BASE}/org/usuarios", json={
        "USU_Username": f"qa3_x_seg_{EST['suf']}", "USU_Password": TEMP, "USU_Rol": "ADMIN_SEGURIDAD",
        "PER_Persona": SED["S"]["persona"]}))
    reg("seg", "PATCH propia cuenta (USU_Rol=SUPER_ADMIN)", "403",
        seg.patch(f"{BASE}/org/usuarios/{yo}", json={"USU_Rol": "SUPER_ADMIN"}))
    reg("seg", "PATCH propia cuenta (desactivarse)", "403",
        seg.patch(f"{BASE}/org/usuarios/{yo}", json={"USU_Estado": False}))
    reg("seg", "reset de su propia contraseña por IAM", "403", seg.post(f"{BASE}/org/usuarios/{yo}/password/reset"))
    reg("seg", "reset de su propio MFA por IAM", "403", seg.post(f"{BASE}/org/usuarios/{yo}/2fa/reset"))
    reg("seg", "revocar sus propias sesiones por IAM", "403", seg.post(f"{BASE}/org/usuarios/{yo}/sessions/revoke"))
    reg("seg", "desactivar su propia cuenta (DELETE)", "403", seg.delete(f"{BASE}/org/usuarios/{yo}"))
    reg("seg", "PATCH otro ADMIN_SEGURIDAD (seg2) desactivar", "403",
        seg.patch(f"{BASE}/org/usuarios/{seg2}", json={"USU_Estado": False}))
    reg("seg", "reset contraseña de otro ADMIN_SEGURIDAD (seg2)", "403",
        seg.post(f"{BASE}/org/usuarios/{seg2}/password/reset"))
    reg("seg", "reset MFA de otro ADMIN_SEGURIDAD (seg2)", "403", seg.post(f"{BASE}/org/usuarios/{seg2}/2fa/reset"))
    reg("seg", "revocar sesiones de otro ADMIN_SEGURIDAD (seg2)", "403",
        seg.post(f"{BASE}/org/usuarios/{seg2}/sessions/revoke"))
    reg("seg", "dejar a tec_n sin sedes ni global", "400",
        seg.patch(f"{BASE}/org/usuarios/{U['qa3_tec_n']['id']}", json={"sedes": []}))
    reg("seg", "PUT /gov/config (solo SUPER_ADMIN)", "403", seg.put(f"{BASE}/gov/config", json={}))
    # Casos límite de la regla "solo SUPER_ADMIN otorga el global"
    todas = [s["SED_Sede"] for s in sa.get(f"{BASE}/org/sedes").json()]
    r = seg.patch(f"{BASE}/org/usuarios/{U['qa3_con_n']['id']}", json={"sedes": todas})
    reg("seg", f"asignar TODAS las sedes ({len(todas)}) a con_n (global de facto)", "403/200 (observación)", r,
        ok=True, detalle=f"{r.status_code} {r.text[:200]}")
    if r.status_code == 200:
        seg.patch(f"{BASE}/org/usuarios/{U['qa3_con_n']['id']}", json={"sedes": [SED["N"]["sede"]]})
    r = seg.patch(f"{BASE}/org/usuarios/{U['qa3_tec_g']['id']}", json={"USU_Rol": "ADMIN_TI"})
    glob = r.json().get("USU_Alcance_Global") if r.is_success else None
    reg("seg", "promover TECNICO global a ADMIN_TI (hereda el global)", "403 o sin global", r,
        ok=not (r.is_success and glob), detalle=f"{r.status_code} global={glob}")
    if r.is_success:
        seg.patch(f"{BASE}/org/usuarios/{U['qa3_tec_g']['id']}", json={"USU_Rol": "TECNICO"})
    reg("seg", "GET /core/activos (sin inventario)", "403", seg.get(f"{BASE}/core/activos"))
    reg("seg", "GET /gov/auditoria (lectura permitida)", "200", seg.get(f"{BASE}/gov/auditoria?limit=5"))

    # ── Cuentas de inventario sobre su propia cuenta ─────────────────────
    tec = cli("qa3_tec_n")
    r = tec.post(f"{BASE}/me/password", json={"current_password": U["qa3_tec_n"]["clave"],
                                              "new_password": U["qa3_tec_n"]["clave"],
                                              "USU_Rol": "SUPER_ADMIN", "USU_Alcance_Global": True})
    me = tec.get(f"{BASE}/me").json()
    reg("tec_n", "POST /me/password con campos extra (rol/global)", "rol y alcance sin cambios",
        f"{r.status_code} → {me.get('role')} {me.get('scope')}",
        ok=me.get("role") == "TECNICO" and not me["scope"]["global"], detalle=r.text[:200])
    reg("tec_n", "PATCH /org/usuarios/{propio} (no es IAM)", "403",
        tec.patch(f"{BASE}/org/usuarios/{U['qa3_tec_n']['id']}", json={"USU_Rol": "ADMIN_TI"}))
    reg("tec_n", "PATCH /org/personas/{propia} (no es admin)", "403",
        tec.patch(f"{BASE}/org/personas/{U['qa3_tec_n']['persona']}", json={"SED_Sede": SED["E"]["sede"]}))
    for metodo in ("PATCH", "PUT", "POST"):
        r = tec.request(metodo, f"{BASE}/me", json={"role": "SUPER_ADMIN", "USU_Rol": "SUPER_ADMIN"})
        reg("tec_n", f"{metodo} /me con rol (método no soportado)", "405/404/403", r)
    # Sesiones de otra cuenta vía /me/sessions/{id}
    sesiones_aud = cli("qa3_aud_n").get(f"{BASE}/me/sessions").json()
    sid = (sesiones_aud[0].get("SES_Sesion") or sesiones_aud[0].get("id") or sesiones_aud[0].get("sid")) if sesiones_aud else None
    if sid:
        r = tec.delete(f"{BASE}/me/sessions/{sid}")
        reg("tec_n", "cerrar sesión de OTRA cuenta vía /me/sessions/{id}", "404/403", r)
    else:
        reg("tec_n", "cerrar sesión de otra cuenta", "404/403", "sin sid", ok=True, detalle=str(sesiones_aud)[:200])

    # ── Token manipulado ──────────────────────────────────────────────────
    tok = tec.headers["Authorization"].split()[1]
    h, p, s = tok.split(".")
    datos = json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))
    for k in ("role", "rol", "USU_Rol"):
        if k in datos:
            datos[k] = "SUPER_ADMIN"
    datos["sub"] = "sa" if "sub" in datos else datos.get("sub")
    p2 = base64.urlsafe_b64encode(json.dumps(datos).encode()).decode().rstrip("=")
    r = httpx.get(f"{BASE}/org/usuarios", headers={"Authorization": f"Bearer {h}.{p2}.{s}"}, verify=False)
    reg("tec_n", "JWT con sub=sa / rol alterado (firma original)", "401", r)
    hn = base64.urlsafe_b64encode(json.dumps({"alg": "none", "typ": "JWT"}).encode()).decode().rstrip("=")
    r = httpx.get(f"{BASE}/me", headers={"Authorization": f"Bearer {hn}.{p2}."}, verify=False)
    reg("tec_n", "JWT alg=none", "401", r)

    # ── Cambio de alcance con efecto inmediato ────────────────────────────
    con = cli("qa3_con_n")
    antes = con.get(f"{BASE}/core/activos/{SED['S']['activo']}").status_code
    r = sa.patch(f"{BASE}/org/usuarios/{U['qa3_con_n']['id']}", json={"sedes": [SED["S"]["sede"]]})
    ya_s = con.get(f"{BASE}/core/activos/{SED['S']['activo']}").status_code
    ya_n = con.get(f"{BASE}/core/activos/{SED['N']['activo']}").status_code
    reg("sa→con_n", "cambio de sedes N→S rige de inmediato con el mismo token",
        "antes 404 / luego S=200, N=404", f"{antes} / S={ya_s}, N={ya_n}",
        ok=(antes == 404 and ya_s == 200 and ya_n == 404), detalle=r.status_code)
    sa.patch(f"{BASE}/org/usuarios/{U['qa3_con_n']['id']}", json={"sedes": [SED["N"]["sede"]]})
    r = sa.patch(f"{BASE}/org/usuarios/{U['qa3_tec_n']['id']}", json={"USU_Rol": "CONSULTA"})
    w = tec.patch(f"{BASE}/core/activos/{SED['N']['activo2']}", json={"ACT_Hostname": "QA3-DEMOTED"})
    reg("sa→tec_n", "degradar TECNICO→CONSULTA: token vigente ya no escribe", "403", w)
    sa.patch(f"{BASE}/org/usuarios/{U['qa3_tec_n']['id']}", json={"USU_Rol": "TECNICO"})
    aud = cli("qa3_aud_n")
    sa.patch(f"{BASE}/org/usuarios/{U['qa3_aud_n']['id']}", json={"USU_Estado": False})
    reg("sa→aud_n", "cuenta desactivada: token vigente rechazado", "401/403",
        aud.get(f"{BASE}/core/activos?limit=1"))
    sa.patch(f"{BASE}/org/usuarios/{U['qa3_aud_n']['id']}", json={"USU_Estado": True})
    time.sleep(1)
    json.dump(RES, open(os.path.join(OUT, "escalamiento_resultados.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"\n{sum(r['ok'] for r in RES)}/{len(RES)} correctos")


if __name__ == "__main__":
    main()

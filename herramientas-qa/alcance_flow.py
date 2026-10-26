"""
Recorrido real (navegador + API) del alcance por sede, el rol AUDITOR, el
autocompletado de sede y las sesiones activas, contra el entorno de revisión.

Uso: python alcance_flow.py <carpeta_salida> "<clave de sa>"   (sa sin MFA enrolado)
"""
import asyncio
import re
import sys
import time

import httpx
import pyotp
from playwright.async_api import async_playwright

BASE = "https://localhost:8443"
API = f"{BASE}/api/v1"
OUT, SA_PWD = sys.argv[1], sys.argv[2]
SUF = str(int(time.time()) % 100000)
TEC, AUD = f"tecnico.sede{SUF}", f"auditor.sede{SUF}"
TEMP_PWD, FINAL_PWD = "Kx7!Ruta#Q26a", "Kx7!Firme#Q26b"
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}{('  -> ' + str(detail)[:240]) if not ok else ''}", flush=True)


def wire(page, box):
    async def on_response(resp):
        try:
            if resp.url.endswith("/me/2fa/totp/setup") and resp.ok:
                box["secret"] = (await resp.json())["secret"]
            if any(p in resp.url for p in ("/login/refresh", "/login/access-token", "/login/2fa/verify")) and resp.ok:
                data = await resp.json()
                if data.get("access_token"):
                    box["token"] = data["access_token"]
        except Exception:
            pass
    page.on("response", on_response)


async def login_ui(page, user, pwd):
    await page.goto(f"{BASE}/login")
    await page.fill("input[name=username]", user)
    await page.fill("input[name=password]", pwd)
    await page.click("button[type=submit]")


async def api_login(c, user, pwd):
    r = await c.post(f"{API}/login/access-token", data={"username": user, "password": pwd})
    return r.json()


async def main():
    async with async_playwright() as p, httpx.AsyncClient(verify=False, timeout=30) as c:
        browser = await p.chromium.launch()

        # ── A. sa entra y enrola MFA (obligatorio para su rol) ──────────────────
        ctx = await browser.new_context(viewport={"width": 1440, "height": 900}, ignore_https_errors=True, locale="es-ES")
        page = await ctx.new_page()
        box = {}
        wire(page, box)
        await login_ui(page, "sa", SA_PWD)
        await page.get_by_text("App de autenticación").first.wait_for(timeout=20000)
        # (primer ingreso: sa aún no tiene MFA y el sistema exige enrolarlo)
        await page.get_by_text("App de autenticación").first.click()
        await page.wait_for_function("() => !!document.querySelector('img[src^=\"data:image/png\"]')", timeout=15000)
        for _ in range(50):  # el listener lee el secreto de la respuesta en paralelo
            if "secret" in box:
                break
            await page.wait_for_timeout(100)
        await page.get_by_label("Código de 6 dígitos").fill(pyotp.TOTP(box["secret"]).now())
        restricted = box.get("token")
        await page.get_by_role("button", name="Activar").click()
        await page.get_by_text("Códigos de recuperación").first.wait_for(timeout=15000)
        await page.get_by_role("checkbox").first.check()
        await page.get_by_role("button", name=re.compile("Entrar al sistema|Continuar")).click()
        await page.wait_for_url(re.compile(r".*/$"), timeout=20000)
        for _ in range(100):  # sesión completa (el refresh tras enrolar emite un token nuevo)
            if box.get("token") and box.get("token") != restricted:
                break
            await page.wait_for_timeout(100)
        H = {"Authorization": f"Bearer {box['token']}"}
        box["sa_secret"] = box["secret"]

        # ── B. Datos: sede B con un área, un técnico y un auditor de la sede A ──
        sedes = (await c.get(f"{API}/org/sedes", headers=H)).json()
        sede_a = sedes[0]
        mun = (await c.get(f"{API}/geo/sedes/{sede_a['SED_Sede']}", headers=H)).json()["MUN_Municipio"]
        sede_b = (await c.post(f"{API}/geo/sedes", headers=H, json={"SED_Nombre": f"Sede Norte {SUF}", "MUN_Municipio": mun})).json()
        edi = (await c.post(f"{API}/geo/edificios", headers=H, json={"EDI_Nombre": "Torre N", "SED_Sede": sede_b["SED_Sede"]})).json()
        niv = (await c.post(f"{API}/geo/niveles", headers=H, json={"NIV_Numero_Piso": "1", "EDI_Edificio": edi["EDI_Edificio"]})).json()
        area_b = (await c.post(f"{API}/geo/areas", headers=H, json={"ARE_Nombre": "Recepción Norte", "NIV_Nivel": niv["NIV_Nivel"]})).json()
        check("sa crea la sede B con su jerarquía", "ARE_Area" in area_b, area_b)

        libres = (await c.get(f"{API}/org/personas/disponibles", headers=H)).json()
        creados = []
        for username, rol, per in ((TEC, "TECNICO", libres[0]), (AUD, "AUDITOR", libres[1])):
            r = await c.post(f"{API}/org/usuarios", headers=H, json={
                "USU_Username": username, "USU_Password": TEMP_PWD, "USU_Rol": rol,
                "PER_Persona": per["PER_Persona"], "sedes": [sede_a["SED_Sede"]]})
            creados.append(r.status_code)
        check("sa crea un TÉCNICO y un AUDITOR con alcance solo en la sede A", creados == [201, 201], creados)
        r = await c.post(f"{API}/org/usuarios", headers=H, json={
            "USU_Username": f"sinalcance{SUF}", "USU_Password": TEMP_PWD, "USU_Rol": "TECNICO",
            "PER_Persona": libres[2]["PER_Persona"]})
        check("Crear una cuenta de inventario sin sedes ni global se rechaza", r.status_code == 400 and r.json()["detail"] == "SCOPE_REQUIRED", r.text)

        # ── C. Autocompletado del alcance al crear un usuario ──────────────────
        await page.goto(f"{BASE}/usuarios")
        await page.get_by_role("button", name=re.compile("Nuevo usuario", re.I)).click()
        dialog = page.get_by_role("dialog")
        await dialog.get_by_label(re.compile("Empleado", re.I)).first.fill(libres[2]["PER_Primer_Nombre"])
        await page.get_by_role("option").first.click()
        await dialog.get_by_text("Alcance de datos").wait_for(timeout=15000)
        await page.wait_for_timeout(1500)
        await page.screenshot(path=f"{OUT}/C1_usuario_alcance_sugerido.png")
        texto = await dialog.inner_text()
        check("El diálogo propone la sede de la persona como alcance (mínimo privilegio)",
              "sede de la persona" in texto and sede_a["SED_Nombre"] in texto, texto[-400:])
        await dialog.get_by_role("button", name="Cancelar").click()

        # ── D. Técnico de la sede A ─────────────────────────────────────────────
        tec_tok = (await api_login(c, TEC, TEMP_PWD))["access_token"]
        r = await c.post(f"{API}/me/password", headers={"Authorization": f"Bearer {tec_tok}"},
                         json={"current_password": TEMP_PWD, "new_password": FINAL_PWD})
        check("El técnico reemplaza su contraseña temporal", r.status_code == 204, r.text)
        HT = {"Authorization": f"Bearer {(await api_login(c, TEC, FINAL_PWD))['access_token']}"}
        total_a = (await c.post(f"{API}/core/activos/search", headers=HT, json={"page": 1, "per_page": 1})).json()["total"]
        total_sa = (await c.post(f"{API}/core/activos/search", headers=H, json={"page": 1, "per_page": 1})).json()["total"]
        check("El técnico ve los activos de su sede", total_a == total_sa and total_a > 0, (total_a, total_sa))
        r = await c.post(f"{API}/geo/sedes", headers=HT, json={"SED_Nombre": "X", "MUN_Municipio": mun})
        check("…no puede tocar la jerarquía compartida (403)", r.status_code == 403, r.text)

        # sa asigna un activo a la sede B: el técnico deja de verlo.
        activo = (await c.post(f"{API}/core/activos/search", headers=H, json={"page": 1, "per_page": 50})).json()["items"]
        libre = next((a for a in activo if a["EOP_Estado_Operativo"] != 0), activo[0])
        tipos = (await c.get(f"{API}/trazabilidad/tipos", headers=H)).json()
        transfer = next(t for t in tipos if "ransfer" in t["TMO_Nombre"])
        persona_b = libres[3]
        mov = await c.post(f"{API}/trazabilidad/transferencia", headers=H, json={
            "ACT_Activo": libre["ACT_Activo"], "PER_Persona_Destino": persona_b["PER_Persona"],
            "ARE_Area_Destino": area_b["ARE_Area"], "MOV_Observacion": "Prueba de alcance"})
        if mov.status_code >= 400:  # activo sin custodia: asignación directa
            asign = next(t for t in tipos if "signa" in t["TMO_Nombre"])
            mov = await c.post(f"{API}/trazabilidad/movimientos", headers=H, json={
                "ACT_Activo": libre["ACT_Activo"], "PER_Persona": persona_b["PER_Persona"],
                "ARE_Area": area_b["ARE_Area"], "TMO_Tipo_Movimiento": asign["TMO_Tipo_Movimiento"]})
        nuevo_total = (await c.post(f"{API}/core/activos/search", headers=HT, json={"page": 1, "per_page": 1})).json()["total"]
        check("Al mover un activo a la sede B, el técnico de la A deja de verlo", mov.status_code < 400 and nuevo_total == total_a - 1,
              (mov.status_code, mov.text[:200], nuevo_total))
        r = await c.get(f"{API}/core/activos/{libre['ACT_Activo']}", headers=HT)
        check("…y su detalle responde 404 (no revela que existe)", r.status_code == 404, r.status_code)

        # UI del técnico: sede prellenada al crear un activo y sesiones activas.
        tctx = await browser.new_context(viewport={"width": 1440, "height": 900}, ignore_https_errors=True, locale="es-ES")
        tpage = await tctx.new_page()
        await login_ui(tpage, TEC, FINAL_PWD)
        await tpage.wait_for_url(re.compile(r".*/$"), timeout=20000)
        await tpage.goto(f"{BASE}/activos")
        await tpage.get_by_role("button", name=re.compile("Nuevo activo", re.I)).click()
        tdialog = tpage.get_by_role("dialog")
        await tdialog.get_by_text("su sede asignada").wait_for(timeout=15000)
        await tpage.screenshot(path=f"{OUT}/D1_tecnico_sede_prellenada.png")
        check("Nuevo activo: la sede se prellena con su única sede", sede_a["SED_Nombre"] in await tdialog.inner_text())
        await tdialog.get_by_role("button", name="Cancelar").click()
        await tpage.get_by_role("button", name="Menú de la cuenta").click()
        await tpage.get_by_role("menuitem", name=re.compile("Seguridad de la cuenta", re.I)).click()
        await tpage.get_by_text("Esta sesión").wait_for(timeout=15000)
        await tpage.screenshot(path=f"{OUT}/D2_sesiones_activas.png")
        check("Seguridad de la cuenta lista las sesiones activas", True)

        # ── E. Auditor: solo lectura + bitácora ─────────────────────────────────
        aud_tok = (await api_login(c, AUD, TEMP_PWD))["access_token"]
        await c.post(f"{API}/me/password", headers={"Authorization": f"Bearer {aud_tok}"},
                     json={"current_password": TEMP_PWD, "new_password": FINAL_PWD})
        actx = await browser.new_context(viewport={"width": 1440, "height": 900}, ignore_https_errors=True, locale="es-ES")
        apage = await actx.new_page()
        await login_ui(apage, AUD, FINAL_PWD)
        await apage.wait_for_url(re.compile(r".*/$"), timeout=20000)
        await apage.goto(f"{BASE}/activos")
        await apage.get_by_text("LAP", exact=False).first.wait_for(timeout=15000)
        await apage.screenshot(path=f"{OUT}/E1_auditor_activos.png")
        check("Auditor: ve el inventario sin botón de alta", await apage.get_by_role("button", name=re.compile("Nuevo activo", re.I)).count() == 0)
        await apage.goto(f"{BASE}/auditoria")
        await apage.wait_for_timeout(2500)
        await apage.screenshot(path=f"{OUT}/E2_auditor_bitacora.png")
        HA = {"Authorization": f"Bearer {(await api_login(c, AUD, FINAL_PWD))['access_token']}"}
        aud = (await c.get(f"{API}/gov/auditoria", headers=HA, params={"limit": 200})).json()
        sedes_vistas = {i.get("AUD_Sede") for i in aud["items"]} - {None}
        check("Auditor: la bitácora solo trae eventos de su sede (o suyos)", sedes_vistas <= {sede_a["SED_Sede"]}, sedes_vistas)
        r = await c.post(f"{API}/core/activos", headers=HA, json={"ACT_Serie_Fabricante": "X", "ACT_Fecha_Compra": "2025-01-01",
                                                                  "MOD_Modelo": 1, "TAC_Tipo_Activo": 1, "EOP_Estado_Operativo": 1})
        check("Auditor: cualquier escritura se rechaza (403)", r.status_code == 403, r.status_code)

        await browser.close()
    print(f"\n{sum(results)}/{len(results)} verificaciones correctas")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

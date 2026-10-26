"""Recorrido real en navegador de los flujos de identidades, contraseñas y MFA."""
import asyncio
import re
import sys
import time

import httpx
import pyotp
from playwright.async_api import async_playwright, expect

BASE = "https://localhost:8443"
API = f"{BASE}/api/v1"
OUT, SA_PWD = sys.argv[1], sys.argv[2]
results = []
SUF = str(int(time.time()) % 100000)
SEG_USER, TEC_USER = f"seguridad.ana{SUF}", f"tecnico.luis{SUF}"


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}{('  -> ' + str(detail)[:200]) if not ok else ''}", flush=True)


async def enroll_mfa(page, tag, secrets_box):
    """Completa el gate de MFA con la app autenticadora (como un usuario real)."""
    await page.get_by_text("App de autenticación").first.click()
    await page.wait_for_function("() => !!document.querySelector('img[src^=\"data:image/png\"]')", timeout=15000)
    await page.screenshot(path=f"{OUT}/{tag}_mfa_qr.png")
    secret = secrets_box["secret"]
    await page.get_by_label("Código de 6 dígitos").fill(pyotp.TOTP(secret).now())
    await page.get_by_role("button", name="Activar").click()
    await page.get_by_text("Códigos de recuperación").first.wait_for(timeout=15000)
    await page.screenshot(path=f"{OUT}/{tag}_mfa_recovery.png")
    await page.get_by_label("Guardé estos códigos en un lugar seguro").check()
    await page.get_by_role("button", name=re.compile("Entrar al sistema|Continuar")).click()
    return secret


def wire(page, box):
    async def on_response(resp):
        try:
            if resp.url.endswith("/me/2fa/totp/setup") and resp.ok:
                box["secret"] = (await resp.json())["secret"]
            if ("/login/refresh" in resp.url or "/login/access-token" in resp.url or "/login/2fa/verify" in resp.url) and resp.ok:
                data = await resp.json()
                if data.get("access_token"):
                    box["token"] = data["access_token"]
                    box["restriction"] = data.get("restriction")
        except Exception:
            pass
    page.on("response", on_response)


async def login_ui(page, user, pwd):
    await page.goto(f"{BASE}/login")
    await page.fill("input[name=username]", user)
    await page.fill("input[name=password]", pwd)
    await page.click("button[type=submit]")


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()

        # ── A. Primer login de sa: el MFA es obligatorio para su rol ─────────
        ctx = await browser.new_context(viewport={"width": 1440, "height": 900}, ignore_https_errors=True)
        page = await ctx.new_page()
        box = {}
        wire(page, box)
        await login_ui(page, "sa", SA_PWD)
        await page.get_by_text("App de autenticación").first.wait_for(timeout=20000)
        await page.screenshot(path=f"{OUT}/A1_sa_gate_mfa.png")
        check("sa sin MFA: el sistema exige enrolar antes de entrar", box.get("restriction") == "MFA_ENROLLMENT_REQUIRED", box.get("restriction"))
        async with httpx.AsyncClient(verify=False) as c:
            r = await c.get(f"{API}/core/activos", headers={"Authorization": f"Bearer {box['token']}"})
        check("…y la API rechaza la sesión restringida (403)", r.status_code == 403 and r.json()["detail"] == "MFA_ENROLLMENT_REQUIRED", r.text)
        sa_secret = await enroll_mfa(page, "A2_sa", box)
        await page.wait_for_url(re.compile(r".*/$"), timeout=20000)
        await page.wait_for_timeout(1500)
        await page.screenshot(path=f"{OUT}/A3_sa_dentro.png")
        check("sa tras enrolar: sesión completa sin restricción", box.get("restriction") is None, box.get("restriction"))
        sa_token = box["token"]

        # ── Datos: un administrador de seguridad y un técnico (vía API, como lo haría la UI)
        H = {"Authorization": f"Bearer {sa_token}"}
        async with httpx.AsyncClient(verify=False) as c:
            personas = (await c.get(f"{API}/org/personas", headers=H)).json()
            usuarios = (await c.get(f"{API}/org/usuarios", headers=H)).json()
            con_usuario = {u["persona"]["PER_Persona"] for u in usuarios if u.get("persona")}
            libres = [pp for pp in personas if pp["PER_Persona"] not in con_usuario]
            seg = await c.post(f"{API}/org/usuarios", headers=H, json={
                "USU_Username": SEG_USER, "USU_Password": "Temporal#2026a", "USU_Rol": "ADMIN_SEGURIDAD",
                "PER_Persona": libres[0]["PER_Persona"]})
            tec = await c.post(f"{API}/org/usuarios", headers=H, json={
                "USU_Username": TEC_USER, "USU_Password": "Temporal#2026b", "USU_Rol": "TECNICO",
                "PER_Persona": libres[1]["PER_Persona"]})
            check("sa crea un ADMIN_SEGURIDAD y un TÉCNICO", seg.status_code == 201 and tec.status_code == 201, seg.text + tec.text)

        # ── B. Panel "Usuarios y accesos" ─────────────────────────────────────
        await page.goto(f"{BASE}/usuarios")
        await page.get_by_text("Usuarios y accesos").first.wait_for(timeout=15000)
        await page.wait_for_timeout(1500)
        await page.screenshot(path=f"{OUT}/B1_panel_usuarios.png", full_page=True)
        body = await page.inner_text("body")
        check("Panel muestra el cambio de contraseña pendiente de las cuentas nuevas", "Cambio de contraseña pendiente" in body)
        # Restablecer contraseña del técnico desde el menú ⋮ de su fila
        await page.locator("tr", has_text=TEC_USER).get_by_role("button").last.click()
        await page.get_by_role("menuitem", name="Restablecer contraseña").click()
        await page.get_by_role("dialog").get_by_role("button").last.click()
        await page.get_by_text("Contraseña temporal de").first.wait_for(timeout=15000)
        await page.screenshot(path=f"{OUT}/B2_contrasena_temporal.png")
        temp = await page.get_by_role("dialog").locator("input").first.input_value()
        check("Diálogo muestra la contraseña temporal una sola vez", len(temp) >= 12, temp)
        await page.get_by_role("button", name="Listo").click()
        # Propia fila sin acciones administrativas
        own = page.locator("tr", has_text="Tú").get_by_role("button").last
        check("La propia cuenta no ofrece acciones administrativas", await own.is_disabled())
        # Seguridad de la cuenta
        await page.get_by_role("button", name="Menú de la cuenta").click()
        await page.get_by_role("menuitem", name=re.compile("Seguridad de la cuenta", re.I)).click()
        await page.wait_for_timeout(1000)
        await page.screenshot(path=f"{OUT}/B3_seguridad_cuenta.png")
        await page.keyboard.press("Escape")
        await ctx.close()

        # ── C. Primer login del admin de seguridad: contraseña temporal + MFA ─
        ctx = await browser.new_context(viewport={"width": 1440, "height": 900}, ignore_https_errors=True)
        page = await ctx.new_page()
        box2 = {}
        wire(page, box2)
        await login_ui(page, SEG_USER, "Temporal#2026a")
        await page.get_by_text("Cambia tu contraseña temporal").first.wait_for(timeout=20000)
        await page.screenshot(path=f"{OUT}/C1_gate_password.png")
        check("Contraseña temporal: sesión restringida a cambiarla", box2.get("restriction") == "PASSWORD_CHANGE_REQUIRED", box2.get("restriction"))
        await page.get_by_label("Contraseña temporal").fill("Temporal#2026a")
        await page.get_by_label("Nueva contraseña", exact=True).fill("AnaSegura#2026x")
        await page.get_by_label("Confirmar nueva contraseña").fill("AnaSegura#2026x")
        await page.get_by_role("button", name="Cambiar y continuar").click()
        await page.get_by_text("App de autenticación").first.wait_for(timeout=25000)
        await page.screenshot(path=f"{OUT}/C2_gate_mfa.png")
        check("Tras cambiar la contraseña, su rol exige MFA", box2.get("restriction") == "MFA_ENROLLMENT_REQUIRED", box2.get("restriction"))
        await enroll_mfa(page, "C3_seg", box2)
        await page.wait_for_url(re.compile(r".*/usuarios$"), timeout=20000)
        await page.wait_for_timeout(1500)
        await page.screenshot(path=f"{OUT}/C4_seg_dentro.png")
        nav = await page.inner_text("nav")
        check("ADMIN_SEGURIDAD entra a Usuarios y accesos; su menú no tiene inventario",
              "Usuarios y accesos" in nav and "Activos" not in nav and "Movimientos" not in nav, nav[:200])
        async with httpx.AsyncClient(verify=False) as c:
            r = await c.get(f"{API}/core/activos", headers={"Authorization": f"Bearer {box2['token']}"})
        check("…y la API le niega el inventario (403)", r.status_code == 403, r.status_code)
        await ctx.close()

        # ── D. El técnico entra con la temporal que le dio el administrador ──
        ctx = await browser.new_context(viewport={"width": 390, "height": 844}, ignore_https_errors=True)
        page = await ctx.new_page()
        box3 = {}
        wire(page, box3)
        await login_ui(page, TEC_USER, temp)
        await page.get_by_text("Cambia tu contraseña temporal").first.wait_for(timeout=20000)
        await page.screenshot(path=f"{OUT}/D1_movil_gate_password.png")
        check("Técnico con temporal: debe cambiarla (móvil)", box3.get("restriction") == "PASSWORD_CHANGE_REQUIRED")
        await ctx.close()

        # ── E. Panel en móvil (sa, con su MFA) ────────────────────────────────
        ctx = await browser.new_context(viewport={"width": 390, "height": 844}, ignore_https_errors=True)
        page = await ctx.new_page()
        await login_ui(page, "sa", SA_PWD)
        await page.get_by_label("Código de verificación").wait_for(timeout=15000)
        await page.wait_for_timeout(31000 - int(time.time() % 30) * 1000 if time.time() % 30 > 25 else 0)
        code = pyotp.TOTP(sa_secret).at(time.time() + 30)  # anti-replay: código de la ventana siguiente
        await page.get_by_label("Código de verificación").fill(code)
        await page.get_by_role("button", name="Verificar").click()
        await page.wait_for_timeout(2000)
        await page.goto(f"{BASE}/usuarios")
        await page.wait_for_timeout(2000)
        await page.screenshot(path=f"{OUT}/E1_movil_panel.png", full_page=True)
        overflow = await page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
        check("Panel en móvil sin desbordes", overflow <= 1, overflow)
        await ctx.close()
        await browser.close()

    print(f"\n{sum(results)}/{len(results)} verificaciones OK")


asyncio.run(main())

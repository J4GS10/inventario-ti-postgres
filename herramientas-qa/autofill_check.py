"""Verifica en navegador el autocompletado relacional (asignación, activo, usuario)."""
import asyncio
import re
import sys

import pyotp
from playwright.async_api import async_playwright

BASE = "https://localhost:8443"
OUT, SA_PWD = sys.argv[1], sys.argv[2]
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}{('  -> ' + str(detail)[:200]) if not ok else ''}", flush=True)


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        ctx = await b.new_context(viewport={"width": 1440, "height": 900}, ignore_https_errors=True)
        page = await ctx.new_page()
        box = {}

        async def on_response(resp):
            try:
                if resp.url.endswith("/me/2fa/totp/setup") and resp.ok:
                    box["secret"] = (await resp.json())["secret"]
            except Exception:
                pass
        page.on("response", on_response)

        # Ingreso de sa (enrola MFA si el gate lo pide)
        await page.goto(f"{BASE}/login")
        await page.fill("input[name=username]", "sa")
        await page.fill("input[name=password]", SA_PWD)
        await page.click("button[type=submit]")
        await page.get_by_text("App de autenticación").first.wait_for(timeout=20000)
        await page.get_by_text("App de autenticación").first.click()
        await page.wait_for_function("() => !!document.querySelector('img[src^=\"data:image/png\"]')", timeout=15000)
        for _ in range(50):
            if "secret" in box:
                break
            await page.wait_for_timeout(100)
        await page.get_by_label("Código de 6 dígitos").fill(pyotp.TOTP(box["secret"]).now())
        await page.get_by_role("button", name="Activar").click()
        await page.get_by_label("Guardé estos códigos en un lugar seguro").check()
        await page.get_by_role("button", name=re.compile("Entrar al sistema|Continuar")).click()
        await page.wait_for_url(re.compile(r".*/$"), timeout=20000)

        # ── Nueva asignación: persona → área sugerida; activo → tipo de movimiento
        await page.goto(f"{BASE}/operaciones/movimientos")
        await page.get_by_role("button", name=re.compile("Nueva asignación", re.I)).click()
        dialog = page.get_by_role("dialog")
        await dialog.wait_for()
        persona = dialog.get_by_role("combobox", name=re.compile("persona|responsable|empleado", re.I)).first
        await persona.click()
        await persona.fill("Juan")
        await page.get_by_role("option").first.click()
        await page.wait_for_timeout(1500)
        await page.screenshot(path=f"{OUT}/asignacion_persona.png")
        texto = await dialog.inner_text()
        check("Asignación: al elegir la persona se sugiere el área", "Sugerido" in texto, texto[:300])
        check("Asignación: muestra departamento/cargo de la persona", re.search(r"Usuarios inventariados|TI staging|Departamento|·", texto) is not None)
        await page.keyboard.press("Escape")

        # ── Nuevo activo: modelo → marca y tipo
        await page.goto(f"{BASE}/activos")
        await page.get_by_role("button", name=re.compile("Nuevo activo", re.I)).click()
        dialog = page.get_by_role("dialog")
        await dialog.wait_for()
        modelo = dialog.get_by_role("combobox", name=re.compile("Modelo", re.I)).first
        await modelo.click()
        await modelo.fill("ProBook 450 G8")
        await page.get_by_role("option").first.click()
        await page.wait_for_timeout(1500)
        await page.screenshot(path=f"{OUT}/activo_modelo.png")
        texto = await dialog.inner_text()
        check("Activo: el modelo completa la marca (HP)", "HP" in texto, texto[:300])
        check("Activo: el modelo sugiere el tipo (Laptop) y el prefijo de código", "Laptop" in texto and "Sugerido" in texto, texto[:400])
        await b.close()
    print(f"\n{sum(results)}/{len(results)} verificaciones OK")


asyncio.run(main())

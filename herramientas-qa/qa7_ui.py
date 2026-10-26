"""QA7 — Mensajes de la UI en Administración → Directorio y notificaciones (navegador, sa con TOTP).
Uso (cwd=herramientas-qa): python -u qa7_ui.py
Un solo intento de 2FA (nunca se reintenta).
"""
import asyncio
import json

from playwright.async_api import async_playwright

from qa_login import codigo_totp_ui, credenciales

BASE = "https://localhost:8443"
OUT = "salidas/qa7"
R = {}


async def texto_alertas(page):
    return [t.strip() for t in await page.locator(".MuiAlert-root, .notistack-Snackbar, #notistack-snackbar").all_inner_texts()]


async def main():
    cred = credenciales()
    async with async_playwright() as p:
        br = await p.chromium.launch()
        ctx = await br.new_context(viewport={"width": 1440, "height": 1000}, ignore_https_errors=True,
                                   locale="es-GT", timezone_id="America/Guatemala")
        page = await ctx.new_page()
        await page.goto(f"{BASE}/login")
        await page.fill("input[name=username]", cred["usuario"])
        await page.fill("input[name=password]", cred["clave"])
        await page.click("button[type=submit]")
        await page.get_by_label("Código de verificación").wait_for(timeout=20000)
        await page.get_by_label("Código de verificación").fill(codigo_totp_ui(cred["usuario"], cred["totp_secret"]))
        await page.get_by_role("button", name="Verificar").click()
        await page.wait_for_timeout(3000)
        await page.goto(f"{BASE}/config/directorio")
        await page.get_by_text("Active Directory").first.wait_for(timeout=20000)
        await page.wait_for_timeout(2500)
        await page.screenshot(path=f"{OUT}/ui_01_directorio.png", full_page=True)

        # Probar conexión (AD configurado pero el cliente real no devuelve usuarios)
        await page.get_by_role("button", name="Probar conexión", exact=True).click()
        await page.wait_for_timeout(4000)
        R["probar_conexion"] = await texto_alertas(page)
        await page.screenshot(path=f"{OUT}/ui_02_probar_conexion.png", full_page=True)

        # Simular sincronización
        await page.get_by_role("button", name="Simular sincronización").click()
        await page.get_by_text("Resultado de la simulación").first.wait_for(timeout=60000)
        await page.wait_for_timeout(1500)
        R["simulacion"] = (await page.get_by_text("Simulación: no se guardó").first.inner_text())
        R["hora_mostrada_ultima_sync"] = await page.locator("text=Última sincronización").first.locator("..").inner_text()
        await page.screenshot(path=f"{OUT}/ui_03_simulacion.png", full_page=True)

        # Prueba de AD con clave incorrecta desde el formulario de la cuenta de servicio
        R["contenido_pagina"] = (await page.inner_text("body"))[:6000]
        await ctx.close()
        await br.close()
    json.dump(R, open(f"{OUT}/ui.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps({k: v for k, v in R.items() if k != "contenido_pagina"}, ensure_ascii=False, indent=1))


asyncio.run(main())

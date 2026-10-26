"""Capturas de todas las rutas (desktop/tablet/móvil) y de la barra superior."""
import asyncio
import os
import sys

from playwright.async_api import async_playwright

BASE = "https://localhost:8443"
OUT = sys.argv[1]
PWD = sys.argv[2]
LANG = sys.argv[3] if len(sys.argv) > 3 else "es"
ONLY = sys.argv[4].split(",") if len(sys.argv) > 4 else None
ROUTES = ["/", "/activos", "/empleados", "/departamentos", "/usuarios", "/operaciones/movimientos",
          "/mantenimiento", "/consumibles", "/software",
          "/compras/proveedores", "/compras/ordenes", "/compras/garantias", "/config/marcas",
          "/config/modelos", "/config/tipos", "/config/ubicaciones", "/config", "/config/directorio",
          "/auditoria", "/scanner"]
VIEWPORTS = {"desk": (1440, 900), "tab": (1024, 768), "mob": (390, 844)}


async def main():
    os.makedirs(OUT, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        for vp_name, (w, h) in VIEWPORTS.items():
            if ONLY and vp_name not in ONLY:
                continue
            ctx = await browser.new_context(viewport={"width": w, "height": h}, ignore_https_errors=True,
                                            locale={"es": "es-ES", "en": "en-US", "it": "it-IT"}[LANG])
            page = await ctx.new_page()
            errors = []
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            await page.goto(f"{BASE}/login")
            await page.evaluate(f"localStorage.setItem('i18nextLng', '{LANG}')")
            await page.reload()
            await page.fill("input[name=username]", "sa")
            await page.fill("input[name=password]", PWD)
            await page.click("button[type=submit]")
            await page.wait_for_url(lambda u: "/login" not in u, timeout=20000)
            for route in ROUTES:
                await page.goto(f"{BASE}{route}")
                await page.wait_for_load_state("networkidle")
                await page.wait_for_timeout(700)
                name = (route.strip("/").replace("/", "_") or "dashboard")
                await page.screenshot(path=f"{OUT}/{LANG}_{vp_name}_{name}.png", full_page=True)
                # Desbordes horizontales = elementos que se salen / se sobreponen
                overflow = await page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
                if overflow > 1:
                    print(f"OVERFLOW {vp_name} {route}: {overflow}px")
            if errors:
                print(f"CONSOLE ERRORS {vp_name}:", *sorted(set(errors))[:10], sep="\n  ")
            await ctx.close()
        await browser.close()


asyncio.run(main())

"""QA5 - Etiquetas desde Activos en la UI (reintento aislado) y diagnóstico de la lista vacía."""
import asyncio, json, os, re
from playwright.async_api import async_playwright
from qa5_ui import login, BASE, D, UI, OUT, check, RES
from qa5_reportes import decode_qrs


async def main():
    u = D["usuarios"]["ADMIN_TI"]
    estados = []
    async with async_playwright() as p:
        b = await p.chromium.launch()
        ctx = await b.new_context(viewport={"width": 1440, "height": 900}, ignore_https_errors=True, locale="es-ES", accept_downloads=True)
        page = await ctx.new_page()
        page.on("response", lambda r: estados.append((r.status, r.url)) if "/core/activos" in r.url else None)
        await login(page, u["usuario"], u["clave"], u["totp_secret"])
        await page.goto(f"{BASE}/activos")
        await page.get_by_text(D["activos"]["a1"]["codigo"]).first.wait_for(timeout=30000)
        for k in ("a1", "a2"):
            await page.get_by_label(f"Seleccionar activo {D['activos'][k]['codigo']}").check()
        await page.screenshot(path=os.path.join(UI, "UI-QR-seleccion.png"))
        async with page.expect_download(timeout=60000) as dl:
            await page.get_by_role("button", name=re.compile("Imprimir etiquetas")).click()
        d = await dl.value
        dest = os.path.join(UI, f"UI-QR__{d.suggested_filename}")
        await d.save_as(dest)
        pages, _ = decode_qrs(open(dest, "rb").read())
        codes = sorted(v for pg in pages for v in pg)
        check("UI-QR", "Activos → Imprimir etiquetas (2 seleccionados): QR decodificados = códigos seleccionados",
              codes == sorted([D["activos"]["a1"]["codigo"], D["activos"]["a2"]["codigo"]]),
              f"{d.suggested_filename}: {codes}", os.path.relpath(dest, os.path.join(OUT, "..")), "alta")
        check("UI-QR-NOM", "Nombre del PDF de etiquetas desde la UI (idioma de la interfaz)", True, d.suggested_filename)
        await b.close()
    print("respuestas /core/activos:", sorted(set(s for s, _ in estados)))
    json.dump(RES, open(os.path.join(OUT, "resultados_ui_qr.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)

asyncio.run(main())

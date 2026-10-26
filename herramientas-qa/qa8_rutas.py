"""QA8 (UX): recorre las rutas en 3 idiomas x 3 tamaños; capturas + desbordes + textos técnicos + consola.

Uso (cwd=herramientas-qa): python qa8_rutas.py [es,en,it] [desk,tab,mob]
Salida: salidas/qa8/rutas/<lang>_<vp>_<ruta>.png y salidas/qa8/rutas.json
"""
import asyncio
import json
import os
import sys

from playwright.async_api import async_playwright

from qa8_comun import BASE, OUT, VIEWPORTS, analizar_texto, guardar, login, nuevo_contexto, overflow, overflow_elems, set_lang

ROUTES = ["/", "/activos", "/scanner", "/operaciones/movimientos", "/mantenimiento", "/software", "/consumibles",
          "/empleados", "/departamentos", "/compras/proveedores", "/compras/ordenes", "/compras/garantias",
          "/config/marcas", "/config/modelos", "/config/tipos", "/config/ubicaciones", "/config/directorio",
          "/usuarios", "/auditoria", "/config"]
LANGS = sys.argv[1].split(",") if len(sys.argv) > 1 else ["es", "en", "it"]
VPS = sys.argv[2].split(",") if len(sys.argv) > 2 else list(VIEWPORTS)
DIR = os.path.join(OUT, "rutas")


async def main():
    os.makedirs(DIR, exist_ok=True)
    res = []
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        ctx = await nuevo_contexto(browser, "desk", LANGS[0])
        page = await ctx.new_page()
        errs = []
        page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errs.append("PAGEERROR " + str(e)))
        await login(page, LANGS[0])
        for lang in LANGS:
            await set_lang(page, lang)
            for vp in VPS:
                w, h = VIEWPORTS[vp]
                await page.set_viewport_size({"width": w, "height": h})
                for route in ROUTES:
                    errs.clear()
                    await page.goto(f"{BASE}{route}")
                    try:
                        await page.wait_for_load_state("networkidle", timeout=15000)
                    except Exception:
                        pass
                    await page.wait_for_timeout(900)
                    name = route.strip("/").replace("/", "_") or "dashboard"
                    shot = f"rutas/{lang}_{vp}_{name}.png"
                    await page.screenshot(path=os.path.join(OUT, shot), full_page=(vp != "desk"))
                    texto = await page.inner_text("body")
                    ov = await overflow(page)
                    h1 = await page.locator("h1").all_inner_texts()
                    # títulos truncados (noWrap) en el encabezado
                    trunc = await page.evaluate("""() => [...document.querySelectorAll('h1')].filter(e => e.scrollWidth > e.clientWidth + 1).map(e => e.innerText)""")
                    r = {"lang": lang, "vp": vp, "route": route, "shot": shot, "overflow": ov,
                         "overflow_elems": await overflow_elems(page) if ov > 1 else [],
                         "h1": h1, "h1_truncado": trunc, "url_final": page.url.replace(BASE, ""),
                         "consola": sorted(set(errs))[:8], **analizar_texto(texto)}
                    res.append(r)
                    flags = [k for k in ("uuids", "claves_i18n", "crudos", "codigos", "consola", "h1_truncado") if r[k]]
                    if len(res) % 10 == 0:
                        await guardar(ctx)
                    print(f"{lang} {vp} {route:28s} ov={ov:<4} {' '.join(f'{k}={r[k]}' for k in flags)}", flush=True)
        await guardar(ctx)
        await browser.close()
    with open(os.path.join(OUT, f"rutas_{'_'.join(LANGS)}_{'_'.join(VPS)}.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)


asyncio.run(main())

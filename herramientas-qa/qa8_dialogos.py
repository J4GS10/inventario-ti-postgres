"""QA8 (UX): abre los diálogos principales (sin guardar), prueba la validación en vacío y captura.

Uso (cwd=herramientas-qa): python qa8_dialogos.py [es,en,it] [desk,mob]
Solo se pulsa "Guardar" en formularios de ALTA vacíos (la validación impide enviar). Los diálogos
de edición, recepción y offboarding se abren y se cierran sin enviar.
"""
import asyncio
import json
import os
import re
import sys

from playwright.async_api import async_playwright

from qa8_comun import BASE, OUT, VIEWPORTS, analizar_texto, guardar, login, nuevo_contexto, set_lang

LANGS = sys.argv[1].split(",") if len(sys.argv) > 1 else ["es", "en", "it"]
VPS = sys.argv[2].split(",") if len(sys.argv) > 2 else ["desk", "mob"]
DIR = os.path.join(OUT, "dialogos")
LOC = {l: json.load(open(os.path.join(os.path.dirname(__file__), "..", "inventarioTI-frontend", "src", "locales", f"{l}.json"),
                        encoding="utf-8")) for l in ("es", "en", "it")}


def T(lang, key):
    d = LOC[lang]
    for p in key.split("."):
        d = d[p]
    return d


def btn(key, first=True, exact=False):
    """Abre con el botón cuyo nombre accesible es t(key)."""
    async def f(page, lang):
        loc = page.get_by_role("button", name=T(lang, key), exact=exact)
        await (loc.first if first else loc).click()
    return f


def chain(*steps):
    async def f(page, lang):
        for s in steps:
            await s(page, lang)
            await page.wait_for_timeout(1200)
    return f


# (nombre, ruta, abrir, enviar_vacio)
SPECS = [
    ("activo_nuevo", "/activos", btn("assets.new_btn"), True),
    ("activo_editar", "/activos", btn("common.edit"), False),
    ("activo_detalle", "/activos", btn("common.view"), False),
    ("asignacion", "/operaciones/movimientos", btn("operations.new_btn"), True),
    ("acta_descargo", "/operaciones/movimientos", btn("operations.download_descargo"), False),
    ("mantenimiento_nuevo", "/mantenimiento", btn("maintenance.new_ticket"), True),
    ("mantenimiento_detalle", "/mantenimiento", btn("common.view"), False),
    ("persona_nueva", "/empleados", btn("persons.new"), True),
    ("persona_editar", "/empleados", btn("common.edit"), False),
    ("persona_detalle", "/empleados", btn("persons.view_detail"), False),
    ("offboarding", "/empleados", btn("persons.offboarding.tooltip"), False),
    ("usuario_nuevo", "/usuarios", btn("users.new_btn"), True),
    ("proveedor_nuevo", "/compras/proveedores", btn("procurement.new_provider"), True),
    ("orden_nueva", "/compras/ordenes", btn("procurement.new_order"), True),
    ("orden_detalle", "/compras/ordenes", btn("procurement.view_receive"), False),
    ("orden_recepcion", "/compras/ordenes", chain(btn("procurement.view_receive"), btn("procurement.receive", exact=True)), False),
    ("software_nuevo", "/software", btn("software.btn_new"), True),
    ("licencia_nueva", "/software", btn("software.btn_add_license"), True),
    ("tipos_licencia", "/software", btn("app.license_types_title"), False),
    ("consumible_nuevo", "/consumibles", btn("consumables.new_btn"), True),
    ("stock_entrada", "/consumibles", btn("consumables.entry_title"), False),
    ("stock_salida", "/consumibles", btn("consumables.exit_title"), False),
    ("consumible_historial", "/consumibles", btn("consumables.history"), False),
    ("tipo_nuevo", "/config/tipos", btn("catalogs.types.new_btn"), True),
    ("marca_editar", "/config/marcas", btn("common.edit"), False),
]


async def dialog_info(page):
    return await page.evaluate("""() => {
      const ds = [...document.querySelectorAll('[role=dialog]')];
      const d = ds[ds.length - 1];
      if (!d) return null;
      const paper = d.querySelector('.MuiDialog-paper') || d;
      const title = (d.querySelector('h2, .MuiDialogTitle-root') || {}).innerText || '';
      const content = d.querySelector('.MuiDialogContent-root');
      const helpers = [...d.querySelectorAll('.MuiFormHelperText-root')].map(e => e.innerText).filter(Boolean);
      const errores = [...d.querySelectorAll('.Mui-error.MuiFormHelperText-root')].map(e => e.innerText);
      const camposError = [...d.querySelectorAll('.MuiInputBase-root.Mui-error')].length;
      const sugerencias = [...d.querySelectorAll('*')].filter(e => e.children.length === 0 && /Sugerido|Suggested|Suggerit/i.test(e.innerText || '')).map(e => e.innerText).slice(0, 6);
      const secciones = [...d.querySelectorAll('h3, h6, .MuiTypography-subtitle1, .MuiTypography-subtitle2, .MuiTypography-overline')].map(e => e.innerText).filter(Boolean).slice(0, 12);
      const campos = [...d.querySelectorAll('label.MuiInputLabel-root, label.MuiFormLabel-root')].map(e => e.innerText.replace(/\\s*\\*$/, '')).filter(Boolean);
      const alerts = [...d.querySelectorAll('.MuiAlert-root')].map(e => e.innerText);
      const botones = [...d.querySelectorAll('.MuiDialogActions-root button')].map(e => e.innerText || e.getAttribute('aria-label'));
      return { title, ancho: Math.round(paper.getBoundingClientRect().width), alto: Math.round(paper.getBoundingClientRect().height),
               desborde_x: content ? content.scrollWidth - content.clientWidth : 0, helpers, errores, camposError,
               sugerencias, secciones, campos, n_campos: campos.length, alerts, botones, texto: d.innerText };
    }""")


async def snacks(page):
    return await page.evaluate("""() => [...document.querySelectorAll('.notistack-SnackbarContainer [role=alert], .notistack-MuiContent, #notistack-snackbar')].map(e => e.innerText).filter(Boolean)""")


async def main():
    os.makedirs(DIR, exist_ok=True)
    res = []
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        ctx = await nuevo_contexto(browser, "desk", LANGS[0])
        page = await ctx.new_page()
        errs = []
        page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        await login(page, LANGS[0])
        for lang in LANGS:
            await set_lang(page, lang)
            for vp in VPS:
                w, h = VIEWPORTS[vp]
                await page.set_viewport_size({"width": w, "height": h})
                for name, route, abrir, enviar in SPECS:
                    errs.clear()
                    r = {"lang": lang, "vp": vp, "dialogo": name, "ruta": route}
                    try:
                        await page.goto(f"{BASE}{route}")
                        await page.wait_for_load_state("networkidle")
                        await page.wait_for_timeout(800)
                        await abrir(page, lang)
                        await page.get_by_role("dialog").last.wait_for(timeout=8000)
                        await page.wait_for_timeout(1500)
                        shot = f"dialogos/{lang}_{vp}_{name}.png"
                        await page.screenshot(path=os.path.join(OUT, shot))
                        info = await dialog_info(page)
                        r.update(shot=shot, **{k: v for k, v in info.items() if k != "texto"}, **analizar_texto(info["texto"]))
                        if enviar:
                            botones = page.get_by_role("dialog").last.locator(".MuiDialogActions-root button")
                            n = await botones.count()
                            if n:
                                await botones.nth(n - 1).click()
                                await page.wait_for_timeout(1200)
                                shot2 = f"dialogos/{lang}_{vp}_{name}_vacio.png"
                                await page.screenshot(path=os.path.join(OUT, shot2))
                                info2 = await dialog_info(page) or {}
                                r["vacio"] = {"shot": shot2, "errores": info2.get("errores"), "camposError": info2.get("camposError"),
                                              "snacks": await snacks(page), "sigue_abierto": bool(info2)}
                        await page.keyboard.press("Escape")
                        await page.wait_for_timeout(400)
                        await page.keyboard.press("Escape")
                    except Exception as e:
                        r["error"] = str(e).splitlines()[0][:200]
                    r["consola"] = sorted(set(errs))[:5]
                    res.append(r)
                    print(f"{lang} {vp} {name:22s} {r.get('title', '')!r:40s} campos={r.get('n_campos')} "
                          f"sug={len(r.get('sugerencias') or [])} vacio={r.get('vacio', {}).get('errores')}/{r.get('vacio', {}).get('camposError')} "
                          f"{r.get('vacio', {}).get('snacks') or ''} dx={r.get('desborde_x')} {r.get('error', '')}", flush=True)
                await guardar(ctx)
        await guardar(ctx)
        await browser.close()
    with open(os.path.join(OUT, f"dialogos_{'_'.join(LANGS)}_{'_'.join(VPS)}.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)


asyncio.run(main())

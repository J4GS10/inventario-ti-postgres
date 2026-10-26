"""
QA1 — Comprobaciones esenciales en navegador de las pantallas CRUD.

Un único inicio de sesión de sa en la UI (código con qa_login.codigo_totp_ui,
sin reintentos). Usa los datos creados por qa1_crud.py (salidas/qa1/api_resultados.json).

Uso (cwd = herramientas-qa):
    PYTHONIOENCODING=utf-8 python qa1_ui.py
"""
import asyncio
import json
import os
import re

from playwright.async_api import async_playwright

from qa_login import ORIGEN, credenciales, codigo_totp_ui

AQUI = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(AQUI, "salidas", "qa1", "ui")
os.makedirs(OUT, exist_ok=True)
DATA = json.load(open(os.path.join(AQUI, "salidas", "qa1", "api_resultados.json"), encoding="utf-8"))
P = DATA["prefijo"]
RES = []


def rec(cid, nombre, ok, obtenido, shot):
    RES.append({"id": cid, "caso": nombre, "ok": ok, "obtenido": obtenido, "evidencia": f"salidas/qa1/ui/{shot}.png"})
    print(("OK   " if ok else "FAIL ") + cid, nombre, "→", obtenido[:200])


async def snack(page, timeout=6000):
    loc = page.locator("#notistack-snackbar, .notistack-MuiContent, [role=alert]").last
    try:
        await loc.wait_for(timeout=timeout)
        return (await loc.inner_text()).strip()
    except Exception:
        return ""


async def main():
    cred = credenciales()
    errores_consola, errores_red = [], []
    async with async_playwright() as p:
        b = await p.chromium.launch()
        ctx = await b.new_context(ignore_https_errors=True, locale="es-ES", viewport={"width": 1440, "height": 900})
        page = await ctx.new_page()
        page.on("console", lambda m: m.type == "error" and errores_consola.append(f"{page.url} :: {m.text[:200]}"))
        async def _resp(r):
            if r.status >= 400 and "/api/" in r.url:
                try:
                    txt = (await r.text())[:200]
                except Exception:
                    txt = ""
                errores_red.append(f"{r.status} {r.request.method} {r.url} {txt}")
        page.on("response", lambda r: asyncio.ensure_future(_resp(r)))

        # ── Login (un único intento) ─────────────────────────────────────
        await page.goto(f"{ORIGEN}/login")
        await page.fill("input[name=username]", cred["usuario"])
        await page.fill("input[name=password]", cred["clave"])
        await page.click("button[type=submit]")
        campo = page.get_by_label("Código de verificación")
        await campo.wait_for(timeout=15000)
        await campo.fill(codigo_totp_ui(cred["usuario"], cred["totp_secret"]))  # justo antes de escribirlo
        await page.get_by_role("button", name="Verificar").click()
        await page.wait_for_url(re.compile(r"^(?!.*login).*$"), timeout=20000)
        await page.wait_for_timeout(1500)
        rec("UI-00", "Inicio de sesión en la UI con MFA", "login" not in page.url, page.url, "00_login")
        await page.screenshot(path=os.path.join(OUT, "00_login.png"))

        # ── Recorrido de pantallas ───────────────────────────────────────
        rutas = [("01_marcas", "/config/marcas"), ("02_tipos", "/config/tipos"), ("03_modelos", "/config/modelos"),
                 ("04_ubicaciones", "/config/ubicaciones"), ("05_departamentos", "/departamentos"),
                 ("06_empleados", "/empleados"), ("07_activos", "/activos"), ("08_software", "/software"),
                 ("09_consumibles", "/consumibles"), ("10_proveedores", "/compras/proveedores"),
                 ("11_ordenes", "/compras/ordenes"), ("12_garantias", "/compras/garantias")]
        PARTE = os.environ.get("QA1_UI_PARTE", "todo")
        if PARTE != "todo":
            rutas = [r for r in rutas if r[1] == "/activos"]
        for i, (shot, ruta) in enumerate(rutas, 1):
            n0 = len(errores_red)
            await page.goto(ORIGEN + ruta)
            await page.wait_for_load_state("networkidle")
            await page.wait_for_timeout(800)
            await page.screenshot(path=os.path.join(OUT, f"{shot}.png"), full_page=False)
            nuevos = errores_red[n0:]
            await page.wait_for_timeout(1500)
            nuevos = errores_red[n0:]
            rec(f"UI-{i:02d}" if PARTE == "todo" else "UI-07", f"Pantalla {ruta} carga sin errores", not nuevos, "; ".join(nuevos) or "sin errores", shot)

        # ── Marca duplicada desde la UI ──────────────────────────────────
        if PARTE != "todo":
            await activos(page)
            await b.close()
            json.dump({"casos": RES, "errores_red": errores_red}, open(os.path.join(OUT, "ui_resultados_activos.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            print("red:", errores_red[:20])
            return
        await page.goto(ORIGEN + "/config/marcas")
        await page.wait_for_load_state("networkidle")
        await page.get_by_role("button", name="Nueva marca").click()
        dlg = page.get_by_role("dialog")
        await dlg.get_by_label("Nombre").fill(f"{P} Marca Editada")
        await dlg.get_by_role("button", name="Guardar").click()
        t = await snack(page)
        await page.screenshot(path=os.path.join(OUT, "20_marca_duplicada.png"))
        rec("UI-20", "Marca duplicada: mensaje traducido", "Ya existe una marca" in t, t or "(sin aviso)", "20_marca_duplicada")
        await page.keyboard.press("Escape")

        # ── Marca duplicada que solo difiere en mayúsculas ───────────────
        await page.wait_for_timeout(800)
        await page.get_by_role("button", name="Nueva marca").click()
        dlg = page.get_by_role("dialog")
        await dlg.get_by_label("Nombre").fill(f"{P} MARCA EDITADA")
        await dlg.get_by_role("button", name="Guardar").click()
        t = await snack(page)
        await page.screenshot(path=os.path.join(OUT, "21_marca_mayusculas.png"))
        rec("UI-21", "Marca duplicada en MAYÚSCULAS se rechaza", "Ya existe" in t, t or "(sin aviso)", "21_marca_mayusculas")
        await page.keyboard.press("Escape")

        # ── Borrar país con hijos desde la UI ────────────────────────────
        await page.goto(ORIGEN + "/config/ubicaciones")
        await page.wait_for_load_state("networkidle")
        fila = page.locator("div[role=button], li", has_text=f"{P} País").first
        try:
            await fila.get_by_role("button", name="Eliminar").click()
            await page.get_by_role("dialog").get_by_role("button", name="Eliminar").click()
            t = await snack(page)
        except Exception as e:
            t = f"(no se pudo ejecutar: {str(e)[:120]})"
        await page.screenshot(path=os.path.join(OUT, "22_borrar_pais_con_hijos.png"))
        rec("UI-22", "Borrar país con estados: mensaje claro", "estado" in t.lower() or "depart" in t.lower(), t or "(sin aviso)", "22_borrar_pais_con_hijos")

        await activos(page)
        await b.close()
    json.dump({"casos": RES, "errores_consola": errores_consola, "errores_red": errores_red},
              open(os.path.join(OUT, "ui_resultados.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


async def activos(page):
    if True:
        # ── Editar activo: título, moneda y fechas ───────────────────────
        cod = DATA["ids"]["cod1"]
        await page.goto(ORIGEN + "/activos")
        await page.wait_for_load_state("networkidle")
        await page.get_by_placeholder(re.compile("Buscar", re.I)).first.fill(cod)
        await page.wait_for_timeout(2500)
        await page.screenshot(path=os.path.join(OUT, "23a_busqueda_activo.png"))
        row = page.locator("tr", has_text=cod).first
        if await row.count() == 0:
            rec("UI-23", "Buscar el activo por código en la pantalla Activos", False,
                f"el activo {cod} no aparece; aviso='{await snack(page, 2000)}'", "23a_busqueda_activo")
            return
        await row.get_by_role("button", name="Editar").click()
        dlg = page.get_by_role("dialog")
        await dlg.wait_for()
        await page.wait_for_timeout(1500)
        titulo = (await dlg.locator("h2").first.inner_text()).strip()
        await page.screenshot(path=os.path.join(OUT, "23_editar_activo.png"))
        texto = await dlg.inner_text()
        rec("UI-23", "Diálogo de edición de activo: título y moneda visibles", titulo == "Editar activo" and ("GTQ" in texto or "Q" in texto),
            f"título='{titulo}'; moneda visible={'GTQ' in texto}", "23_editar_activo")
        fecha = dlg.get_by_label("Fecha de compra")
        antes = await fecha.input_value()
        await fecha.fill("2026-06-01")
        await dlg.get_by_role("button", name="Guardar").click()
        t = await snack(page)
        await page.wait_for_timeout(1500)
        await row.get_by_role("button", name="Editar").click()
        dlg = page.get_by_role("dialog")
        await dlg.wait_for()
        await page.wait_for_timeout(1500)
        despues = await dlg.get_by_label("Fecha de compra").input_value()
        await page.screenshot(path=os.path.join(OUT, "24_fecha_compra_no_guardada.png"))
        rec("UI-24", "Editar fecha de compra desde el formulario", despues == "2026-06-01",
            f"aviso='{t}'; antes={antes}; tras guardar y reabrir={despues}", "24_fecha_compra_no_guardada")
        await page.keyboard.press("Escape")

        # ── Órdenes: moneda visible en el listado ────────────────────────
        await page.goto(ORIGEN + "/compras/ordenes")
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(1000)
        body = await page.inner_text("body")
        await page.screenshot(path=os.path.join(OUT, "25_ordenes_monedas.png"), full_page=True)
        visto = [m for m in ("USD", "CHF", "GTQ", "Q") if m in body]
        rec("UI-25", "Listado de órdenes muestra la moneda de cada orden", ("USD" in body or "US$" in body) and ("CHF" in body),
            f"monedas vistas: {visto}", "25_ordenes_monedas")

        # ── Pantallas CRUD ausentes ──────────────────────────────────────
        await page.goto(ORIGEN + "/config")
        await page.wait_for_load_state("networkidle")
        cfg = await page.inner_text("body")
        await page.screenshot(path=os.path.join(OUT, "26_config.png"), full_page=True)
        faltan = [x for x in ("Estados operativos", "Tipos de especificación", "Tipos de conexión", "Cargos") if x.lower() not in cfg.lower()]
        rec("UI-26", "Existen pantallas para estados, tipos de especificación, conexión y cargos", not faltan,
            f"no se encontraron en la UI: {faltan}", "26_config")


asyncio.run(main())

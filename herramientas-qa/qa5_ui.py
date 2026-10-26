"""QA5 - Descargas desde la interfaz (Playwright, chromium).

Ingresa como el ADMIN_TI de la sede QA5 (y el AUDITOR para la bitácora), usa el
botón "Exportar" de cada listado, las actas de Movimientos y las etiquetas de
Activos, y valida el archivo descargado (nombre sugerido, BOM, idioma y alcance).
Uso: PYTHONIOENCODING=utf-8 python qa5_ui.py
"""
import asyncio
import csv
import io
import json
import os
import re
import time

from playwright.async_api import async_playwright

from qa5_comun import OUT, datos
from qa_login import codigo_totp_ui
from qa5_reportes import decode_qrs, pdf_text, docx_text  # reutiliza utilidades

BASE = "https://localhost:8443"
D = datos()
RES = []
UI = os.path.join(OUT, "ui")
os.makedirs(UI, exist_ok=True)


def check(cid, caso, ok, obtenido="", evidencia="", severidad="media", observacion=False):
    estado = "OK" if ok else ("OBSERVACIÓN" if observacion else "DEFECTO")
    RES.append({"id": cid, "caso": caso, "obtenido": str(obtenido)[:600], "evidencia": evidencia,
                "severidad": "" if ok else severidad, "estado": estado})
    print(f"{estado:12} {cid:10} {caso}" + ("" if ok else f"  -> {str(obtenido)[:300]}"), flush=True)


async def login(page, user, pwd, secret):
    await page.goto(f"{BASE}/login")
    await page.fill("input[name=username]", user)
    await page.fill("input[name=password]", pwd)
    await page.click("button[type=submit]")
    if secret:
        await page.get_by_label("Código de verificación").wait_for(timeout=20000)
        codigo = await asyncio.to_thread(codigo_totp_ui, user, secret)
        await page.get_by_label("Código de verificación").fill(codigo)
        await page.get_by_role("button", name="Verificar").click()
    await page.wait_for_url(re.compile(r".*/$"), timeout=30000)
    await page.wait_for_timeout(1500)


async def export_click(page, idioma, tag):
    await page.get_by_role("button", name=re.compile("^Exportar")).first.click()
    async with page.expect_download(timeout=60000) as dl:
        await page.get_by_role("menuitem", name=idioma).click()
    d = await dl.value
    dest = os.path.join(UI, f"{tag}__{d.suggested_filename}")
    await d.save_as(dest)
    return d.suggested_filename, open(dest, "rb").read(), os.path.relpath(dest, os.path.join(OUT, "..")).replace("\\", "/")


def rows_of(raw):
    return list(csv.reader(io.StringIO(raw.decode("utf-8-sig"))))


async def main():
    u = D["usuarios"]
    codes_qa5 = {v["codigo"] for v in D["activos"].values()}
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        ctx = await browser.new_context(viewport={"width": 1440, "height": 900}, ignore_https_errors=True,
                                        locale="es-ES", accept_downloads=True)
        page = await ctx.new_page()
        await login(page, u["ADMIN_TI"]["usuario"], u["ADMIN_TI"]["clave"], u["ADMIN_TI"]["totp_secret"])

        # ── Exportes CSV desde cada listado ─────────────────────────────────
        casos = [
            ("/activos", "English", "assets", "Internal code", "UI-ACT-EN"),
            ("/activos", "Español", "activos", "Código interno", "UI-ACT-ES"),
            ("/operaciones/movimientos", "Italiano", "movimenti", "Data di assegnazione", "UI-MOV-IT"),
            ("/consumibles", "English", "consumables", "Name", "UI-CON-EN"),
            ("/compras/proveedores", "Italiano", "fornitori", "Nome", "UI-PRV-IT"),
            ("/compras/ordenes", "English", "purchase_orders", "Order number", "UI-ORD-EN"),
        ]
        for ruta, idioma, base_esperada, h0, cid in casos:
            await page.goto(f"{BASE}{ruta}")
            await page.wait_for_timeout(2500)
            try:
                fname, raw, ev = await export_click(page, idioma, cid)
            except Exception as e:  # noqa: BLE001
                check(cid, f"Exportar desde {ruta} ({idioma})", False, repr(e)[:200], severidad="alta")
                continue
            rows = rows_of(raw)
            problemas = []
            if not raw.startswith(b"\xef\xbb\xbf"):
                problemas.append("sin BOM")
            if rows[0][0] != h0:
                problemas.append(f"encabezado {rows[0][0]}")
            if ruta == "/activos" and {r[0] for r in rows[1:]} != codes_qa5 | ({r[0] for r in rows[1:]} - codes_qa5 if False else set()):
                ajenos = {r[0] for r in rows[1:]} - codes_qa5
                if ajenos:
                    problemas.append(f"filas de otra sede: {sorted(ajenos)[:3]}")
            check(cid, f"Botón Exportar en {ruta} ({idioma}): descarga CSV con BOM, idioma correcto y solo su sede",
                  not problemas, "; ".join(problemas) or f"{len(rows) - 1} filas, archivo '{fname}'", ev, "alta")
            check(cid + "-NOM", f"Nombre del archivo descargado desde {ruta} en {idioma} es localizado y con fecha (como el servidor)",
                  fname.startswith(base_esperada + "_"), f"nombre sugerido por el navegador: '{fname}' (servidor envía '{base_esperada}_AAAAMMDD_HHMMSS.csv')",
                  ev, "baja")
            await page.screenshot(path=os.path.join(UI, f"{cid}.png"))

        # ── Actas desde Movimientos ─────────────────────────────────────────
        await page.goto(f"{BASE}/operaciones/movimientos")
        await page.wait_for_timeout(2500)
        cod1 = D["activos"]["a1"]["codigo"]
        fila = page.get_by_role("checkbox").filter(has_text=cod1)
        row = page.locator("tr", has_text=cod1).first
        try:
            async with page.expect_download(timeout=60000) as dl:
                await row.get_by_role("button").nth(1).click()  # PDF individual (tras Word)
            d = await dl.value
            dest = os.path.join(UI, f"UI-ACTA-PDF__{d.suggested_filename}")
            await d.save_as(dest)
            raw = open(dest, "rb").read()
            txt, _, _ = pdf_text(raw)
            check("UI-ACTA-PDF", "Movimientos → acta de entrega PDF individual: se descarga y corresponde al activo",
                  raw.startswith(b"%PDF") and D["activos"]["a1"]["serie"] in txt and "NOTA: ENTREGA DE EQUIPO" in txt,
                  d.suggested_filename, os.path.relpath(dest, os.path.join(OUT, "..")))
            check("UI-ACTA-NOM", "Nombre del acta descargada desde la UI coincide con el del servidor (Acta_Entrega_…)",
                  d.suggested_filename.startswith("Acta_Entrega"), f"UI: '{d.suggested_filename}'", "", "baja", observacion=True)
        except Exception as e:  # noqa: BLE001
            check("UI-ACTA-PDF", "Movimientos → acta PDF individual", False, repr(e)[:200], severidad="alta")
        # Lote: seleccionar m1 y m2 (mismo colaborador) y "Acta (Word)"
        try:
            for code in (cod1, D["activos"]["a2"]["codigo"]):
                await page.locator("tr", has_text=code).first.get_by_role("checkbox").check()
            async with page.expect_download(timeout=60000) as dl:
                await page.get_by_role("button", name="Acta (Word)").click()
            d = await dl.value
            dest = os.path.join(UI, f"UI-LOTE__{d.suggested_filename}")
            await d.save_as(dest)
            txt, tables, _ = docx_text(open(dest, "rb").read())
            series = [r[3] for r in tables[1][1:]]
            check("UI-LOTE", "Movimientos → acta por lote (Word) con 2 activos del mismo colaborador",
                  sorted(series) == sorted([D["activos"]["a1"]["serie"], D["activos"]["a2"]["serie"]]),
                  f"{d.suggested_filename}; series {series}", os.path.relpath(dest, os.path.join(OUT, "..")))
        except Exception as e:  # noqa: BLE001
            check("UI-LOTE", "Movimientos → acta por lote", False, repr(e)[:200], severidad="alta")
        # Descargo desde el diálogo (movimiento devuelto de a3) en inglés
        try:
            await page.reload()
            await page.wait_for_timeout(2500)
            row3 = page.locator("tr", has_text=D["activos"]["a3"]["codigo"])
            n = await row3.count()
            target = None
            for i in range(n):
                if await row3.nth(i).get_by_role("button", name=re.compile("descargo", re.I)).count():
                    target = row3.nth(i)
                    break
            if target is None:
                # los tooltips no dan nombre accesible: tercer botón de la fila cerrada
                target = row3.first
                btn = target.get_by_role("button").nth(2)
            else:
                btn = target.get_by_role("button", name=re.compile("descargo", re.I))
            await btn.click()
            dlg = page.get_by_role("dialog")
            await dlg.wait_for(timeout=10000)
            await page.screenshot(path=os.path.join(UI, "UI-DESC-dialogo.png"))
            combo = dlg.get_by_role("combobox")
            if await combo.count():
                await combo.first.click()
                await page.get_by_role("option", name=re.compile("English|Inglés")).click()
            await dlg.get_by_label(re.compile("mensajero", re.I)).fill("QA5 Courier")
            async with page.expect_download(timeout=60000) as dl:
                await dlg.get_by_role("button", name=re.compile("Descargar|Generar", re.I)).click()
            d = await dl.value
            dest = os.path.join(UI, f"UI-DESC__{d.suggested_filename}")
            await d.save_as(dest)
            txt, _, _ = pdf_text(open(dest, "rb").read())
            plano = " ".join(txt.split())
            check("UI-DESC", "Movimientos → hoja de descargo (diálogo) en inglés con mensajero externo",
                  "NOTE: RETURN OF EQUIPMENT" in plano and "QA5 Courier" in plano and "External courier" in plano,
                  f"{d.suggested_filename}: {plano[:160]}", os.path.relpath(dest, os.path.join(OUT, "..")))
        except Exception as e:  # noqa: BLE001
            await page.screenshot(path=os.path.join(UI, "UI-DESC-error.png"))
            check("UI-DESC", "Movimientos → hoja de descargo desde el diálogo", False, repr(e)[:200], severidad="alta")

        # ── Etiquetas desde Activos ────────────────────────────────────────
        try:
            await page.goto(f"{BASE}/activos")
            await page.wait_for_timeout(2500)
            for k in ("a1", "a2"):
                await page.get_by_label(f"Seleccionar activo {D['activos'][k]['codigo']}").check()
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
        except Exception as e:  # noqa: BLE001
            await page.screenshot(path=os.path.join(UI, "UI-QR-error.png"))
            check("UI-QR", "Activos → Imprimir etiquetas", False, repr(e)[:200], severidad="alta")
        await ctx.close()

        # ── Auditor: bitácora ──────────────────────────────────────────────
        ctx = await browser.new_context(viewport={"width": 1440, "height": 900}, ignore_https_errors=True,
                                        locale="es-ES", accept_downloads=True)
        page = await ctx.new_page()
        await login(page, u["AUDITOR"]["usuario"], u["AUDITOR"]["clave"], u["AUDITOR"]["totp_secret"])
        await page.goto(f"{BASE}/auditoria")
        await page.wait_for_timeout(2500)
        try:
            fname, raw, ev = await export_click(page, "Italiano", "UI-AUD-IT")
            rows = rows_of(raw)
            check("UI-AUD-IT", "Auditoría → Exportar (Italiano) como AUDITOR de una sede",
                  raw.startswith(b"\xef\xbb\xbf") and rows[0][0] == "Data e ora", f"'{fname}', {len(rows) - 1} filas, {rows[0][:3]}", ev)
            check("UI-AUD-NOM", "Nombre del archivo de bitácora en italiano (registro_audit_…)", fname.startswith("registro_audit"),
                  f"'{fname}'", ev, "baja")
        except Exception as e:  # noqa: BLE001
            check("UI-AUD-IT", "Auditoría → Exportar", False, repr(e)[:200], severidad="alta")
        await page.screenshot(path=os.path.join(UI, "UI-AUD.png"))
        await browser.close()
    with open(os.path.join(OUT, "resultados_ui.json"), "w", encoding="utf-8") as f:
        json.dump(RES, f, ensure_ascii=False, indent=1)
    print(f"\n{sum(r['estado'] == 'OK' for r in RES)}/{len(RES)} OK")


if __name__ == "__main__":
    asyncio.run(main())

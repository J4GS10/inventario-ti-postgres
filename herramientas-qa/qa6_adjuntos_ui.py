"""
QA6 — Adjuntos desde la interfaz (Playwright/chromium) contra https://localhost:8443.

sa (un único login con TOTP): subir PNG/PDF a un activo QA6 y una factura a una
orden QA6, descargar y comparar SHA-256, confirmar en MinIO, errores de tipo y
tamaño, borrado, nombres especiales. Técnico y auditor de la sede 1: qué botones ven
y qué mensaje reciben. Técnico de otra sede: no encuentra el activo.
Capturas en salidas/qa6/ui_*.png; resultados en salidas/qa6/ui.json.
"""
import asyncio
import os
import re
import time

from playwright.async_api import async_playwright

from qa6_comun import (OUT, Registro, cargar_estado, credenciales_sa, jpg_bytes, objeto_en_bucket,
                       pdf_bytes, png_bytes, sha)
from qa_login import ORIGEN, codigo_totp_ui

R = Registro("ui")
e = cargar_estado()
FILES = os.path.join(OUT, "archivos_ui")
os.makedirs(FILES, exist_ok=True)
MB = 1024 * 1024


def archivo(nombre, contenido):
    p = os.path.join(FILES, nombre)
    with open(p, "wb") as f:
        f.write(contenido)
    return p


F = {
    "png": archivo("QA6-UI-foto.png", png_bytes()),
    "jpg": archivo("QA6-UI-foto.jpg", jpg_bytes()),
    "pdf": archivo("QA6-UI-acta.pdf", pdf_bytes(f"QA6 UI acta {time.time()}")),
    "fac": archivo("QA6-UI-factura.pdf", pdf_bytes(f"QA6 UI factura {time.time()}")),
    "exe": archivo("QA6-UI-setup.exe", b"MZ" + b"\x00" * 500),
    "svg": archivo("QA6-UI-logo.svg", b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'),
    "big11": archivo("QA6-UI-11MB.pdf", b"%PDF-1.4\n" + b"0" * (11 * MB)),
    "big25": archivo("QA6-UI-25MB.pdf", b"%PDF-1.4\n" + b"0" * (25 * MB)),
    "vacio": archivo("QA6-UI-vacio.pdf", b""),
}
alertas = []


async def snack(page, timeout=20000):
    """Texto del último aviso (notistack)."""
    loc = page.locator("#notistack-snackbar")
    try:
        await loc.last.wait_for(timeout=timeout)
        return (await loc.last.inner_text()).strip()
    except Exception:  # noqa: BLE001
        return "(sin aviso)"


async def cerrar_snacks(page):
    await page.wait_for_timeout(300)
    for b in await page.locator("#notistack-snackbar").all():
        try:
            await b.evaluate("n => n.closest('.notistack-SnackbarContainer, .notistack-MuiContent')?.remove()")
        except Exception:  # noqa: BLE001
            pass


async def login(page, usuario, clave, secret=None):
    await page.goto(f"{ORIGEN}/login")
    await page.fill("input[name=username]", usuario)
    await page.fill("input[name=password]", clave)
    await page.click("button[type=submit]")
    if secret:
        campo = page.get_by_label("Código de verificación")
        await campo.wait_for(timeout=20000)
        await campo.fill(codigo_totp_ui(usuario, secret))  # un solo intento; nunca se reintenta
        await page.get_by_role("button", name="Verificar").click()
    await page.wait_for_url(re.compile(r"^(?!.*login).*$"), timeout=30000)
    await page.wait_for_timeout(1500)


async def abrir_activo(page, serie):
    await page.goto(f"{ORIGEN}/activos")
    await page.get_by_placeholder(re.compile("Buscar por código")).first.fill(serie)
    await page.wait_for_timeout(2500)
    fila = page.locator("tbody tr", has_text=serie)
    if await fila.count() == 0:
        return None
    await fila.first.get_by_role("button", name="Ver detalle").click()
    dlg = page.get_by_role("dialog")
    await dlg.get_by_role("tab", name=re.compile("Adjuntos")).click()
    await page.wait_for_timeout(1500)
    return dlg


async def subir(page, dlg, ruta, categoria=None):
    if categoria:
        await dlg.get_by_label("Categoría").click()
        await page.get_by_role("option", name=categoria).click()
    await cerrar_snacks(page)
    boton = dlg.get_by_role("button", name="Subir archivo")
    await dlg.locator("input[type=file]").set_input_files(ruta)
    # Indicador de progreso: el botón se deshabilita y muestra un spinner mientras sube.
    en_progreso = False
    for _ in range(20):
        if await boton.is_disabled():
            en_progreso = True
            break
        await page.wait_for_timeout(50)
    return en_progreso, await snack(page)


async def descargar(page, dlg, nombre):
    fila = dlg.locator("tbody tr", has_text=nombre).first
    async with page.expect_download(timeout=30000) as d:
        await fila.get_by_role("button", name="Descargar").click()
    dl = await d.value
    destino = os.path.join(FILES, "desc_" + dl.suggested_filename)
    await dl.save_as(destino)
    with open(destino, "rb") as f:
        return dl.suggested_filename, f.read()


async def adjuntos_api(page, url):
    """Lee la lista vía la sesión del navegador (token del almacenamiento de la app)."""
    return await page.evaluate("""async (url) => {
        const r = await fetch(url, {headers: {Authorization: 'Bearer ' + (window.__qa6_token || '')}});
        return r.status; }""", url)


async def main():
    cu = e["cuentas"]
    serie = f"QA6-SER-{e['suf']}"
    async with async_playwright() as p:
        browser = await p.chromium.launch()

        # ═══ sa ═══════════════════════════════════════════════════════════
        ctx = await browser.new_context(ignore_https_errors=True, accept_downloads=True,
                                        viewport={"width": 1440, "height": 900}, locale="es-ES")
        page = await ctx.new_page()
        page.on("dialog", lambda d: (alertas.append(d.message), asyncio.ensure_future(d.dismiss())))
        subidas = {}

        async def capturar(resp):
            if "/adjuntos/" in resp.url and resp.request.method == "POST":
                try:
                    subidas[resp.url + str(time.time())] = (resp.status, await resp.json())
                except Exception:  # noqa: BLE001
                    subidas[resp.url + str(time.time())] = (resp.status, None)
        page.on("response", lambda r: asyncio.ensure_future(capturar(r)))

        cred = credenciales_sa()
        await login(page, cred["usuario"], cred["clave"], cred["totp_secret"])
        dlg = await abrir_activo(page, serie)
        if dlg is None:
            R.caso("U00", "Abrir el activo QA6 en la UI", False, "no se encontró el activo", "fila visible")
            return
        await page.screenshot(path=f"{OUT}/ui_01_activo_adjuntos.png")
        texto = await dlg.inner_text()
        hint = "PDF, imágenes, Word o Excel. Máx. 10 MB." in texto
        acc = await dlg.locator("input[type=file]").get_attribute("accept")
        R.caso("U01", "Pestaña Adjuntos del activo: lista, ayuda y selector de archivos", hint and acc,
               f"ayuda visible={hint}; atributo accept del <input type=file>={acc!r}",
               "Lista y ayuda visibles; el selector filtra por tipos permitidos (accept)", "salidas/qa6/ui_01_activo_adjuntos.png")
        # Nombres especiales creados por API (D03 Unicode, D04 HTML): se muestran como texto.
        xss_txt = "QA6-<img src=x onerror=alert(1)>.pdf" in texto
        uni_txt = "ñandú №5 «año»" in texto and "😀" in texto
        R.caso("U02", "Nombres con HTML y Unicode se muestran como texto (sin XSS)", xss_txt and uni_txt and not alertas,
               f"HTML literal visible={xss_txt}; Unicode íntegro={uni_txt}; alert() disparados={alertas}",
               "Texto literal, sin ejecutar script", "salidas/qa6/ui_01_activo_adjuntos.png")

        # U03 PNG (foto)
        prog, msg = await subir(page, dlg, F["png"], "Foto")
        await page.wait_for_timeout(300)
        await page.screenshot(path=f"{OUT}/ui_03_subida_png.png")
        await dlg.locator("tbody tr", has_text="QA6-UI-foto.png").first.wait_for(timeout=15000)
        nombre, cont = await descargar(page, dlg, "QA6-UI-foto.png")
        adj = [v[1] for v in subidas.values() if v[1] and v[1].get("ADJ_Nombre_Original") == "QA6-UI-foto.png"]
        existe, sha_b, _ = objeto_en_bucket(adj[-1]["ADJ_Object_Key"]) if adj else (False, None, "")
        h = sha(open(F["png"], "rb").read())
        fila = await dlg.locator("tbody tr", has_text="QA6-UI-foto.png").first.inner_text()
        R.caso("U03", "Subir PNG (Foto) al activo desde la UI y descargarlo", msg == "Archivo subido" and sha(cont) == h and existe and sha_b == h
               and "Foto" in fila,
               f"indicador de progreso={prog}; aviso={msg!r}; fila={fila.replace(chr(10), ' | ')!r}; descarga {nombre!r} sha "
               f"{'=' if sha(cont) == h else '≠'}; MinIO existe={existe} sha {'=' if sha_b == h else '≠'}",
               "Aviso de éxito, fila con categoría Foto, descarga idéntica y objeto en el bucket", "salidas/qa6/ui_03_subida_png.png")

        # U04 PDF (acta) y JPG
        prog, msg = await subir(page, dlg, F["pdf"], "Acta")
        await dlg.locator("tbody tr", has_text="QA6-UI-acta.pdf").first.wait_for(timeout=15000)
        nombre, cont = await descargar(page, dlg, "QA6-UI-acta.pdf")
        h = sha(open(F["pdf"], "rb").read())
        prog2, msg2 = await subir(page, dlg, F["jpg"], "Foto")
        await dlg.locator("tbody tr", has_text="QA6-UI-foto.jpg").first.wait_for(timeout=15000)
        n2, c2 = await descargar(page, dlg, "QA6-UI-foto.jpg")
        h2 = sha(open(F["jpg"], "rb").read())
        R.caso("U04", "Subir PDF (Acta) y JPG (Foto) desde la UI y descargarlos",
               msg == msg2 == "Archivo subido" and sha(cont) == h and sha(c2) == h2,
               f"PDF: aviso={msg!r}, sha {'=' if sha(cont) == h else '≠'}; JPG: aviso={msg2!r}, sha {'=' if sha(c2) == h2 else '≠'}",
               "Ambos con aviso de éxito y descarga idéntica")

        # U05..U08 errores
        for cid, clave, caso, esperado in (
            ("U05", "exe", "Subir un .exe desde la UI", "Tipo de archivo no permitido."),
            ("U06", "svg", "Subir un .svg con script desde la UI", "Tipo de archivo no permitido."),
            ("U07", "big11", "Subir 11 MB desde la UI (supera 10 MB)", "El archivo supera el tamaño máximo permitido."),
            ("U08", "big25", "Subir 25 MB desde la UI (supera el límite de nginx, 20 MB)", "El archivo supera el tamaño máximo permitido."),
            ("U09", "vacio", "Subir un archivo vacío desde la UI", "Mensaje claro: el archivo está vacío"),
        ):
            t0 = time.time()
            prog, msg = await subir(page, dlg, F[clave])
            await page.wait_for_timeout(200)
            await page.screenshot(path=f"{OUT}/ui_{cid}_{clave}.png")
            ok = msg == esperado if cid != "U09" else ("vací" in msg.lower())
            R.caso(cid, caso, ok, f"aviso={msg!r} (tras {time.time() - t0:.1f} s; progreso={prog})",
                   f"Aviso de error: {esperado!r}", f"salidas/qa6/ui_{cid}_{clave}.png")

        # U10 borrar desde la UI
        await cerrar_snacks(page)
        fila = dlg.locator("tbody tr", has_text="QA6-UI-foto.png").first
        await fila.get_by_role("button", name="Eliminar").click()
        await page.get_by_role("dialog").filter(has_text="eliminar este elemento").wait_for(timeout=10000)
        await page.screenshot(path=f"{OUT}/ui_10_confirmar_borrado.png")
        await page.get_by_role("dialog").filter(has_text="eliminar este elemento").get_by_role("button", name="Eliminar").click()
        await page.wait_for_timeout(2500)
        sigue = await dlg.locator("tbody tr", has_text="QA6-UI-foto.png").count()
        msg = await snack(page, 3000)
        existe_desp, _, _ = objeto_en_bucket(adj[-1]["ADJ_Object_Key"]) if adj else (None, None, "")
        await page.screenshot(path=f"{OUT}/ui_10_tras_borrado.png")
        R.caso("U10", "Borrar un adjunto desde la UI (con confirmación)", sigue == 0 and existe_desp is False,
               f"confirmación mostrada; filas restantes={sigue}; objeto en MinIO={existe_desp}; aviso tras borrar={msg!r}",
               "Pide confirmación, desaparece de la lista y del bucket, y avisa que se eliminó", "salidas/qa6/ui_10_tras_borrado.png")
        R.caso("U11", "Aviso de éxito al borrar", msg != "(sin aviso)", f"aviso={msg!r}",
               "Aviso de éxito ('Archivo eliminado') como al subir", "salidas/qa6/ui_10_tras_borrado.png")
        await page.keyboard.press("Escape")

        # U12 orden de compra: factura
        await page.goto(f"{ORIGEN}/compras/ordenes")
        await page.wait_for_timeout(2500)
        await page.locator("tbody tr", has_text=e["orden_numero"]).first.click()
        dlg = page.get_by_role("dialog")
        await dlg.get_by_text("Factura y documentos").wait_for(timeout=15000)
        await dlg.get_by_text("Factura y documentos").scroll_into_view_if_needed()
        cat = await dlg.get_by_label("Categoría").inner_text()
        prog, msg = await subir(page, dlg, F["fac"])
        await dlg.locator("tbody tr", has_text="QA6-UI-factura.pdf").first.wait_for(timeout=15000)
        await dlg.locator("tbody tr", has_text="QA6-UI-factura.pdf").first.scroll_into_view_if_needed()
        await page.screenshot(path=f"{OUT}/ui_12_orden_factura.png")
        nombre, cont = await descargar(page, dlg, "QA6-UI-factura.pdf")
        h = sha(open(F["fac"], "rb").read())
        adjo = [v[1] for v in subidas.values() if v[1] and v[1].get("ADJ_Nombre_Original") == "QA6-UI-factura.pdf"]
        existe, sha_b, _ = objeto_en_bucket(adjo[-1]["ADJ_Object_Key"]) if adjo else (False, None, "")
        R.caso("U12", "Subir la factura a la orden de compra desde la UI y descargarla",
               msg == "Archivo subido" and sha(cont) == h and existe and sha_b == h and "Factura" in cat,
               f"categoría por defecto={cat!r}; aviso={msg!r}; descarga sha {'=' if sha(cont) == h else '≠'}; "
               f"MinIO {adjo[-1]['ADJ_Object_Key'] if adjo else '-'} existe={existe} sha {'=' if sha_b == h else '≠'}",
               "Categoría Factura por defecto, aviso de éxito, descarga idéntica, objeto en ordenes/<id>/",
               "salidas/qa6/ui_12_orden_factura.png")
        if adjo:
            e.setdefault("persistencia", []).append({"id": adjo[-1]["ADJ_Adjunto"], "key": adjo[-1]["ADJ_Object_Key"],
                                                     "sha": h, "caso": "U12"})
            from qa6_comun import guardar_estado
            guardar_estado(e)
        await ctx.close()

        # ═══ Técnico y auditor de la sede 1 ═══════════════════════════════
        for clave, cid in (("tec1", "U13"), ("aud1", "U14")):
            ctx = await browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900}, locale="es-ES")
            page = await ctx.new_page()
            await login(page, cu[clave]["usuario"], cu[clave]["clave"])
            dlg = await abrir_activo(page, serie)
            if dlg is None:
                R.caso(cid, f"{cu[clave]['rol']} abre los adjuntos del activo", False, "no ve el activo", "")
                await ctx.close()
                continue
            ver_subir = await dlg.get_by_role("button", name="Subir archivo").count()
            ver_borrar = await dlg.get_by_role("button", name="Eliminar").count()
            detalle = ""
            if ver_borrar:
                await cerrar_snacks(page)
                await dlg.get_by_role("button", name="Eliminar").first.click()
                cd = page.get_by_role("dialog").filter(has_text="eliminar este elemento")
                await cd.get_by_role("button", name="Eliminar").click()
                detalle = f"; al intentar borrar: aviso={await snack(page)!r}"
            if clave == "aud1" and ver_subir:
                _, m = await subir(page, dlg, F["pdf"])
                detalle += f"; al intentar subir: aviso={m!r}"
            await page.screenshot(path=f"{OUT}/ui_{cid}_{clave}.png")
            if clave == "tec1":
                ok = ver_borrar == 0
                esp = "El técnico puede subir pero no ve el botón Eliminar (el servidor responde 403)"
            else:
                ok = ver_borrar == 0 and ver_subir == 0
                esp = "El auditor (solo lectura) no ve Subir ni Eliminar; solo Descargar"
            R.caso(cid, f"UI según rol: {cu[clave]['rol']} de la sede 1", ok,
                   f"botón Subir visible={bool(ver_subir)}; botones Eliminar visibles={ver_borrar}{detalle}", esp,
                   f"salidas/qa6/ui_{cid}_{clave}.png")
            await ctx.close()

        # ═══ Técnico de otra sede ═════════════════════════════════════════
        ctx = await browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900}, locale="es-ES")
        page = await ctx.new_page()
        await login(page, cu["tecsur"]["usuario"], cu["tecsur"]["clave"])
        dlg = await abrir_activo(page, serie)
        await page.screenshot(path=f"{OUT}/ui_15_tecnico_otra_sede.png")
        R.caso("U15", "Técnico de otra sede no encuentra el activo ni sus adjuntos en la UI", dlg is None,
               "activo no listado" if dlg is None else "activo visible", "Sin resultados",
               "salidas/qa6/ui_15_tecnico_otra_sede.png")
        await ctx.close()
        await browser.close()
    print("\nCasos UI:", len(R.casos), "OK:", sum(x["ok"] for x in R.casos))


asyncio.run(main())

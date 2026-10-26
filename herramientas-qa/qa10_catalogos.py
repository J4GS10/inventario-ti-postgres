"""
QA del Centro de catálogos y de los formularios con catálogo (plan de licencias
y catálogos, bloque B) en el stack DESECHABLE inv_qa (https://localhost:8443).

Requiere la cuenta de prueba: python herramientas-qa/cuenta_inv_qa.py
Uso:  python herramientas-qa/qa10_catalogos.py   (resultado en salidas/qa10/)
"""
import asyncio
import json
import os
import re
import time

import pyotp
from playwright.async_api import async_playwright

AQUI = os.path.dirname(os.path.abspath(__file__))
BASE = "https://localhost:8443"
API = f"{BASE}/api/v1"
OUT = os.path.join(AQUI, "salidas", "qa10")
os.makedirs(OUT, exist_ok=True)
CRED = json.load(open(os.path.join(AQUI, "salidas", "credenciales_inv_qa.json"), encoding="utf-8"))
SUF = time.strftime("%H%M%S")
RES: list[dict] = []
BOX: dict = {}


def caso(nombre: str, ok: bool, **extra) -> None:
    RES.append({"caso": nombre, "ok": bool(ok), **extra})
    print(("OK   " if ok else "FALLA"), nombre, "" if ok else json.dumps(extra, ensure_ascii=False)[:400])


async def shot(page, nombre: str) -> str:
    ruta = os.path.join(OUT, f"{nombre}.png")
    await page.screenshot(path=ruta, full_page=True)
    return ruta


async def api(page, method: str, path: str, body=None):
    r = await page.request.fetch(f"{API}{path}", method=method, headers={"Authorization": f"Bearer {BOX['token']}"},
                                 data=body, ignore_https_errors=True)
    try:
        return r.status, await r.json()
    except Exception:  # noqa: BLE001
        return r.status, await r.text()


async def desborde(page) -> int:
    return await page.evaluate("document.documentElement.scrollWidth - window.innerWidth")


async def snacks(page, espera=1500):
    await page.wait_for_timeout(espera)
    return await page.evaluate(
        "() => [...document.querySelectorAll('#notistack-snackbar, .notistack-MuiContent')].map(e => e.innerText)")


async def login(page, lang="es"):
    async def capturar(resp):
        if ("/login/2fa/verify" in resp.url or "/login/refresh" in resp.url) and resp.ok:
            try:
                data = await resp.json()
                if data.get("access_token"):
                    BOX["token"] = data["access_token"]
            except Exception:  # noqa: BLE001
                pass
    page.on("response", capturar)
    await page.goto(f"{BASE}/login")
    await page.evaluate(f"localStorage.setItem('app_language', '{lang}')")
    await page.goto(f"{BASE}/login")
    await page.wait_for_load_state("networkidle")
    await page.fill("input[name=username]", CRED["usuario"])
    await page.fill("input[name=password]", CRED["clave"])
    await page.click("button[type=submit]")
    otp = page.locator("input[autocomplete=one-time-code]")
    await otp.wait_for(timeout=15000)
    # Anti-replay: un código TOTP solo se acepta una vez; se espera a una ventana no usada.
    marca = os.path.join(AQUI, "salidas", ".totp_qa_catalogos")
    ultima = int(open(marca).read()) if os.path.exists(marca) else -1
    while int(time.time()) // 30 <= ultima:
        await asyncio.sleep(1)
    open(marca, "w").write(str(int(time.time()) // 30))
    await otp.fill(pyotp.TOTP(CRED["totp_secret"]).now())
    await otp.press("Enter")
    await page.wait_for_url(lambda u: "/login" not in u, timeout=20000)
    await page.wait_for_load_state("networkidle")
    if "token" not in BOX:
        # El token vive en memoria de la SPA: se pide uno con la cookie de sesión del navegador.
        r = await page.request.post(f"{API}/login/refresh", ignore_https_errors=True)
        if r.ok:
            BOX["token"] = (await r.json()).get("access_token")
    # Ayuda contextual: marcarla como vista para que no tape la pantalla en las pruebas.
    import base64
    sub = json.loads(base64.urlsafe_b64decode(BOX["token"].split(".")[1] + "==")).get("sub")
    await page.evaluate("""(sub) => ['assets', 'movements', 'software', 'catalogs', 'persons', 'maintenance']
        .forEach((m) => localStorage.setItem(`ayuda:${sub}:${m}`, '1'))""", sub)


async def abrir_catalogo(page, nombre_re):
    await page.locator("main").get_by_role("button", name=nombre_re).first.click()
    await page.wait_for_load_state("networkidle")
    await page.wait_for_timeout(800)


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        ctx = await b.new_context(viewport={"width": 1440, "height": 900}, ignore_https_errors=True, locale="es-ES")
        page = await ctx.new_page()
        await login(page)
        caso("login", "token" in BOX)

        # ---------- Datos de apoyo por API ----------
        # Kits de corridas anteriores: un cargo admite un solo kit activo.
        _, kits = await api(page, "GET", "/catalogos/kits")
        for k in kits if isinstance(kits, list) else []:
            if k["KIT_Nombre"].startswith("Kit QA"):
                await api(page, "DELETE", f"/catalogos/kits/{k['KIT_Kit']}")
        _, marcas = await api(page, "GET", "/catalogos/marcas")
        kingston = next((m for m in marcas if m["nombre"].lower() == "kingston"), None)
        if kingston is None:
            _, kingston = await api(page, "POST", "/catalogos/marcas", {"nombre": "Kingston"})
        st, kingstom = await api(page, "POST", "/catalogos/marcas", {"nombre": f"Kingstom {SUF}"})
        st2, modelo = await api(page, "POST", "/cat/modelos", {"MOD_Nombre": f"DT QA {SUF}", "MAR_Marca": kingstom["id"]})
        caso("datos: marca duplicada y modelo", st == 201 and st2 == 201, detalle=[st, st2])

        # ---------- C1: Centro de catálogos ----------
        await page.goto(f"{BASE}/config/catalogos")
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(1200)
        texto = await page.locator("main").inner_text()
        caso("C1 centro carga con grupos", all(x in texto for x in ("Movimientos y bajas", "Mantenimiento", "Documentos")),
             shot=await shot(page, "C1_centro"))
        caso("C1 sin desborde horizontal (escritorio)", await desborde(page) <= 0)
        caso("C2 motivos de sistema traducidos y marcados",
             "Ingreso de personal" in texto and "Sistema" in texto)

        # ---------- C3: agregar y desactivar ----------
        nombre = f"Auditoría QA {SUF}"
        await page.get_by_label("Nuevo registro").fill(nombre)
        await page.get_by_role("button", name=re.compile("^Agregar$")).click()
        await page.wait_for_timeout(1200)
        fila = page.locator("tr", has=page.locator(f"input[value='{nombre}']"))
        caso("C3 agregar registro", await fila.count() == 1, snacks=await snacks(page, 300))
        await fila.locator("input[aria-label='Activo']").click()
        await page.wait_for_timeout(1200)
        caso("C3 desactivar lo oculta", await page.locator(f"input[value='{nombre}']").count() == 0)
        await page.get_by_label("Mostrar inactivos").check()
        await page.wait_for_timeout(600)
        caso("C3 visible con «Mostrar inactivos»", await page.locator(f"input[value='{nombre}']").count() == 1,
             shot=await shot(page, "C3_inactivos"))

        # ---------- C4: fusionar la marca duplicada desde la UI ----------
        await abrir_catalogo(page, re.compile("^Marcas$"))
        fila = page.locator("tr", has=page.locator(f"input[value='Kingstom {SUF}']"))
        await fila.get_by_role("button", name="Fusionar con otro").click()
        dlg = page.get_by_role("dialog")
        await dlg.wait_for()
        await dlg.get_by_label("Fusionar en").fill("Kingston")
        await page.get_by_role("option", name=re.compile("^Kingston$")).click()
        await page.wait_for_timeout(1500)
        previa = await dlg.inner_text()
        caso("C4 vista previa de la fusión", "Se moverá 1 registro" in previa and "Modelos" in previa,
             shot=await shot(page, "C4_fusion_previa"), texto=previa[:300])
        await dlg.get_by_role("button", name="Fusionar", exact=True).click()
        sn = await snacks(page, 1500)
        _, mod = await api(page, "GET", f"/cat/modelos/{modelo['MOD_Modelo']}")
        _, todas = await api(page, "GET", "/catalogos/marcas?inactivos=true")
        origen = next(m for m in todas if m["id"] == kingstom["id"])
        caso("C4 fusión mueve el modelo y deja el origen inactivo",
             mod.get("MAR_Marca") == kingston["id"] and not origen["activo"] and origen["fusionado_en"] == kingston["id"],
             snacks=sn)
        _, aud = await api(page, "GET", "/gov/auditoria?accion=CATALOG_MERGE&limit=5")
        caso("C4 fusión auditada", "CATALOG_MERGE" in json.dumps(aud), detalle=str(aud)[:200])

        # ---------- C5: especificaciones por tipo ----------
        await abrir_catalogo(page, re.compile("^Especificaciones por tipo de activo$"))
        await page.get_by_label("Tipo de activo").click()
        await page.get_by_role("option", name=re.compile("^Laptop$")).click()
        await page.wait_for_timeout(1200)
        t5 = await page.locator("main").inner_text()
        caso("C5 especificaciones de Laptop", "RAM" in t5 and "Procesador" in t5, shot=await shot(page, "C5_especificaciones"))

        # ---------- C6: kit de puesto ----------
        await abrir_catalogo(page, re.compile("^Kits de puesto$"))
        await page.get_by_role("button", name="Nuevo kit").click()
        dlg = page.get_by_role("dialog")
        await dlg.get_by_label("Nombre").fill(f"Kit QA {SUF}")
        await dlg.get_by_role("combobox").nth(0).click()   # cargo
        await page.get_by_role("option").first.click()
        await dlg.get_by_role("combobox").nth(1).click()   # tipo de activo
        await page.get_by_role("option", name=re.compile("^Laptop$")).click()
        await dlg.get_by_role("button", name="Guardar").click()
        await page.wait_for_timeout(1500)
        t6 = await page.locator("main").inner_text()
        caso("C6 kit creado", f"Kit QA {SUF}" in t6 and "1 × Laptop" in t6, shot=await shot(page, "C6_kits"))

        # ---------- C7: formularios con catálogo ----------
        await page.goto(f"{BASE}/mantenimiento")
        await page.wait_for_load_state("networkidle")
        await page.get_by_role("button", name=re.compile("Nuevo ticket|Nuevo", re.I)).first.click()
        dlg = page.get_by_role("dialog")
        await dlg.wait_for()
        await dlg.get_by_label("Tipo de falla").click()
        opciones = await page.get_by_role("option").all_inner_texts()
        caso("C7 ticket pide tipo de falla del catálogo", "No enciende" in opciones and "Pantalla" in opciones,
             shot=await shot(page, "C7_ticket"))
        await page.keyboard.press("Escape")
        await page.keyboard.press("Escape")

        await page.goto(f"{BASE}/consumibles")
        await page.wait_for_load_state("networkidle")
        await page.get_by_role("button", name=re.compile("Nuevo", re.I)).first.click()
        dlg = page.get_by_role("dialog")
        await dlg.wait_for()
        await page.wait_for_timeout(800)
        unidad = await dlg.get_by_label("Unidad", exact=False).last.input_value()
        caso("C7 consumible con unidad por defecto del catálogo", unidad == "Unidad", unidad=unidad,
             shot=await shot(page, "C7_consumible"))
        await page.keyboard.press("Escape")

        # ---------- C8: inglés y móvil ----------
        await page.evaluate("localStorage.setItem('app_language', 'en')")
        await page.goto(f"{BASE}/config/catalogos?c=motivos-movimiento")
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(1200)
        t8 = await page.locator("main").inner_text()
        caso("C8 textos en inglés", "Catalog center" in t8 and "New hire" in t8, shot=await shot(page, "C8_ingles"))
        await page.evaluate("localStorage.setItem('app_language', 'es')")
        await page.set_viewport_size({"width": 390, "height": 844})
        await page.goto(f"{BASE}/config/catalogos?c=motivos-baja")
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(1200)
        caso("C8 móvil sin desborde horizontal", await desborde(page) <= 0, shot=await shot(page, "C8_movil"))

        await b.close()
    json.dump(RES, open(os.path.join(OUT, "resultado.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    ok = sum(r["ok"] for r in RES)
    print(f"\n{ok}/{len(RES)} casos correctos")


if __name__ == "__main__":
    asyncio.run(main())

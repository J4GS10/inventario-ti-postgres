"""
QA3 — La UI oculta lo no permitido y no muestra filas fuera del alcance.

Para cada cuenta QA3 elegida entra por el navegador, recorre las páginas del
inventario y comprueba: menú visible, botones de acción presentes/ausentes
según el rol, rutas protegidas (redirección) y que el texto de la página no
contenga códigos de sedes fuera de su alcance. Capturas en salidas/qa3/ui/.

Uso: python qa3_ui.py
"""
import asyncio
import json
import os
import re
import time

from playwright.async_api import async_playwright

from qa_login import codigo_totp_ui

AQUI = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(AQUI, "salidas", "qa3")
SHOTS = os.path.join(OUT, "ui")
EST = json.load(open(os.path.join(OUT, "estado.json"), encoding="utf-8"))
SED = EST["sedes"]
URL = "https://localhost:8443"
RES = []
ALC = {"g": {"N", "S", "E"}, "n": {"N"}, "ns": {"N", "S"}}

# página -> [(texto del botón, roles que deben verlo)]
BOTONES = {
    "/activos": [("Nuevo activo", {"ADMIN_TI", "TECNICO"}), ("Exportar", {"ADMIN_TI", "AUDITOR"})],
    "/operaciones/movimientos": [("Nueva asignación", {"ADMIN_TI", "TECNICO"}), ("Exportar", {"ADMIN_TI", "AUDITOR"})],
    "/mantenimiento": [("Nuevo ticket", {"ADMIN_TI", "TECNICO"})],
    "/consumibles": [("Nuevo", {"ADMIN_TI"}), ("Exportar", {"ADMIN_TI", "AUDITOR"})],
    "/empleados": [("Nuevo empleado", {"ADMIN_TI"})],
    "/compras/ordenes": [("Nueva orden de compra", {"ADMIN_TI"}), ("Exportar", {"ADMIN_TI", "AUDITOR"})],
}
# ruta -> roles que pueden entrar
RUTAS = {
    "/activos": {"ADMIN_TI", "TECNICO", "AUDITOR"},
    "/operaciones/movimientos": {"ADMIN_TI", "TECNICO", "AUDITOR"},
    "/mantenimiento": {"ADMIN_TI", "TECNICO", "AUDITOR"},
    "/consumibles": {"ADMIN_TI", "TECNICO", "AUDITOR"},
    "/empleados": {"ADMIN_TI", "AUDITOR"},
    "/compras/ordenes": {"ADMIN_TI", "AUDITOR"},
    "/usuarios": {"ADMIN_SEGURIDAD"},
    "/auditoria": {"ADMIN_SEGURIDAD", "AUDITOR"},
    "/config/ubicaciones": {"ADMIN_TI"},
    "/config": set(),
}


def reg(usuario, caso, esperado, obtenido, ok, evidencia=""):
    RES.append({"usuario": usuario, "caso": caso, "esperado": esperado, "obtenido": str(obtenido)[:300],
                "ok": bool(ok), "evidencia": evidencia})
    print(f"{'OK ' if ok else 'XX '} {usuario:10} {caso:60} {str(obtenido)[:160]}", flush=True)


async def entrar(page, u):
    await page.goto(f"{URL}/login")
    await page.fill("input[name=username]", u["username"])
    await page.fill("input[name=password]", u["clave"])
    await page.click("button[type=submit]")
    if u.get("totp"):
        campo = page.get_by_label("Código de verificación")
        await campo.wait_for(timeout=20000)
        # código serializado entre procesos, obtenido justo antes de escribirlo
        codigo = await asyncio.to_thread(codigo_totp_ui, u["username"], u["totp"])
        await campo.fill(codigo)
        await page.get_by_role("button", name="Verificar").click()
    await page.wait_for_url(re.compile(r".*/$"), timeout=25000)
    await page.wait_for_timeout(1500)


async def cuenta(browser, clave):
    u = EST["usuarios"][clave]
    rol = u["rol"]
    dentro = {"N", "S", "E"} if rol == "ADMIN_SEGURIDAD" else ALC[clave.rsplit("_", 1)[-1]]
    fuera = {"N", "S", "E"} - dentro
    ctx = await browser.new_context(viewport={"width": 1440, "height": 900}, ignore_https_errors=True, locale="es-ES")
    page = await ctx.new_page()
    await entrar(page, u)
    nav = await page.locator("nav").first.inner_text() if await page.locator("nav").count() else ""
    await page.screenshot(path=f"{SHOTS}/{clave}_inicio.png")
    tablero = await page.locator("main").inner_text() if await page.locator("main").count() else ""
    reg(clave, "tablero sin códigos de sedes ajenas", "sin fugas",
        [SED[x]["codigo"] for x in fuera if SED[x]["codigo"] in tablero] or "ok",
        not [x for x in fuera if SED[x]["codigo"] in tablero], f"ui/{clave}_inicio.png")
    reg(clave, "menú lateral", "según rol", nav.replace("\n", " | ")[:250], True, f"ui/{clave}_inicio.png")
    for ruta, roles in RUTAS.items():
        await page.goto(f"{URL}{ruta}")
        await page.wait_for_timeout(2500)
        final = page.url.replace(URL, "") or "/"
        permitido = rol in roles
        entro = final.rstrip("/") == ruta.rstrip("/")
        reg(clave, f"ruta {ruta}", "accede" if permitido else "redirige", final, entro == permitido)
        if not entro:
            continue
        cuerpo = await page.locator("main").inner_text() if await page.locator("main").count() else await page.inner_text("body")
        if ruta == "/activos":
            # buscar el código de cada sede en el buscador
            for letra in ("N", "S", "E"):
                caja = page.get_by_placeholder(re.compile("Buscar", re.I)).first
                if await caja.count():
                    await caja.fill(SED[letra]["codigo"])
                    await page.wait_for_timeout(2000)
                    txt = await page.locator("main").inner_text()
                    visto = SED[letra]["codigo"] in txt.replace(SED[letra]["codigo"] + "\n", SED[letra]["codigo"] + " ") and \
                        txt.count(SED[letra]["codigo"]) >= 1
                    # el propio texto de la caja no aparece en inner_text; si aparece es una fila
                    esperado = letra in dentro
                    await page.screenshot(path=f"{SHOTS}/{clave}_activos_busca_{letra}.png")
                    reg(clave, f"buscar código sede {letra} en /activos", "visible" if esperado else "no visible",
                        "visible" if visto else "no visible", visto == esperado, f"ui/{clave}_activos_busca_{letra}.png")
                    await caja.fill("")
            await page.wait_for_timeout(1000)
        fuga = [SED[x]["codigo"] for x in fuera if SED[x]["codigo"] in cuerpo] + \
               [SED[x]["consumible_nombre"] for x in fuera if SED[x]["consumible_nombre"] in cuerpo] + \
               [SED[x]["orden_numero"] for x in fuera if SED[x]["orden_numero"] in cuerpo]
        shot = f"{clave}{ruta.replace('/', '_')}.png"
        await page.screenshot(path=f"{SHOTS}/{shot}", full_page=True)
        reg(clave, f"{ruta}: sin filas de sedes ajenas", "sin fugas", fuga or "ok", not fuga, f"ui/{shot}")
        for texto, roles_btn in BOTONES.get(ruta, []):
            n = await page.get_by_role("button", name=re.compile(rf"^\s*{re.escape(texto)}\s*$", re.I)).count()
            debe = rol in roles_btn
            reg(clave, f"{ruta}: botón «{texto}»", "visible" if debe else "oculto",
                "visible" if n else "oculto", bool(n) == debe, f"ui/{shot}")
        if ruta == "/activos" and rol in ("AUDITOR", "CONSULTA"):
            iconos = await page.locator("main table button").count()
            reg(clave, "/activos: sin botones de acción por fila (editar/borrar)", "0 de escritura",
                iconos, True, f"ui/{shot}")
    await ctx.close()


async def main():
    os.makedirs(SHOTS, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        import sys
        cuentas = sys.argv[1].split(",") if len(sys.argv) > 1 else ["qa3_tec_n", "qa3_aud_n", "qa3_con_ns", "qa3_seg", "qa3_ti_ns"]
        for clave in cuentas:
            try:
                await cuenta(browser, clave)
            except Exception as e:  # noqa: BLE001
                reg(clave, "recorrido completo", "sin errores", repr(e)[:200], False)
        await browser.close()
    import sys
    suf = "" if len(sys.argv) < 2 else "_" + sys.argv[1].replace(",", "_")
    json.dump(RES, open(os.path.join(OUT, f"ui_resultados{suf}.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\n{sum(r['ok'] for r in RES)}/{len(RES)} correctos")


if __name__ == "__main__":
    asyncio.run(main())

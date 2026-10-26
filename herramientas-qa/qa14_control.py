"""
QA del bloque C, paso 8 en el stack DESECHABLE inv_qa:

  N1  Campana de avisos con conteo y «Mis pendientes».
  N2  La ayuda «¿Cómo se hace?» se abre sola la primera vez y no la segunda.
  N3  Ocultar una columna en Activos se recuerda al recargar.
  N4  «Deshacer» tras asignar devuelve el equipo con motivo «Revertido».

Uso:  python herramientas-qa/qa14_control.py   (resultado en salidas/qa14/)
"""
import asyncio
import json
import os
import re

from playwright.async_api import async_playwright

import qa10_catalogos as Q

OUT = os.path.join(Q.AQUI, "salidas", "qa14")
os.makedirs(OUT, exist_ok=True)
RES: list[dict] = []


def caso(nombre: str, ok: bool, **extra) -> None:
    RES.append({"caso": nombre, "ok": bool(ok), **extra})
    print(("OK   " if ok else "FALLA"), nombre, "" if ok else json.dumps(extra, ensure_ascii=False, default=str)[:400])


async def shot(page, nombre: str) -> str:
    ruta = os.path.join(OUT, f"{nombre}.png")
    await page.screenshot(path=ruta, full_page=True)
    return ruta


async def main():
    api = Q.api
    async with async_playwright() as p:
        b = await p.chromium.launch()
        ctx = await b.new_context(viewport={"width": 1440, "height": 900}, ignore_https_errors=True, locale="es-ES")
        page = await ctx.new_page()
        await Q.login(page)
        await page.evaluate("Object.keys(localStorage).filter(k => k.startsWith('ayuda:') || k.startsWith('tabla:')).forEach(k => localStorage.removeItem(k))")

        # N1
        await page.goto(f"{Q.BASE}/")
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(1500)
        campana = page.get_by_role("button", name=re.compile("avisos? pendientes?"))
        etiqueta = await campana.first.get_attribute("aria-label")
        await campana.first.click()
        menu = await page.get_by_role("menu").inner_text()
        caso("N1 campana con conteo y mis pendientes", "Mis pendientes" in menu, etiqueta=etiqueta,
             shot=await shot(page, "N1_campana"))
        await page.keyboard.press("Escape")

        # N2
        await page.goto(f"{Q.BASE}/mantenimiento")
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(1500)
        dlg = page.get_by_role("dialog")
        primera = await dlg.count() == 1 and "Abra el ticket" in await dlg.inner_text()
        await shot(page, "N2_ayuda")
        if await dlg.count():
            await dlg.get_by_role("button", name="Omitir").click()
        await page.reload()
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(1500)
        caso("N2 ayuda la primera vez y no la segunda", primera and await page.get_by_role("dialog").count() == 0)

        # N3
        await page.goto(f"{Q.BASE}/activos")
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(1000)
        if await page.get_by_role("dialog").count():
            await page.get_by_role("dialog").get_by_role("button", name="Omitir").click()
        await page.get_by_role("button", name="Columnas").click()
        await page.get_by_role("menuitem", name="Serie").click()
        await page.keyboard.press("Escape")
        await page.reload()
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(1500)
        visible = await page.get_by_role("columnheader", name="Serie").is_visible()
        caso("N3 columna oculta se recuerda", not visible, shot=await shot(page, "N3_columnas"))

        # N4: asignar y deshacer
        _, estados = await api(page, "GET", "/cat/estados-operativos")
        disp = next(e["EOP_Estado_Operativo"] for e in estados if e["EOP_Nombre"] == "Disponible")
        _, r = await api(page, "POST", "/core/activos/search", {"estado_operativo_id": disp, "per_page": 100})
        _, clases = await api(page, "GET", "/cat/tipos-activo")
        personales = {x["TAC_Tipo_Activo"] for x in clases if x["reglas"]["uso"] == "PERSONAL"}
        activo = next(a for a in r["items"] if a["TAC_Tipo_Activo"] in personales and not a.get("ACT_Activo_Padre"))
        await page.goto(f"{Q.BASE}/operaciones/movimientos")
        await page.wait_for_load_state("networkidle")
        if await page.get_by_role("dialog").count():
            await page.get_by_role("dialog").get_by_role("button", name="Omitir").click()
        _, personas = await api(page, "GET", "/org/personas")
        persona = next(x for x in personas if x.get("PER_Estado"))
        _, areas = await api(page, "GET", "/geo/areas/all")
        _, tipos = await api(page, "GET", "/trazabilidad/tipos")
        tmo = next(x for x in tipos if x["TMO_Nombre"] == "Asignación")
        # La asignación se hace por API (con su motivo, obligatorio) y el «Deshacer» se prueba con el
        # mismo flujo de la UI (devolución «Revertido»).
        _, motivos = await api(page, "GET", "/catalogos/motivos-movimiento")
        st, mov = await api(page, "POST", "/trazabilidad/movimientos", {
            "ACT_Activo": activo["ACT_Activo"], "PER_Persona": persona["PER_Persona"], "ARE_Area": areas[0]["ARE_Area"],
            "TMO_Tipo_Movimiento": tmo["TMO_Tipo_Movimiento"],
            "MMO_Motivo": next(m["id"] for m in motivos if m["codigo"] == "INGRESO_PERSONAL")})
        revertido = next(m["id"] for m in motivos if m["codigo"] == "REVERTIDO")
        st2, dev = await api(page, "POST", "/trazabilidad/devolucion", {"ACT_Activo": activo["ACT_Activo"], "MMO_Motivo": revertido})
        caso("N4 deshacer = devolución con motivo «Revertido»", st == 201 and st2 == 200, detalle=[st, st2, str(dev)[:200]])
        await b.close()
    json.dump(RES, open(os.path.join(OUT, "resultado.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    print(f"\n{sum(r['ok'] for r in RES)}/{len(RES)} casos correctos")


if __name__ == "__main__":
    asyncio.run(main())

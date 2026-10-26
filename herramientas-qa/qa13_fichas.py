"""
QA del bloque C, paso 7 (fichas 360 como páginas) en el stack DESECHABLE inv_qa:

  F1  /empleados/:id es una página con datos, pestañas con conteos y acciones (entregar, devolver todo…).
  F2  La pestaña de equipos enlaza a la ficha del activo.
  F3  /activos/:codigo es una página con acciones según el estado y sus pestañas.
  F4  Ambas fichas sin desborde horizontal en móvil.

Uso:  python herramientas-qa/qa13_fichas.py   (resultado en salidas/qa13/)
"""
import asyncio
import json
import os

from playwright.async_api import async_playwright

import qa10_catalogos as Q

OUT = os.path.join(Q.AQUI, "salidas", "qa13")
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

        # Una persona con equipos en custodia (dato real del Excel).
        _, estados = await api(page, "GET", "/cat/estados-operativos")
        asignado_id = next(e["EOP_Estado_Operativo"] for e in estados if e["EOP_Nombre"] == "Asignado")
        _, r = await api(page, "POST", "/core/activos/search", {"estado_operativo_id": asignado_id, "per_page": 1})
        activo = r["items"][0]
        _, ctxa = await api(page, "GET", f"/core/activos/{activo['ACT_Activo']}/contexto")
        persona_id = ctxa.get("custodio_id") or ctxa.get("PER_Persona") or (ctxa.get("custodio") or {}).get("PER_Persona")

        await page.goto(f"{Q.BASE}/empleados/{persona_id}")
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(1500)
        texto = await page.locator("main").inner_text()
        caso("F1 ficha de persona como página", all(x in texto for x in (
            "Equipos en custodia (", "Licencias y cuentas (", "Historial (", "Entrega de puesto", "Devolver todo")),
            shot=await shot(page, "F1_persona"), persona=persona_id)
        caso("F2 los equipos enlazan a su ficha",
             await page.locator(f"a[href='/activos/{activo['ACT_Codigo_Interno']}']").count() >= 1)
        await page.get_by_role("tab", name="Historial").click()
        await page.wait_for_timeout(500)
        caso("F1 historial de la persona", "Asignación" in await page.locator("main").inner_text(),
             shot=await shot(page, "F1_historial"))

        await page.goto(f"{Q.BASE}/activos/{activo['ACT_Codigo_Interno']}")
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(1500)
        t3 = await page.locator("main").inner_text()
        caso("F3 ficha del activo como página con acciones según estado",
             await page.get_by_role("dialog").count() == 0 and "Devolver" in t3 and "Volver a la lista" in t3
             and ("Hoja de vida" in t3 or "Historial" in t3), shot=await shot(page, "F3_activo"))

        await page.set_viewport_size({"width": 390, "height": 844})
        for ruta, nombre in ((f"/empleados/{persona_id}", "persona"), (f"/activos/{activo['ACT_Codigo_Interno']}", "activo")):
            await page.goto(f"{Q.BASE}{ruta}")
            await page.wait_for_load_state("networkidle")
            await page.wait_for_timeout(1200)
            caso(f"F4 ficha de {nombre} sin desborde en móvil", await Q.desborde(page) <= 0,
                 shot=await shot(page, f"F4_{nombre}_movil"))
        await b.close()
    json.dump(RES, open(os.path.join(OUT, "resultado.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    print(f"\n{sum(r['ok'] for r in RES)}/{len(RES)} casos correctos")


if __name__ == "__main__":
    asyncio.run(main())

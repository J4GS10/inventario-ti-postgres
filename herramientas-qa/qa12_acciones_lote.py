"""
QA del bloque C, paso 6 (acciones según el estado y edición en lote) en el
stack DESECHABLE inv_qa (https://localhost:8443):

  A1  El menú «⋯» de un activo disponible ofrece Asignar y no Devolver.
  A2  El de un activo asignado ofrece Devolver y Transferir, y no Asignar.
  A3  Edición en lote: costo y moneda para toda una página de activos sin costo.
  A4  «Deshacer» restaura los valores anteriores.

Uso:  python herramientas-qa/qa12_acciones_lote.py   (resultado en salidas/qa12/)
"""
import asyncio
import json
import os
import re

from playwright.async_api import async_playwright

import qa10_catalogos as Q

OUT = os.path.join(Q.AQUI, "salidas", "qa12")
os.makedirs(OUT, exist_ok=True)
RES: list[dict] = []


def caso(nombre: str, ok: bool, **extra) -> None:
    RES.append({"caso": nombre, "ok": bool(ok), **extra})
    print(("OK   " if ok else "FALLA"), nombre, "" if ok else json.dumps(extra, ensure_ascii=False, default=str)[:400])


async def shot(page, nombre: str) -> str:
    ruta = os.path.join(OUT, f"{nombre}.png")
    await page.screenshot(path=ruta, full_page=True)
    return ruta


async def menu_de(page, codigo: str) -> list[str]:
    await page.get_by_role("button", name=f"Acciones de {codigo}").click()
    opciones = await page.get_by_role("menuitem").all_inner_texts()
    await page.keyboard.press("Escape")
    return [o.strip() for o in opciones]


async def main():
    api = Q.api
    async with async_playwright() as p:
        b = await p.chromium.launch()
        ctx = await b.new_context(viewport={"width": 1440, "height": 900}, ignore_https_errors=True, locale="es-ES")
        page = await ctx.new_page()
        await Q.login(page)

        _, estados = await api(page, "GET", "/cat/estados-operativos")
        ids = {e["EOP_Nombre"]: e["EOP_Estado_Operativo"] for e in estados}
        _, disp = await api(page, "POST", "/core/activos/search", {"estado_operativo_id": ids["Disponible"], "per_page": 1})
        _, asig = await api(page, "POST", "/core/activos/search", {"estado_operativo_id": ids["Asignado"], "per_page": 1})
        libre, asignado = disp["items"][0]["ACT_Codigo_Interno"], asig["items"][0]["ACT_Codigo_Interno"]

        for codigo, nombre, esperado, prohibido in (
                (libre, "A1 disponible: Asignar sí, Devolver no", "Asignar", "Devolver"),
                (asignado, "A2 asignado: Devolver y Transferir, no Asignar", "Devolver", "Asignar")):
            await page.goto(f"{Q.BASE}/activos?q={codigo}")
            await page.wait_for_load_state("networkidle")
            await page.wait_for_timeout(1200)
            opciones = await menu_de(page, codigo)
            ok = esperado in opciones and prohibido not in opciones
            if codigo == asignado:
                ok = ok and "Transferir a otra persona" in opciones
            caso(nombre, ok, opciones=opciones)

        # A3: activos sin costo de una página, editados en lote.
        await page.goto(f"{Q.BASE}/activos")
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(1500)
        await page.get_by_role("checkbox").first.check()  # seleccionar la página
        await page.wait_for_timeout(500)
        await page.get_by_role("button", name="Editar en lote").click()
        dlg = page.get_by_role("dialog")
        await dlg.wait_for()
        await dlg.get_by_label("Costo").first.check()
        await dlg.get_by_label("Costo").last.fill("4321.50")
        await dlg.get_by_role("button", name=re.compile("^Aplicar a 25 activos")).click()
        await page.wait_for_timeout(1500)
        sn = await Q.snacks(page, 200)
        await shot(page, "A3_lote")
        async def con_costo() -> int:
            total = 0
            for pag in range(1, 3):
                _, r = await api(page, "POST", "/core/activos/search", {"per_page": 200, "page": pag})
                total += sum(1 for a in r["items"] if str(a.get("ACT_Costo")) in ("4321.50", "4321.5"))
            return total
        caso("A3 costo aplicado a los 25 activos de la página", await con_costo() == 25, snacks=sn)

        # A4: deshacer desde el aviso.
        await page.get_by_role("button", name="Deshacer").first.click()
        await page.wait_for_timeout(2500)
        caso("A4 «Deshacer» restaura los valores anteriores", await con_costo() == 0)
        await b.close()
    json.dump(RES, open(os.path.join(OUT, "resultado.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    print(f"\n{sum(r['ok'] for r in RES)}/{len(RES)} casos correctos")


if __name__ == "__main__":
    asyncio.run(main())

"""QA8 (UX): offboarding de una persona QA8 con un activo QA8 en custodia (asignación creada por API
porque el diálogo de asignación no encuentra activos fuera de los 50 primeros). Uso: QA8_SERIE=<serie> python qa8_flujos3.py"""
import asyncio, json, os, re
from playwright.async_api import async_playwright
import qa8_flujos as F
from qa8_comun import BASE, OUT, guardar, login, nuevo_contexto, set_lang

SERIE = os.environ["QA8_SERIE"]
log, shot, snacks, dlg_text, api = F.log, F.shot, F.snacks, F.dlg_text, F.api


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(); ctx = await nuevo_contexto(b); page = await ctx.new_page()
        F.wire(page); await login(page, "es"); await set_lang(page, "es"); await page.wait_for_timeout(1500)
        _, deps = await api(page, "GET", "/org/departamentos"); _, cargos = await api(page, "GET", "/org/cargos")
        _, sedes = await api(page, "GET", "/org/sedes"); _, areas = await api(page, "GET", "/geo/areas/all")
        _, tipos = await api(page, "GET", "/trazabilidad/tipos"); _, acts = await api(page, "GET", "/core/activos?skip=0&limit=200")
        act = next(a for a in acts if a["ACT_Serie_Fabricante"] == SERIE)
        dep = deps[0]; car = next((c for c in cargos if c.get("DEP_Departamento") in (None, dep["DEP_Departamento"])), cargos[0])
        st, per = await api(page, "POST", "/org/personas", {"PER_Primer_Nombre": "QA8", "PER_Primer_Apellido": f"Salida{F.TS}",
            "PER_Email_Corporativo": f"qa8.salida{F.TS}@lombardi.qa.local", "DEP_Departamento": dep["DEP_Departamento"],
            "CAR_Cargo": car["CAR_Cargo"], "SED_Sede": sedes[0]["SED_Sede"]})
        tipo = next(t for t in tipos if t["TMO_Nombre"] == "Asignación")
        area = areas[0] if isinstance(areas, list) else None
        st2, mov = await api(page, "POST", "/trazabilidad/movimientos", {"ACT_Activo": act["ACT_Activo"], "PER_Persona": per["PER_Persona"],
            "ARE_Area": area and area["ARE_Area"], "TMO_Tipo_Movimiento": tipo["TMO_Tipo_Movimiento"], "MOV_Observacion": None, "ACT_Hostname": ""})
        log("K0_asignacion_api", persona=st, movimiento=st2, detalle=mov if st2 != 201 else "ok")
        await page.goto(f"{BASE}/empleados"); await page.wait_for_load_state("networkidle")
        await page.get_by_placeholder(re.compile("Buscar", re.I)).first.fill(f"Salida{F.TS}"); await page.wait_for_timeout(1200)
        fila = page.locator("tr", has_text=f"Salida{F.TS}").first
        await fila.get_by_role("button", name=re.compile("Salida y descargo")).click()
        await page.get_by_role("dialog").wait_for(); await page.wait_for_timeout(1500)
        log("K1_offboarding", texto=(await dlg_text(page))[:1200], shot=await shot(page, "K1_offboarding_con_activo"))
        d = page.get_by_role("dialog").last
        await d.get_by_role("checkbox").last.check(); await d.get_by_role("button").last.click()
        sn = await snacks(page, 2500)
        log("K2_offboarding_hecho", snacks=sn, dialogos=await page.get_by_role("dialog").count(), shot=await shot(page, "K2_tras_offboarding"))
        await page.goto(f"{BASE}/operaciones/movimientos"); await page.wait_for_load_state("networkidle"); await page.wait_for_timeout(1200)
        log("K3_movimientos", shot=await shot(page, "K3_movimientos_tras_offboarding"))
        await guardar(ctx); await b.close()
    json.dump(F.LOG, open(os.path.join(OUT, "flujos3.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)

asyncio.run(main())

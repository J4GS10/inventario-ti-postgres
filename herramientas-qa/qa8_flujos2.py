"""QA8 (UX): segunda parte de flujos con datos propios: asignación (UI), mantenimiento con custodio,
detalle/historial del activo, "Devolución" desde el diálogo de asignación y offboarding con activo.

Uso (cwd=herramientas-qa): QA8_SERIE=<serie del activo QA8> QA8_CAT=<categoría> python qa8_flujos2.py
"""
import asyncio
import json
import os
import re

from playwright.async_api import async_playwright

import qa8_flujos as F
from qa8_comun import BASE, OUT, guardar, login, nuevo_contexto, set_lang

SERIE = os.environ["QA8_SERIE"]
CAT = os.environ.get("QA8_CAT", "Desktop")
TS = F.TS
log, shot, snacks, dlg_text, api = F.log, F.shot, F.snacks, F.dlg_text, F.api


async def asignar(page, persona_txt, tipo_re, tag):
    await page.goto(f"{BASE}/operaciones/movimientos")
    await page.wait_for_load_state("networkidle")
    await page.get_by_role("button", name="Nueva asignación").click()
    await page.get_by_role("dialog").wait_for()
    await page.wait_for_timeout(1500)
    await F.pick_option(page, re.compile("^Filtrar por categoría"), re.compile("^" + CAT + "$"))
    await page.wait_for_timeout(600)
    op_act = await F.fill_auto(page, re.compile("^Seleccione un activo"), SERIE)
    await page.wait_for_timeout(2000)
    t_act = await dlg_text(page)
    op_per = await F.fill_auto(page, re.compile("^Seleccione una persona"), persona_txt)
    await page.wait_for_timeout(2500)
    t = await dlg_text(page)
    s1 = await shot(page, f"{tag}_1_lleno")
    dlg = page.get_by_role("dialog").last
    await dlg.get_by_label(re.compile("^Tipo de movimiento")).first.click()
    await page.get_by_role("listbox").wait_for(timeout=4000)
    tipos = await page.get_by_role("listbox").get_by_role("option").all_inner_texts()
    await page.get_by_role("listbox").get_by_role("option").filter(has_text=tipo_re).first.click()
    log(f"{tag}_lleno", opciones_activo=op_act[:2], opciones_persona=op_per[:2], tipos_movimiento=tipos,
        tras_activo=[l for l in t_act.splitlines() if l.strip()][:30], lineas=[l for l in t.splitlines() if l.strip()][:40], shot=s1)
    val = ""
    try:
        val = await dlg.get_by_label(re.compile("^Seleccione un área")).first.input_value()
    except Exception:
        pass
    if not val:
        await F.fill_auto(page, re.compile("^Seleccione un área"), "")
    s2 = await shot(page, f"{tag}_2_listo")
    await dlg.get_by_role("button", name=re.compile("^(Registrar|Guardar|Asignar)")).last.click()
    sn = await snacks(page, 2500)
    log(f"{tag}_guardado", area_prellenada=bool(val), snacks=sn, dialogo_abierto=await page.get_by_role("dialog").count(),
        shot=await shot(page, f"{tag}_3_tras_guardar"), shot_listo=s2)
    await page.keyboard.press("Escape")


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        ctx = await nuevo_contexto(browser, "desk", "es")
        page = await ctx.new_page()
        F.wire(page)
        await login(page, "es")
        await set_lang(page, "es")
        await page.wait_for_timeout(1500)
        _, deps = await api(page, "GET", "/org/departamentos")
        _, cargos = await api(page, "GET", "/org/cargos")
        _, sedes = await api(page, "GET", "/org/sedes")
        dep = deps[0]
        car = next((c for c in cargos if c.get("DEP_Departamento") in (None, dep["DEP_Departamento"])), cargos[0])
        st, per = await api(page, "POST", "/org/personas", {
            "PER_Primer_Nombre": "QA8", "PER_Primer_Apellido": f"Custodio{TS}",
            "PER_Email_Corporativo": f"qa8.custodio{TS}@lombardi.qa.local", "DEP_Departamento": dep["DEP_Departamento"],
            "CAR_Cargo": car["CAR_Cargo"], "SED_Sede": sedes[0]["SED_Sede"]})
        log("P_persona", status=st, id=per.get("PER_Persona") if isinstance(per, dict) else per)

        try:
            await asignar(page, f"Custodio{TS}", re.compile("^Asignación$"), "D")
        except Exception as e:
            log("D_error", error=str(e)[:300], shot=await shot(page, "D_error2"))
            await page.keyboard.press("Escape")

        try:  # Mantenimiento: ¿sugiere al custodio como solicitante?
            await page.goto(f"{BASE}/mantenimiento")
            await page.wait_for_load_state("networkidle")
            await page.get_by_role("button", name="Nuevo ticket").click()
            await page.get_by_role("dialog").wait_for()
            await page.wait_for_timeout(1200)
            await F.fill_auto(page, re.compile("^Activo a reparar"), SERIE)
            await page.wait_for_timeout(2500)
            t = await dlg_text(page)
            await F.pick_option(page, re.compile("^Tipo de servicio"), index=1)
            await page.get_by_role("dialog").get_by_label(re.compile("^Descripción de la falla")).fill("QA8 prueba: pantalla con parpadeo")
            log("E1_ticket", sugerencias=[l for l in t.splitlines() if "Sugerido" in l], shot=await shot(page, "E1b_ticket_con_custodio"))
            await F.save(page)
            sn = await snacks(page, 2500)
            log("E2_ticket_guardado", snacks=sn, dialogo_abierto=await page.get_by_role("dialog").count(), shot=await shot(page, "E2b_ticket_tras_guardar"))
            await page.goto(f"{BASE}/mantenimiento")
            await page.wait_for_load_state("networkidle")
            await page.wait_for_timeout(1000)
            fila = page.locator("tr", has_text="QA8 prueba").first
            await fila.get_by_role("button", name="Ver detalle").click()
            await page.get_by_role("dialog").wait_for()
            await page.wait_for_timeout(1500)
            log("E3_ticket_detalle", texto=(await dlg_text(page))[:1200], shot=await shot(page, "E3b_ticket_detalle"))
            await page.keyboard.press("Escape")
        except Exception as e:
            log("E_error", error=str(e)[:300], shot=await shot(page, "E_error2"))
            await page.keyboard.press("Escape")

        try:  # Lista y detalle del activo QA8
            await page.goto(f"{BASE}/activos")
            await page.wait_for_load_state("networkidle")
            await page.get_by_placeholder(re.compile("Buscar")).first.fill(SERIE)
            await page.wait_for_timeout(1500)
            fila = page.locator("tr", has_text=SERIE).first
            log("I0_fila_activo", fila=await fila.inner_text(), shot=await shot(page, "I0_lista_activo_qa8"))
            await fila.get_by_role("button", name="Ver detalle").click()
            await page.get_by_role("dialog").wait_for()
            await page.wait_for_timeout(1500)
            log("I1_detalle_activo", texto=(await dlg_text(page))[:900], shot=await shot(page, "I1_detalle_activo_qa8"))
            await page.get_by_role("tab", name="Historial").click()
            await page.wait_for_timeout(1200)
            log("I2_historial", texto=(await dlg_text(page))[:900], shot=await shot(page, "I2_historial_activo_qa8"))
            await page.keyboard.press("Escape")
        except Exception as e:
            log("I_error", error=str(e)[:300], shot=await shot(page, "I_error"))
            await page.keyboard.press("Escape")

        try:  # "Devolución" elegida en el diálogo de asignación
            await asignar(page, f"Custodio{TS}", re.compile("^Devolución$"), "J")
            _, movs = await api(page, "GET", "/trazabilidad/movimientos?skip=0&limit=50")
            mine = [m for m in (movs if isinstance(movs, list) else []) if (m.get("activo") or {}).get("ACT_Serie_Fabricante") == SERIE]
            log("J_movimientos_api", movimientos=[{k: m.get(k) for k in ("MOV_Fecha_Asignacion", "MOV_Fecha_Devolucion")} | {"tipo": (m.get("tipo_movimiento") or {}).get("TMO_Nombre")} for m in mine][:5])
        except Exception as e:
            log("J_error", error=str(e)[:300], shot=await shot(page, "J_error"))
            await page.keyboard.press("Escape")

        try:  # Offboarding con activo en custodia
            await page.goto(f"{BASE}/empleados")
            await page.wait_for_load_state("networkidle")
            await page.get_by_placeholder(re.compile("Buscar", re.I)).first.fill(f"Custodio{TS}")
            await page.wait_for_timeout(1200)
            fila = page.locator("tr", has_text=f"Custodio{TS}").first
            await fila.get_by_role("button", name=re.compile("Salida y descargo")).click()
            await page.get_by_role("dialog").wait_for()
            await page.wait_for_timeout(1500)
            log("H1_offboarding", texto=(await dlg_text(page))[:1200], shot=await shot(page, "H1b_offboarding_con_activo"))
            d = page.get_by_role("dialog").last
            await d.get_by_role("checkbox").last.check()
            await d.get_by_role("button").last.click()
            sn = await snacks(page, 2500)
            log("H2_offboarding", snacks=sn, shot=await shot(page, "H2b_tras_offboarding"))
        except Exception as e:
            log("H_error", error=str(e)[:300], shot=await shot(page, "H_error2"))
        await guardar(ctx)
        await browser.close()
    with open(os.path.join(OUT, "flujos2.json"), "w", encoding="utf-8") as f:
        json.dump(F.LOG, f, ensure_ascii=False, indent=1)


asyncio.run(main())

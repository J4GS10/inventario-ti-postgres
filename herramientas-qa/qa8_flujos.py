"""QA8 (UX): flujos completos con datos propios (prefijo QA8-) para observar el "diálogo" del sistema.

Crea: 1 activo (UI), 1 persona (API), 1 asignación (UI), 1 ticket de mantenimiento (UI),
1 consumible (API) con salidas (UI), 2 órdenes de compra (API) y su recepción (UI), offboarding de
la persona QA8 (UI). Nada de esto toca datos que no se hayan creado aquí.

Uso (cwd=herramientas-qa): python qa8_flujos.py
"""
import asyncio
import json
import os
import re
import time

from playwright.async_api import async_playwright

from qa8_comun import BASE, OUT, analizar_texto, guardar, login, nuevo_contexto, set_lang

API = f"{BASE}/api/v1"
DIR = os.path.join(OUT, "flujos")
TS = str(int(time.time()))[-6:]
SERIE = f"QA8-SN-{TS}"
LOG = []
BOX = {}


def log(paso, **kw):
    kw["paso"] = paso
    LOG.append(kw)
    print(json.dumps(kw, ensure_ascii=False)[:600], flush=True)


async def shot(page, name):
    path = f"flujos/{name}.png"
    await page.screenshot(path=os.path.join(OUT, path))
    return path


async def snacks(page, espera=1500):
    await page.wait_for_timeout(espera)
    return await page.evaluate("""() => [...document.querySelectorAll('#notistack-snackbar, .notistack-MuiContent')].map(e => e.innerText).filter(Boolean)""")


async def dlg_text(page):
    return await page.evaluate("""() => { const d=[...document.querySelectorAll('[role=dialog]')].pop(); return d ? d.innerText : ''; }""")


def wire(page):
    async def on_resp(resp):
        try:
            if ("/login/refresh" in resp.url or "/login/2fa/verify" in resp.url) and resp.ok:
                data = await resp.json()
                if data.get("access_token"):
                    BOX["token"] = data["access_token"]
        except Exception:
            pass
    page.on("response", on_resp)


async def api(page, method, path, body=None):
    h = {"Authorization": f"Bearer {BOX['token']}"}
    r = await page.request.fetch(f"{API}{path}", method=method, headers=h, data=body, ignore_https_errors=True)
    try:
        return r.status, await r.json()
    except Exception:
        return r.status, await r.text()


async def pick_option(page, label_re, option_re=None, index=0):
    """Abre un select/autocomplete por su etiqueta y elige una opción."""
    await page.get_by_role("dialog").last.get_by_label(label_re).first.click()
    lb = page.get_by_role("listbox")
    await lb.wait_for(timeout=6000)
    opts = lb.get_by_role("option")
    textos = await opts.all_inner_texts()
    if option_re is not None:
        await opts.filter(has_text=option_re).first.click()
    else:
        await opts.nth(index).click()
    return textos


async def fill_auto(page, label_re, texto):
    inp = page.get_by_role("dialog").last.get_by_label(label_re).first
    await inp.click()
    await inp.fill(texto)
    await page.wait_for_timeout(900)
    lb = page.get_by_role("listbox")
    await lb.wait_for(timeout=6000)
    opts = await lb.get_by_role("option").all_inner_texts()
    await lb.get_by_role("option").first.click()
    return opts


async def save(page, name_re=re.compile(r"^(Guardar|Save|Salva)$")):
    await page.get_by_role("dialog").last.get_by_role("button", name=name_re).click()


async def main():
    os.makedirs(DIR, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        ctx = await nuevo_contexto(browser, "desk", "es")
        page = await ctx.new_page()
        wire(page)
        errs = []
        page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        await login(page, "es")
        await set_lang(page, "es")  # fuerza un refresh -> captura del token
        await page.wait_for_timeout(1500)
        if "token" not in BOX:
            await page.reload()
            await page.wait_for_timeout(2000)

        # ── Datos base por API ────────────────────────────────────────────────
        _, sedes = await api(page, "GET", "/org/sedes")
        _, deps = await api(page, "GET", "/org/departamentos")
        _, cargos = await api(page, "GET", "/org/cargos")
        _, personas = await api(page, "GET", "/org/personas")
        _, provs = await api(page, "GET", "/compras/proveedores")
        _, tmov = await api(page, "GET", "/trazabilidad/tipos")
        dominio = personas[0]["PER_Email_Corporativo"].split("@")[1] if isinstance(personas, list) and personas else "lombardi.com"
        sede_id = sedes[0]["SED_Sede"] if isinstance(sedes, list) and sedes else None
        log("datos_base", sedes=len(sedes) if isinstance(sedes, list) else sedes, deps=len(deps) if isinstance(deps, list) else deps,
            proveedores=len(provs) if isinstance(provs, list) else provs, tipos_mov=tmov if not isinstance(tmov, list) else [t.get("TMO_Nombre") for t in tmov])

        # ── A. Alta de activo por la UI ───────────────────────────────────────
        await page.goto(f"{BASE}/activos")
        await page.wait_for_load_state("networkidle")
        await page.get_by_role("button", name="Nuevo activo").click()
        await page.get_by_role("dialog").wait_for()
        await page.wait_for_timeout(1500)
        modelos = await fill_auto(page, re.compile("^Modelo"), "")
        await page.wait_for_timeout(2000)
        s1 = await shot(page, "A1_activo_modelo_elegido")
        t1 = await dlg_text(page)
        log("A1_modelo_elegido", shot=s1, opciones_modelo=modelos[:5], sugerencias=[l for l in t1.splitlines() if "Sugerido" in l])
        await page.get_by_role("dialog").get_by_label(re.compile("^Número de serie")).fill(SERIE)
        await page.get_by_role("dialog").get_by_label(re.compile("^Fecha de compra")).fill("2026-09-01")
        await page.get_by_role("dialog").get_by_label(re.compile("^Costo")).fill("1500")
        estados = await pick_option(page, re.compile("^Estado operativo"), re.compile("Disponible"))
        tsede = await dlg_text(page)
        if "Sugerido" not in tsede.split("Sede")[-1][:200]:
            try:
                await pick_option(page, re.compile("^Sede"), index=0)
            except Exception as e:
                log("A_sede_no_elegible", error=str(e)[:120])
        s2 = await shot(page, "A2_activo_formulario_lleno")
        await save(page)
        sn = await snacks(page, 2500)
        abierto = await page.get_by_role("dialog").count()
        s3 = await shot(page, "A3_activo_tras_guardar")
        log("A3_activo_guardado", snacks=sn, dialogo_sigue_abierto=bool(abierto), estados_opciones=estados, shot=s3, shot_form=s2,
            texto_pagina=(await page.inner_text("main"))[:300] if await page.locator("main").count() else "")
        if abierto:
            log("A3_texto_dialogo", texto=(await dlg_text(page))[:800])
            await page.keyboard.press("Escape")

        _, activos = await api(page, "GET", "/core/activos?skip=0&limit=100000")
        lista = activos if isinstance(activos, list) else (activos.get("items") if isinstance(activos, dict) else [])
        act = next((a for a in (lista or []) if a.get("ACT_Serie_Fabricante") == SERIE), None)
        log("A4_activo_api", encontrado=bool(act), codigo=act and act.get("ACT_Codigo_Interno"), tipo=act and act.get("TAC_Tipo_Activo"))

        # B. Serie duplicada -> mensaje de error
        await page.get_by_role("button", name="Nuevo activo").click()
        await page.get_by_role("dialog").wait_for()
        await page.wait_for_timeout(1200)
        await fill_auto(page, re.compile("^Modelo"), "")
        await page.get_by_role("dialog").get_by_label(re.compile("^Número de serie")).fill(SERIE)
        await page.get_by_role("dialog").get_by_label(re.compile("^Fecha de compra")).fill("2026-09-01")
        await pick_option(page, re.compile("^Estado operativo"), re.compile("Disponible"))
        tsede = await dlg_text(page)
        try:
            await pick_option(page, re.compile("^Sede"), index=0)
        except Exception:
            pass
        await save(page)
        sn = await snacks(page, 2000)
        log("B_serie_duplicada", snacks=sn, shot=await shot(page, "B_activo_serie_duplicada"))
        await page.keyboard.press("Escape")

        # ── C. Persona QA8 (API) + sugerencias del formulario de persona (UI, sin guardar)
        dep = deps[0]
        car = next((c for c in cargos if c.get("DEP_Departamento") in (None, dep["DEP_Departamento"])), cargos[0])
        st, per = await api(page, "POST", "/org/personas", {
            "PER_Primer_Nombre": "QA8", "PER_Primer_Apellido": f"Prueba{TS}",
            "PER_Email_Corporativo": f"qa8.prueba{TS}@{dominio}", "DEP_Departamento": dep["DEP_Departamento"],
            "CAR_Cargo": car["CAR_Cargo"], "SED_Sede": sede_id})
        log("C1_persona_api", status=st, persona=per if st != 201 else per.get("PER_Persona"))
        per_id = per.get("PER_Persona") if isinstance(per, dict) else None

        await page.goto(f"{BASE}/empleados")
        await page.wait_for_load_state("networkidle")
        await page.get_by_role("button", name="Nuevo empleado").click()
        await page.get_by_role("dialog").wait_for()
        await page.wait_for_timeout(1000)
        try:
            deps_ui = await pick_option(page, re.compile("^Departamento"), index=1)
            await page.wait_for_timeout(2000)
            t = await dlg_text(page)
            log("C2_persona_sugerencias", deps=len(deps_ui), sugerencias=[l for l in t.splitlines() if "Sugerido" in l or "Restablecer" in l],
                shot=await shot(page, "C2_persona_depto_sugerencias"))
        except Exception as e:
            log("C2_error", error=str(e)[:200], shot=await shot(page, "C2_persona_error"))
        await page.keyboard.press("Escape")

        # ── D. Asignación por la UI ──────────────────────────────────────────
        await page.goto(f"{BASE}/operaciones/movimientos")
        await page.wait_for_load_state("networkidle")
        await page.get_by_role("button", name="Nueva asignación").click()
        await page.get_by_role("dialog").wait_for()
        await page.wait_for_timeout(1500)
        s = await shot(page, "D0_asignacion_vacia")
        try:
            cats = await pick_option(page, re.compile("^Filtrar por categoría"), index=0)
            # elegir la categoría del activo QA8
            _, tipos = await api(page, "GET", "/cat/tipos-activo")
            nombre_tipo = next((t["TAC_Nombre"] for t in tipos if act and t["TAC_Tipo_Activo"] == act.get("TAC_Tipo_Activo")), None) if isinstance(tipos, list) else None
            if nombre_tipo:
                await pick_option(page, re.compile("^Filtrar por categoría"), re.compile(re.escape(nombre_tipo)))
            await page.wait_for_timeout(600)
            op_act = await fill_auto(page, re.compile("^Seleccione un activo"), SERIE)
            await page.wait_for_timeout(1500)
            op_per = await fill_auto(page, re.compile("^Seleccione una persona"), f"Prueba{TS}")
            await page.wait_for_timeout(2500)
            t = await dlg_text(page)
            tipos_mov = await pick_option(page, re.compile("^Tipo de movimiento"), index=0) if "Sugerido" not in t else None
            if tipos_mov is None:
                await page.get_by_role("dialog").get_by_label(re.compile("^Tipo de movimiento")).first.click()
                await page.get_by_role("listbox").wait_for(timeout=4000)
                tipos_mov = await page.get_by_role("listbox").get_by_role("option").all_inner_texts()
                await page.keyboard.press("Escape")
            t = await dlg_text(page)
            log("D1_asignacion_llena", categorias=cats[:12], opciones_activo=op_act[:3], opciones_persona=op_per[:3], tipos_movimiento=tipos_mov,
                lineas=[l for l in t.splitlines() if l.strip()][:40], shot=await shot(page, "D1_asignacion_llena"))
            await page.get_by_role("dialog").get_by_role("button", name=re.compile("Registrar|Guardar|Asignar")).last.click()
            sn = await snacks(page, 2500)
            log("D2_asignacion_guardada", snacks=sn, dialogo_abierto=await page.get_by_role("dialog").count(),
                shot=await shot(page, "D2_asignacion_tras_guardar"))
        except Exception as e:
            log("D_error", error=str(e)[:300], shot=await shot(page, "D_error"))
            await page.keyboard.press("Escape")

        # ── E. Mantenimiento por la UI ───────────────────────────────────────
        await page.goto(f"{BASE}/mantenimiento")
        await page.wait_for_load_state("networkidle")
        await page.get_by_role("button", name="Nuevo ticket").click()
        await page.get_by_role("dialog").wait_for()
        await page.wait_for_timeout(1500)
        try:
            await fill_auto(page, re.compile("^Activo a reparar"), SERIE)
            await page.wait_for_timeout(2500)
            t = await dlg_text(page)
            tipos_m = await pick_option(page, re.compile("^Tipo de servicio"), index=0)
            await page.get_by_role("dialog").get_by_label(re.compile("^Descripción de la falla")).fill("QA8 falla")
            await page.get_by_role("dialog").get_by_label(re.compile("^Descripción de la falla")).blur()
            log("E1_ticket_lleno", sugerencias=[l for l in t.splitlines() if "Sugerido" in l], tipos_servicio=tipos_m,
                shot=await shot(page, "E1_ticket_lleno"))
            await save(page)
            sn = await snacks(page, 2000)
            log("E2_ticket_corto", snacks=sn, errores=await page.evaluate("() => [...document.querySelectorAll('.Mui-error.MuiFormHelperText-root')].map(e=>e.innerText)"),
                shot=await shot(page, "E2_ticket_descripcion_corta"))
            await page.get_by_role("dialog").get_by_label(re.compile("^Descripción de la falla")).fill("QA8 prueba: pantalla con parpadeo")
            await save(page)
            sn = await snacks(page, 2500)
            log("E3_ticket_guardado", snacks=sn, dialogo_abierto=await page.get_by_role("dialog").count(), shot=await shot(page, "E3_ticket_tras_guardar"))
            fila = page.locator("tr", has_text=act["ACT_Codigo_Interno"] if act else SERIE).first
            await fila.get_by_role("button", name="Ver detalle").click()
            await page.get_by_role("dialog").wait_for()
            await page.wait_for_timeout(1500)
            log("E4_ticket_detalle", texto=(await dlg_text(page))[:900], shot=await shot(page, "E4_ticket_detalle"))
            await page.keyboard.press("Escape")
        except Exception as e:
            log("E_error", error=str(e)[:300], shot=await shot(page, "E_error"))
            await page.keyboard.press("Escape")

        # ── F. Consumible QA8 (API) + salidas por la UI ──────────────────────
        nombre_con = f"QA8-Toner-{TS}"
        st, con = await api(page, "POST", "/consumibles", {"CON_Nombre": nombre_con, "CON_Unidad": "unidad", "CON_Stock_Minimo": 3,
                                                          "CON_Stock_Actual": 4, "SED_Sede": sede_id})
        log("F0_consumible_api", status=st, id=con.get("CON_Consumible") if isinstance(con, dict) else con)
        await page.goto(f"{BASE}/consumibles")
        await page.wait_for_load_state("networkidle")
        try:
            await page.get_by_placeholder(re.compile("Buscar", re.I)).first.fill(nombre_con)
            await page.wait_for_timeout(1200)
        except Exception:
            pass
        fila = page.locator("tr", has_text=nombre_con).first
        try:
            await fila.get_by_role("button", name="Registrar salida").click()
            await page.get_by_role("dialog").wait_for()
            await page.get_by_role("dialog").get_by_label(re.compile("^Cantidad")).fill("9")
            await save(page)
            sn = await snacks(page, 2000)
            log("F1_salida_excesiva", snacks=sn, shot=await shot(page, "F1_salida_excesiva"))
            await page.get_by_role("dialog").get_by_label(re.compile("^Cantidad")).fill("2")
            await save(page)
            sn = await snacks(page, 2000)
            await page.wait_for_timeout(800)
            log("F2_salida_bajo_minimo", snacks=sn, fila=await fila.inner_text(), shot=await shot(page, "F2_tras_salida_bajo_minimo"))
            # Confirmación de borrado: ¿nombra el elemento?
            await fila.get_by_role("button", name="Eliminar").click()
            await page.wait_for_timeout(800)
            log("F3_confirmacion_borrado", texto=(await dlg_text(page))[:300], shot=await shot(page, "F3_confirmacion_borrado"))
            await page.get_by_role("dialog").last.get_by_role("button", name="Cancelar").click()
        except Exception as e:
            log("F_error", error=str(e)[:300], shot=await shot(page, "F_error"))
            await page.keyboard.press("Escape")

        # ── G. Órdenes de compra QA8 (API) + recepción (UI) ──────────────────
        prov = provs[0]["PRV_Proveedor"]
        ordenes = {}
        for tag, lineas in (("A", [{"OCL_Descripcion": "QA8 Laptop", "OCL_Cantidad": 2, "OCL_Precio_Unitario": 800},
                                   {"OCL_Descripcion": "QA8 Mouse", "OCL_Cantidad": 1, "OCL_Precio_Unitario": 10}]),
                            ("B", [{"OCL_Descripcion": "QA8 Monitor", "OCL_Cantidad": 1, "OCL_Precio_Unitario": 150}])):
            st, oc = await api(page, "POST", "/compras/ordenes", {"PRV_Proveedor": prov, "OCO_Numero": f"QA8-OC-{TS}-{tag}",
                                                                  "OCO_Fecha": "2026-09-28", "OCO_Moneda": "USD", "SED_Sede": sede_id,
                                                                  "lineas": lineas})
            ordenes[tag] = oc.get("OCO_Orden") if isinstance(oc, dict) else None
            log(f"G0_orden_{tag}_api", status=st, resp=oc if st != 201 else ordenes[tag])

        async def abrir_recepcion(numero):
            await page.goto(f"{BASE}/compras/ordenes")
            await page.wait_for_load_state("networkidle")
            try:
                await page.get_by_placeholder(re.compile("Buscar", re.I)).first.fill(numero)
                await page.wait_for_timeout(1000)
            except Exception:
                pass
            await page.locator("tr", has_text=numero).first.get_by_role("button", name="Ver / Recibir").click()
            await page.get_by_role("dialog").wait_for()
            await page.wait_for_timeout(1500)
            det = await shot(page, f"G_{numero}_detalle")
            det_txt = (await dlg_text(page))[:700]
            await page.get_by_role("dialog").get_by_role("button", name="Recibir", exact=True).click()
            await page.wait_for_timeout(1500)
            return det, det_txt

        try:
            det, det_txt = await abrir_recepcion(f"QA8-OC-{TS}-B")
            log("G1_detalle_orden_B", texto=det_txt, shot=det, recepcion_shot=await shot(page, "G1_recepcion_vacia"))
            # Confirmar sin configurar nada (todas las líneas en "Ignorar")
            await page.get_by_role("dialog").last.get_by_role("button", name=re.compile("Confirmar")).click()
            sn = await snacks(page, 2500)
            st, oc = await api(page, "GET", f"/compras/ordenes/{ordenes['B']}")
            log("G2_recepcion_todo_ignorado", snacks=sn, estado_orden=oc.get("OCO_Estado") if isinstance(oc, dict) else oc,
                shot=await shot(page, "G2_recepcion_todo_ignorado"))
            await page.keyboard.press("Escape")
        except Exception as e:
            log("G2_error", error=str(e)[:300], shot=await shot(page, "G2_error"))
            await page.keyboard.press("Escape")

        try:
            det, det_txt = await abrir_recepcion(f"QA8-OC-{TS}-A")
            d = page.get_by_role("dialog").last
            await d.get_by_label("Acción").first.click()
            await page.get_by_role("option", name="Crear activo").click()
            await page.wait_for_timeout(600)
            await d.get_by_label(re.compile("^Números de serie")).fill(f"QA8-R1-{TS}")
            await d.get_by_role("button", name=re.compile("Confirmar")).click()
            sn = await snacks(page, 1500)
            log("G3_serie_incompleta", snacks=sn, shot=await shot(page, "G3_recepcion_serie_incompleta"))
            await d.get_by_label(re.compile("^Números de serie")).fill(f"QA8-R1-{TS}\nQA8-R2-{TS}")
            await d.get_by_label(re.compile("^Tipo de activo")).click()
            await page.get_by_role("option").first.click()
            await page.wait_for_timeout(400)
            await d.get_by_label(re.compile("^Modelo")).click()
            modelos_rec = await page.get_by_role("option").all_inner_texts()
            await page.get_by_role("option").first.click()
            s = await shot(page, "G4_recepcion_configurada")
            await d.get_by_role("button", name=re.compile("Confirmar")).click()
            sn = await snacks(page, 3000)
            log("G4_recepcion_ok", snacks=sn, n_modelos_select=len(modelos_rec), shot_config=s, shot=await shot(page, "G5_tras_recepcion"),
                dialogo_abierto=await page.get_by_role("dialog").count())
        except Exception as e:
            log("G_error", error=str(e)[:300], shot=await shot(page, "G_error"))
            await page.keyboard.press("Escape")

        # ── H. Offboarding de la persona QA8 (tiene el activo QA8) ──────────
        try:
            await page.goto(f"{BASE}/empleados")
            await page.wait_for_load_state("networkidle")
            await page.get_by_placeholder(re.compile("Buscar", re.I)).first.fill(f"Prueba{TS}")
            await page.wait_for_timeout(1200)
            fila = page.locator("tr", has_text=f"Prueba{TS}").first
            await fila.get_by_role("button", name="Ver activos asignados").click()
            await page.get_by_role("dialog").wait_for()
            await page.wait_for_timeout(1500)
            log("H0_detalle_persona", texto=(await dlg_text(page))[:700], shot=await shot(page, "H0_persona_detalle"))
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(500)
            await fila.get_by_role("button", name=re.compile("Salida y descargo")).click()
            await page.get_by_role("dialog").wait_for()
            await page.wait_for_timeout(1500)
            log("H1_offboarding", texto=(await dlg_text(page))[:900], shot=await shot(page, "H1_offboarding"))
            d = page.get_by_role("dialog").last
            await d.get_by_role("checkbox").last.check()
            await d.get_by_role("button").last.click()
            sn = await snacks(page, 2500)
            log("H2_offboarding_hecho", snacks=sn, shot=await shot(page, "H2_tras_offboarding"))
        except Exception as e:
            log("H_error", error=str(e)[:300], shot=await shot(page, "H_error"))

        log("consola", errores=sorted(set(errs))[:10])
        await guardar(ctx)
        await browser.close()
    with open(os.path.join(OUT, "flujos.json"), "w", encoding="utf-8") as f:
        json.dump(LOG, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    asyncio.run(main())

"""
QA de licencias con activación variable (plan, bloque A §3.4) en el stack
DESECHABLE inv_qa (https://localhost:8443), por la interfaz:

  L1  Microsoft 365: Suscripción · Cuenta (correo + clave) · Por persona, cuentas pegadas en lista.
  L2  El listado no trae secretos; «Mostrar» lo enseña, lo oculta solo y queda en la bitácora.
  L3  Un método creado por el usuario («Código de suscripción + PIN») cambia el formulario sin tocar código.
  L4  La entrega de puesto ofrece la licencia por persona.
  L5  La salida de una persona libera su cuenta y la bandeja pide cambiar la clave.

Requiere la cuenta de prueba: python herramientas-qa/cuenta_inv_qa.py
Uso:  python herramientas-qa/qa11_licencias.py   (resultado en salidas/qa11/)
"""
import asyncio
import json
import os
import re

from playwright.async_api import async_playwright

import qa10_catalogos as Q

OUT = os.path.join(Q.AQUI, "salidas", "qa11")
os.makedirs(OUT, exist_ok=True)
RES: list[dict] = []


def caso(nombre: str, ok: bool, **extra) -> None:
    RES.append({"caso": nombre, "ok": bool(ok), **extra})
    print(("OK   " if ok else "FALLA"), nombre, "" if ok else json.dumps(extra, ensure_ascii=False, default=str)[:400])


async def shot(page, nombre: str) -> str:
    ruta = os.path.join(OUT, f"{nombre}.png")
    await page.screenshot(path=ruta, full_page=True)
    return ruta


async def buscar_software(page, nombre: str):
    await page.goto(f"{Q.BASE}/software")
    await page.wait_for_load_state("networkidle")
    await page.get_by_placeholder(re.compile("Buscar", re.I)).first.fill(nombre)
    await page.wait_for_timeout(1200)
    return page.locator("tr", has_text=nombre).first


async def elegir(page, dialogo, etiqueta: str, opcion) -> None:
    await dialogo.get_by_label(etiqueta, exact=False).first.click()
    await page.get_by_role("option", name=opcion).first.click()


async def main():
    api = Q.api
    async with async_playwright() as p:
        b = await p.chromium.launch()
        ctx = await b.new_context(viewport={"width": 1440, "height": 900}, ignore_https_errors=True, locale="es-ES")
        page = await ctx.new_page()
        await Q.login(page)
        suf = Q.SUF
        _, sw = await api(page, "POST", "/soft/software", {"SOF_Nombre": f"Microsoft 365 QA {suf}", "MAR_Marca": None,
                                                           "SOF_Fabricante": "Microsoft"})
        caso("datos: software", "SOF_Software" in sw, detalle=sw)

        # ---------- L1: alta por la interfaz con cuentas pegadas ----------
        fila = await buscar_software(page, f"Microsoft 365 QA {suf}")
        await fila.get_by_role("button", name="Agregar licencia").click()
        dlg = page.get_by_role("dialog")
        await dlg.wait_for()
        await elegir(page, dlg, "Modelo comercial", "Suscripción")
        await elegir(page, dlg, "Forma de activación", re.compile("^Cuenta"))
        await elegir(page, dlg, "Forma de conteo", re.compile("^Por persona"))
        await dlg.get_by_label("Cupos", exact=False).first.fill("3")
        await dlg.get_by_role("tab", name="Pegar una lista").click()
        lineas = "\n".join(f"m365.{suf}.{i}@lombardi.qa;Clave-QA-{suf}-{i}" for i in range(3))
        await dlg.get_by_label("Una credencial por línea").fill(lineas)
        texto = await dlg.inner_text()
        caso("L1 vista previa del pegado", "3 de 3 líneas son válidas" in texto and "Correo;Contraseña" in texto,
             shot=await shot(page, "L1_pegado"))
        await dlg.get_by_role("button", name=re.compile("^Agregar 3 credenciales")).click()
        await dlg.get_by_role("button", name="Guardar", exact=True).click()
        await page.wait_for_timeout(2000)
        _, lics = await api(page, "GET", f"/soft/licencias?software_id={sw['SOF_Software']}")
        lic = lics[0] if lics else {}
        _, creds = await api(page, "GET", f"/soft/licencias/{lic.get('LIC_Licencia')}/credenciales")
        caso("L1 licencia con 3 cuentas por persona", len(creds) == 3 and all(c["tiene_secreto"] for c in creds),
             detalle=str(creds)[:300])

        # ---------- L2: sin secretos en el listado; mostrar auditado ----------
        caso("L2 el listado no trae la clave", f"Clave-QA-{suf}" not in json.dumps(creds))
        fila = await buscar_software(page, f"Microsoft 365 QA {suf}")
        await fila.get_by_role("button", name="Ver licencias").click()
        await page.wait_for_timeout(1200)
        await page.get_by_role("button", name="Ver claves").first.click()
        await page.wait_for_timeout(1500)
        panel = await page.locator("main").inner_text()
        caso("L2 panel muestra cuentas sin clave", f"m365.{suf}.0@lombardi.qa" in panel and f"Clave-QA-{suf}" not in panel,
             shot=await shot(page, "L2_panel"))
        await page.get_by_role("button", name="Mostrar (queda registrado)").first.click()
        await page.wait_for_timeout(1200)
        visible = await page.locator("main").inner_text()
        caso("L2 «Mostrar» enseña la clave con cuenta regresiva",
             f"Clave-QA-{suf}-0" in visible and "Se oculta en" in visible, shot=await shot(page, "L2_mostrar"))
        _, aud = await api(page, "GET", "/gov/auditoria?accion=LICENSE_SECRET_VIEWED&limit=5")
        caso("L2 consulta registrada en la bitácora", "LICENSE_SECRET_VIEWED" in json.dumps(aud) and
             f"Clave-QA-{suf}" not in json.dumps(aud))
        await page.wait_for_timeout(31000)
        caso("L2 la clave se oculta sola a los 30 s", f"Clave-QA-{suf}-0" not in await page.locator("main").inner_text())

        # ---------- L3: método nuevo creado por el usuario ----------
        st, metodo = await api(page, "POST", "/catalogos/metodos-activacion", {
            "nombre": f"Código de suscripción + PIN {suf}",
            "extras": {"MAC_Pide_Serial": True, "MAC_Pide_Secreto": True,
                       "MAC_Etiqueta_Serial": "Código de suscripción", "MAC_Etiqueta_Secreto": "PIN"}})
        fila = await buscar_software(page, f"Microsoft 365 QA {suf}")
        await fila.get_by_role("button", name="Agregar licencia").click()
        dlg = page.get_by_role("dialog")
        await dlg.wait_for()
        await elegir(page, dlg, "Forma de activación", re.compile(f"^Código de suscripción \\+ PIN {suf}"))
        await page.wait_for_timeout(500)
        caso("L3 el formulario pide los campos del método nuevo",
             st == 201 and await dlg.get_by_label("Código de suscripción").count() == 1
             and await dlg.get_by_label("PIN").count() == 1, shot=await shot(page, "L3_metodo_nuevo"))
        await page.keyboard.press("Escape")

        # ---------- L4: entrega de puesto con la licencia ----------
        await page.goto(f"{Q.BASE}/operaciones/alta-empleado")
        await page.wait_for_load_state("networkidle")
        # Asistente: 1) persona → 2) equipos del puesto (donde están las licencias).
        await page.get_by_label("Persona registrada").fill("QA")
        await page.get_by_role("option").first.click(timeout=10000)
        await page.get_by_role("button", name="Continuar").click()
        await page.wait_for_timeout(2000)
        t4 = await page.locator("main").inner_text()
        caso("L4 la entrega ofrece la licencia por persona",
             "Licencias y cuentas" in t4 and f"Microsoft 365 QA {suf} · 3 libres" in t4, shot=await shot(page, "L4_entrega"))

        # ---------- L5: salida de la persona ----------
        _, deps = await api(page, "GET", "/org/departamentos")
        _, cargos = await api(page, "GET", "/org/cargos")
        _, sedes = await api(page, "GET", "/org/sedes")
        _, per = await api(page, "POST", "/org/personas", {
            "PER_Primer_Nombre": "QA", "PER_Primer_Apellido": f"Salida{suf}",
            "PER_Email_Corporativo": f"qa.salida{suf}@lombardi.qa", "DEP_Departamento": deps[0]["DEP_Departamento"],
            "CAR_Cargo": cargos[0]["CAR_Cargo"], "SED_Sede": sedes[0]["SED_Sede"]})
        st, ins = await api(page, "POST", "/soft/instalaciones", {"LIC_Licencia": lic["LIC_Licencia"],
                                                                   "PER_Persona": per["PER_Persona"]})
        st2, off = await api(page, "POST", f"/trazabilidad/persona/{per['PER_Persona']}/offboarding")
        caso("L5 la salida libera la cuenta", st == 201 and st2 == 200 and off.get("cuentas_por_cambiar") == 1,
             detalle=[st, st2, off])
        await page.goto(f"{Q.BASE}/")
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(2000)
        t5 = await page.locator("main").inner_text()
        caso("L5 la bandeja pide cambiar la clave", re.search(r"deben? cambiar su clave", t5) is not None, shot=await shot(page, "L5_bandeja"))

        await b.close()
    json.dump(RES, open(os.path.join(OUT, "resultado.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    print(f"\n{sum(r['ok'] for r in RES)}/{len(RES)} casos correctos")


if __name__ == "__main__":
    asyncio.run(main())

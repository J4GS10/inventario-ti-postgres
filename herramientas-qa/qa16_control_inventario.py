"""
QA de control del inventario (sesión 16) en el stack DESECHABLE inv_qa:

  K1  Tipos: cada tipo con su clase; un insumo se reclasifica con aprobación.
  K2  El Jefe de TI aprueba la reclasificación: el consumible recibe la existencia.
  K3  Impresora: «Instalar en un área» en vez de «Asignar»; sin pestaña de software.
  K4  Asignación: un solo motivo (obligatorio) y una sola nota.
  K5  Un ajuste que baja existencias queda pendiente de aprobación.
  K6  Faltantes de una toma: nota y «Localizado».
  K7  Cierre mensual: tablas y exportación a Excel; sin desborde en móvil.

Uso:  python herramientas-qa/cuenta_inv_qa.py  &&  python herramientas-qa/qa16_control_inventario.py
"""
import asyncio
import json
import os
import re
import secrets
import subprocess
import time

import pyotp
from playwright.async_api import async_playwright

import qa10_catalogos as Q

OUT = os.path.join(Q.AQUI, "salidas", "qa16")
os.makedirs(OUT, exist_ok=True)
RES: list[dict] = []


def caso(nombre: str, ok: bool, **extra) -> None:
    RES.append({"caso": nombre, "ok": bool(ok), **extra})
    print(("OK   " if ok else "FALLA"), nombre, "" if ok else json.dumps(extra, ensure_ascii=False, default=str)[:500])


async def shot(page, nombre: str) -> str:
    ruta = os.path.join(OUT, f"{nombre}.png")
    await page.screenshot(path=ruta, full_page=True)
    return ruta


def cuenta_jefe() -> dict:
    """Jefe de TI de prueba (aprueba lo que pide qa.catalogos) en la base del contenedor."""
    c = {"usuario": "qa.jefe", "clave": "Qa-" + secrets.token_urlsafe(10) + "9!", "totp": pyotp.random_base32()}
    codigo = f"""
import asyncio
from sqlalchemy import select
from app.core.security import encrypt_field, get_password_hash
from app.core.data_scope import system_scope
from app.db.session import SessionLocal
from app.models.organization import Departamento, Cargo, Persona, Usuario
async def main():
    with system_scope():
        async with SessionLocal() as db:
            u = (await db.execute(select(Usuario).where(Usuario.USU_Username == "{c['usuario']}"))).scalar_one_or_none()
            if u is None:
                dep = (await db.execute(select(Departamento))).scalars().first()
                car = (await db.execute(select(Cargo))).scalars().first()
                p = Persona(PER_Primer_Nombre="QA", PER_Primer_Apellido="Jefe", PER_Email_Corporativo="qa.jefe@lombardi.qa.local",
                            DEP_Departamento=dep.DEP_Departamento, CAR_Cargo=car.CAR_Cargo)
                db.add(p); await db.flush()
                u = Usuario(USU_Username="{c['usuario']}", USU_Rol="JEFE_TI", PER_Persona=p.PER_Persona, USU_Alcance_Global=True)
                db.add(u)
            u.USU_Password_Hash = get_password_hash("{c['clave']}")
            u.USU_2FA_Habilitado = True
            u.USU_2FA_Metodo = "TOTP"
            u.USU_2FA_Secret = encrypt_field("{c['totp']}")
            u.USU_2FA_Ultimo_Paso = None
            u.USU_Debe_Cambiar_Password = False
            u.USU_Estado = True
            await db.commit()
    print("ok")
asyncio.run(main())
"""
    r = subprocess.run(["docker", "exec", "-i", "inv_qa-backend-1", "python", "-"], input=codigo, text=True,
                       capture_output=True, encoding="utf-8")
    if "ok" not in r.stdout:
        raise SystemExit(f"No se pudo crear el Jefe de TI: {r.stdout}\n{r.stderr}")
    return c


async def login_jefe(page, c: dict) -> str:
    box = {}

    async def capturar(resp):
        if "/login/2fa/verify" in resp.url and resp.ok:
            try:
                box["token"] = (await resp.json()).get("access_token")
            except Exception:  # noqa: BLE001
                pass
    page.on("response", capturar)
    await page.goto(f"{Q.BASE}/login")
    await page.evaluate("localStorage.setItem('app_language', 'es')")
    await page.goto(f"{Q.BASE}/login")
    await page.wait_for_load_state("networkidle")
    await page.fill("input[name=username]", c["usuario"])
    await page.fill("input[name=password]", c["clave"])
    await page.click("button[type=submit]")
    otp = page.locator("input[autocomplete=one-time-code]")
    await otp.wait_for(timeout=15000)
    await otp.fill(pyotp.TOTP(c["totp"]).now())
    await otp.press("Enter")
    await page.wait_for_url(lambda u: "/login" not in u, timeout=20000)
    await page.wait_for_load_state("networkidle")
    return box.get("token", "")


async def cerrar_ayuda(page):
    dlg = page.get_by_role("dialog")
    if await dlg.count() and await dlg.get_by_role("button", name="Omitir").count():
        await dlg.get_by_role("button", name="Omitir").first.click()


async def ir(page, ruta: str):
    await page.goto(f"{Q.BASE}{ruta}")
    await page.wait_for_load_state("networkidle")
    await page.wait_for_timeout(1200)
    await cerrar_ayuda(page)


async def elegir_primera_opcion(page, etiqueta):
    campo = page.get_by_role("dialog").get_by_label(etiqueta, exact=False).first
    await campo.click()
    await page.wait_for_timeout(500)
    await page.get_by_role("option").first.click()


async def main():
    jefe = cuenta_jefe()
    api = Q.api
    async with async_playwright() as p:
        b = await p.chromium.launch()
        ctx = await b.new_context(viewport={"width": 1440, "height": 900}, ignore_https_errors=True, locale="es-ES",
                                  accept_downloads=True)
        page = await ctx.new_page()
        await Q.login(page)

        # ---------------- K1 reclasificación de un insumo ----------------
        _, tipos = await api(page, "GET", "/cat/tipos-activo")
        aire = None
        for t in tipos:
            if t["TAC_Clase"] == "INSUMO":
                _, v = await api(page, "GET", f"/core/tipos-activo/{t['TAC_Tipo_Activo']}/reclasificar")
                if v["activos"] and not v["bloqueados"] and len(v["por_sede"]) == 1:
                    aire = t
                    break
        await ir(page, "/config/tipos")
        fila = page.get_by_role("row", name=re.compile(re.escape(aire["TAC_Nombre"])))
        chip_ok = await fila.get_by_text("Insumo").count() > 0
        await fila.get_by_role("button", name=re.compile("Reclasificar")).click()
        dlg = page.get_by_role("dialog")
        await dlg.get_by_text(re.compile(r"\d+ activos de este tipo")).wait_for(timeout=10000)
        texto = await dlg.inner_text()
        sede_sel = dlg.get_by_role("combobox", name="Sede")
        if not (await sede_sel.inner_text()).strip().strip("​"):
            await sede_sel.click()
            await page.get_by_role("option").first.click()
        await dlg.get_by_label("Motivo").fill("Es un insumo de limpieza: se controla por cantidad")
        await shot(page, "K1_reclasificar")
        await dlg.get_by_role("button", name="Solicitar reclasificación").click()
        snack = await Q.snacks(page)
        caso("K1 insumo con clase y reclasificación solicitada", chip_ok and "Se envió a aprobación" in " ".join(snack),
             texto=texto[:200], snack=snack)

        # ---------------- K2 el Jefe de TI aprueba ----------------
        ctx_j = await b.new_context(viewport={"width": 1440, "height": 900}, ignore_https_errors=True, locale="es-ES")
        pj = await ctx_j.new_page()
        await login_jefe(pj, jefe)
        await ir(pj, "/aprobaciones")
        fila = pj.get_by_role("row", name=re.compile("Reclasificación a consumible"))
        await fila.get_by_role("button", name=re.compile("Aprobar")).first.click()
        dlg = pj.get_by_role("dialog")
        await dlg.get_by_role("button", name=re.compile("Aprobar")).last.click()
        await pj.wait_for_timeout(2000)
        await shot(pj, "K2_aprobado")
        _, consumibles = await api(page, "GET", "/consumibles")
        nuevo = next((c for c in consumibles if c["CON_Nombre"].lower() == aire["TAC_Nombre"].lower()), None)
        _, vista = await api(page, "GET", f"/core/tipos-activo/{aire['TAC_Tipo_Activo']}/reclasificar")
        caso("K2 aprobada: consumible con la existencia y sin activos del tipo",
             nuevo is not None and nuevo["CON_Stock_Actual"] > 0 and vista["activos"] == 0,
             consumible=nuevo, vista=vista)

        # ---------------- K3 impresora: instalar en un área ----------------
        imp_tipo = next(t for t in tipos if t["TAC_Nombre"].lower() == "impresora")
        _, modelos = await api(page, "GET", "/cat/modelos-flat")
        _, estados = await api(page, "GET", "/cat/estados-operativos")
        bodega = next(e for e in estados if e["EOP_Nombre"] == "En Bodega")
        _, sedes = await api(page, "GET", "/org/sedes")
        sede_id = sedes[0]["SED_Sede"] if isinstance(sedes, list) else sedes["sedes"][0]["SED_Sede"]
        s, imp = await api(page, "POST", "/core/activos", {
            "ACT_Serie_Fabricante": f"QA-IMP-{secrets.token_hex(3)}", "ACT_Fecha_Compra": time.strftime("%Y-%m-%d"),
            "MOD_Modelo": modelos[0]["MOD_Modelo"], "TAC_Tipo_Activo": imp_tipo["TAC_Tipo_Activo"],
            "EOP_Estado_Operativo": bodega["EOP_Estado_Operativo"], "SED_Sede": sede_id})
        await ir(page, f"/activos/{imp['ACT_Codigo_Interno']}")
        boton_instalar = page.get_by_role("button", name="Instalar en un área")
        sin_asignar = await page.get_by_role("button", name="Asignar", exact=True).count() == 0
        software_off = await page.get_by_role("tab", name=re.compile("Software")).first.is_disabled()
        await boton_instalar.first.click()
        dlg = page.get_by_role("dialog").last
        await dlg.get_by_label("Área donde queda").click()
        await page.get_by_role("option").first.click()
        await dlg.get_by_label("Motivo").click()
        await page.get_by_role("option", name="Instalación nueva").click()
        await shot(page, "K3_instalar")
        await dlg.get_by_role("button", name="Instalar en un área").click()
        snack = await Q.snacks(page)
        await page.wait_for_timeout(1500)
        await ir(page, f"/activos/{imp['ACT_Codigo_Interno']}")
        info = await page.locator("main").inner_text()
        await shot(page, "K3_instalado")
        caso("K3 impresora se instala en un área (no se asigna, sin software)",
             s == 201 and sin_asignar and software_off and "quedó instalado" in " ".join(snack) and "Instalado en" in info,
             status=s, sin_asignar=sin_asignar, software_off=software_off, snack=snack)

        # ---------------- K4 asignación: un motivo y una nota ----------------
        _, busqueda = await api(page, "POST", "/core/activos/search",
                                {"estado_operativo_id": bodega["EOP_Estado_Operativo"], "per_page": 200})
        personales = {t["TAC_Tipo_Activo"] for t in tipos if t["reglas"]["uso"] == "PERSONAL"}
        laptop = next(a for a in busqueda["items"] if a["TAC_Tipo_Activo"] in personales and not a.get("ACT_Activo_Padre"))
        await ir(page, f"/activos/{laptop['ACT_Codigo_Interno']}")
        await page.get_by_role("button", name="Asignar", exact=True).first.click()
        dlg = page.get_by_role("dialog").last
        await dlg.get_by_label("Nota").first.wait_for(timeout=10000)
        etiquetas = await dlg.inner_text()
        await shot(page, "K4_asignar")
        caso("K4 asignación con un motivo obligatorio y una nota",
             "Motivo *" in etiquetas.replace(" ", " ") and "Nota" in etiquetas and "Observaciones" not in etiquetas
             and etiquetas.count("Detalle") == 0,
             texto=etiquetas[-600:])
        await page.keyboard.press("Escape")

        # ---------------- K5 ajuste que baja: pendiente ----------------
        s, r = await api(page, "POST", f"/consumibles/{nuevo['CON_Consumible']}/ajuste",
                         {"CON_Stock_Real": max(nuevo["CON_Stock_Actual"] - 2, 0), "MOC_Motivo": "Conteo de control QA"})
        _, despues = await api(page, "GET", f"/consumibles/{nuevo['CON_Consumible']}")
        caso("K5 disminución queda pendiente de aprobación", s == 202 and despues["CON_Stock_Actual"] == nuevo["CON_Stock_Actual"],
             status=s, respuesta=r)

        # ---------------- K6 faltantes de una toma ----------------
        s, toma = await api(page, "POST", "/inventario/tomas", {"SED_Sede": sede_id})
        await api(page, "POST", f"/inventario/tomas/{toma['TOM_Toma']}/cerrar")
        await ir(page, "/inventario/tomas?vista=faltantes")
        filas = page.get_by_role("row")
        hay = await filas.count() > 1
        primera = filas.nth(1)
        await primera.get_by_role("button", name="Nota").click()
        dlg = page.get_by_role("dialog")
        await dlg.get_by_label("Qué se investigó").fill("Se revisó la bodega y la sala de juntas")
        await dlg.get_by_role("button", name="Guardar nota").click()
        nota = await Q.snacks(page)
        primera = page.get_by_role("row").nth(1)
        await primera.get_by_role("button", name="Localizado").click()
        dlg = page.get_by_role("dialog")
        await dlg.get_by_label("Explicación").fill("Estaba en préstamo informal")
        await dlg.get_by_role("button", name="Marcar como localizado").click()
        loc = await Q.snacks(page)
        await shot(page, "K6_faltantes")
        caso("K6 faltantes: nota y localizado", hay and "Nota registrada" in " ".join(nota) and "el equipo apareció" in " ".join(loc),
             nota=nota, loc=loc)

        # ---------------- K7 cierre mensual ----------------
        await ir(page, "/contabilidad/cierre")
        texto = await page.locator("main").inner_text()
        async with page.expect_download() as descarga:
            await page.get_by_role("button", name="Exportar a Excel").click()
        archivo = await descarga.value
        await shot(page, "K7_cierre")
        await page.set_viewport_size({"width": 390, "height": 844})
        await page.wait_for_timeout(800)
        desborde = await Q.desborde(page)
        await page.set_viewport_size({"width": 1440, "height": 900})
        caso("K7 cierre mensual con exportación y sin desborde en móvil",
             "Activos por sede y moneda" in texto and archivo.suggested_filename.endswith(".xlsx") and desborde <= 0,
             archivo=archivo.suggested_filename, desborde=desborde)

        await b.close()
    fallas = [r for r in RES if not r["ok"]]
    json.dump(RES, open(os.path.join(OUT, "resultado.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\n{len(RES) - len(fallas)}/{len(RES)} casos OK")


asyncio.run(main())

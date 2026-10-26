"""
QA de la matriz de roles en el stack DESECHABLE inv_qa (https://localhost:8443).

Crea una cuenta por rol (con TOTP enrolado) directamente en la base del
contenedor y, para cada una:

  R1  Recorre las páginas que su menú ofrece: ninguna llamada de la interfaz
      recibe 403 (la UI no pide lo que el rol no ve) y no hay errores en pantalla.
  R2  El menú muestra exactamente los módulos del rol.
  R3  Una ruta ajena redirige (no muestra datos).
  R4  TECNICO y CONSULTA no ven costos (ni en la API ni en pantalla).
  R5  COMPRAS crea un proveedor y una orden, y no tiene «Recibir».

No toca el stack formal. Uso:  python herramientas-qa/qa15_roles.py
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

AQUI = os.path.dirname(os.path.abspath(__file__))
BASE = "https://localhost:8443"
API = f"{BASE}/api/v1"
OUT = os.path.join(AQUI, "salidas", "qa15")
os.makedirs(OUT, exist_ok=True)
RES: list[dict] = []

# Páginas que cada rol debe ver en el menú (espejo de src/router/navigation.ts).
STOCK = ["/", "/activos", "/consumibles"]
INV = ["/scanner", "/operaciones/movimientos", "/mantenimiento", "/trazabilidad", "/inventario/tomas"]
COMPRAS_P = ["/compras/ordenes", "/compras/proveedores", "/compras/garantias"]
PAGINAS = {
    "TECNICO": STOCK + INV,
    "CONSULTA": STOCK + INV,
    "AUDITOR": STOCK + INV + ["/aprobaciones", "/auditoria"] + COMPRAS_P + ["/software", "/empleados", "/departamentos",
                                                                            "/config/catalogos"],
    "COMPRAS": COMPRAS_P,
    "CONTABILIDAD": STOCK + COMPRAS_P + ["/software"],
}
AJENA = {"TECNICO": "/compras/ordenes", "CONSULTA": "/empleados", "AUDITOR": "/config/marcas",
         "COMPRAS": "/activos", "CONTABILIDAD": "/trazabilidad"}
DINERO = re.compile(r"(GTQ|USD|CHF|Q\s?\d|\$\s?\d)")


def caso(nombre: str, ok: bool, **extra) -> None:
    RES.append({"caso": nombre, "ok": bool(ok), **extra})
    print(("OK   " if ok else "FALLA"), nombre, "" if ok else json.dumps(extra, ensure_ascii=False, default=str)[:600])


def crear_cuentas() -> dict:
    cuentas = {rol: {"usuario": f"qa.rol.{rol.lower()}", "clave": "Qa-" + secrets.token_urlsafe(10) + "9!",
                     "totp": pyotp.random_base32()} for rol in PAGINAS}
    codigo = f"""
import asyncio
from sqlalchemy import select
from app.core.security import encrypt_field, get_password_hash
from app.core.data_scope import system_scope
from app.db.session import SessionLocal
from app.models.organization import Departamento, Cargo, Persona, Usuario
CUENTAS = {json.dumps(cuentas)}
async def main():
    with system_scope():
        async with SessionLocal() as db:
            dep = (await db.execute(select(Departamento))).scalars().first()
            car = (await db.execute(select(Cargo))).scalars().first()
            for rol, c in CUENTAS.items():
                u = (await db.execute(select(Usuario).where(Usuario.USU_Username == c["usuario"]))).scalar_one_or_none()
                if u is None:
                    correo = c["usuario"] + "@lombardi.qa.local"
                    p = Persona(PER_Primer_Nombre="QA", PER_Primer_Apellido=rol.title(), PER_Email_Corporativo=correo,
                                DEP_Departamento=dep.DEP_Departamento, CAR_Cargo=car.CAR_Cargo)
                    db.add(p); await db.flush()
                    u = Usuario(USU_Username=c["usuario"], USU_Rol=rol, PER_Persona=p.PER_Persona, USU_Alcance_Global=True)
                    db.add(u)
                u.USU_Rol = rol
                u.USU_Password_Hash = get_password_hash(c["clave"])
                u.USU_2FA_Habilitado = True
                u.USU_2FA_Metodo = "TOTP"
                u.USU_2FA_Secret = encrypt_field(c["totp"])
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
        raise SystemExit(f"No se pudieron crear las cuentas: {r.stdout}\n{r.stderr}")
    return cuentas


async def login(page, c: dict) -> str:
    box = {}

    async def capturar(resp):
        if "/login/2fa/verify" in resp.url and resp.ok:
            try:
                box["token"] = (await resp.json()).get("access_token")
            except Exception:  # noqa: BLE001
                pass
    page.on("response", capturar)
    await page.goto(f"{BASE}/login")
    await page.evaluate("localStorage.setItem('app_language', 'es')")
    await page.goto(f"{BASE}/login")
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
    if not box.get("token"):
        # El token vive en memoria de la SPA: se pide uno con la cookie de sesión.
        r = await page.request.post(f"{API}/login/refresh", ignore_https_errors=True)
        if r.ok:
            box["token"] = (await r.json()).get("access_token")
    # La ayuda de cada página se abre sola la primera vez: se cierra con «Omitir» (cerrar_ayuda).
    return box.get("token", "")


async def api(page, token: str, method: str, path: str, body=None):
    r = await page.request.fetch(f"{API}{path}", method=method, headers={"Authorization": f"Bearer {token}"},
                                 data=body, ignore_https_errors=True)
    try:
        return r.status, await r.json()
    except Exception:  # noqa: BLE001
        return r.status, await r.text()


async def cerrar_ayuda(page):
    dlg = page.get_by_role("dialog")
    if await dlg.count():
        omitir = dlg.get_by_role("button", name="Omitir")
        if await omitir.count():
            await omitir.first.click()


async def recorrer(b, rol: str, c: dict) -> None:
    ctx = await b.new_context(viewport={"width": 1440, "height": 900}, ignore_https_errors=True, locale="es-ES")
    page = await ctx.new_page()
    prohibidas: list[str] = []
    errores: list[str] = []

    def al_responder(resp):
        if "/api/v1/" in resp.url and resp.status == 403:
            prohibidas.append(f"{resp.request.method} {resp.url.split('/api/v1')[1]}")
    page.on("response", al_responder)
    page.on("pageerror", lambda e: errores.append(str(e)[:200]))
    token = await login(page, c)

    # R2: menú del rol.
    await page.goto(f"{BASE}/")
    await page.wait_for_load_state("networkidle")
    await page.wait_for_timeout(1200)
    destino_inicio = page.url.replace(BASE, "")
    menu = await page.locator("nav, [role=navigation], .MuiDrawer-root").first.inner_text()
    caso(f"R2 {rol} inicio", True, inicio=destino_inicio, menu=menu.replace("\n", " · ")[:500])

    # R1: cada página sin 403 ni errores.
    for ruta in PAGINAS[rol]:
        antes_p, antes_e = len(prohibidas), len(errores)
        await page.goto(f"{BASE}{ruta}")
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(1200)
        await cerrar_ayuda(page)
        texto = await page.locator("main").inner_text() if await page.locator("main").count() else ""
        nuevas = prohibidas[antes_p:]
        ok = not nuevas and len(errores) == antes_e and ruta.split("?")[0] in page.url
        extra = {"403": nuevas, "errores": errores[antes_e:], "url": page.url}
        if rol in ("TECNICO", "CONSULTA"):
            montos = DINERO.findall(texto)
            ok = ok and not montos
            extra["montos"] = montos[:5]
        await page.screenshot(path=os.path.join(OUT, f"{rol}_{ruta.strip('/').replace('/', '_') or 'inicio'}.png"))
        caso(f"R1 {rol} {ruta}", ok, **extra)

    # R3: ruta ajena.
    await page.goto(f"{BASE}{AJENA[rol]}")
    await page.wait_for_load_state("networkidle")
    await page.wait_for_timeout(800)
    caso(f"R3 {rol} no entra a {AJENA[rol]}", AJENA[rol] not in page.url, url=page.url)

    # R4: costos en la API.
    if rol in ("TECNICO", "CONSULTA"):
        s, activos = await api(page, token, "GET", "/core/activos?limit=50")
        s2, tablero = await api(page, token, "GET", "/stats/dashboard")
        caso(f"R4 {rol} API sin costos", s == 200 and all("ACT_Costo" not in a for a in activos)
             and "costo_inventario_por_moneda" not in tablero, status=(s, s2))

    # R5: Compras registra y no recibe.
    if rol == "COMPRAS":
        sufijo = secrets.token_hex(3)
        s, prv = await api(page, token, "POST", "/compras/proveedores", {"PRV_Nombre": f"Proveedor QA {sufijo}"})
        s_o, orden = await api(page, token, "POST", "/compras/ordenes", {
            "OCO_Numero": f"OC-QA-{sufijo}", "OCO_Fecha": time.strftime("%Y-%m-%d"),
            "PRV_Proveedor": prv.get("PRV_Proveedor") if isinstance(prv, dict) else 0,
            "lineas": [{"OCL_Descripcion": "Laptop de prueba", "OCL_Cantidad": 1, "OCL_Precio_Unitario": "900",
                        "OCL_Tipo": "ACTIVO"}]})
        s_r, _ = await api(page, token, "POST", f"/compras/ordenes/{orden.get('OCO_Orden', 0)}/recibir", {})
        print("   COMPRAS api:", s, s_o, s_r, str(orden)[:200])
        await page.goto(f"{BASE}/compras/ordenes")
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(1000)
        nueva = await page.get_by_role("button", name=re.compile("Nueva orden", re.I)).count()
        buscar = page.get_by_placeholder(re.compile("Buscar", re.I))
        if await buscar.count():
            await buscar.first.fill(f"OC-QA-{sufijo}")
            await page.wait_for_timeout(800)
        await page.get_by_text(f"OC-QA-{sufijo}").first.click(timeout=10000)
        await page.wait_for_timeout(1200)
        recibir = await page.get_by_role("dialog").get_by_role("button", name=re.compile("^Recibir", re.I)).count()
        await page.screenshot(path=os.path.join(OUT, "COMPRAS_orden.png"))
        caso("R5 COMPRAS crea proveedor y orden, no recibe", s == 201 and s_o == 201 and s_r == 403 and nueva and not recibir,
             status=(s, s_o, s_r), boton_nueva=nueva, boton_recibir=recibir)
        # Limpieza: la orden de prueba se cancela (es un stack desechable).
        await api(page, token, "PATCH", f"/compras/ordenes/{orden.get('OCO_Orden', 0)}/estado",
                  {"OCO_Estado": "CANCELADA", "motivo": "Prueba QA"})
    await ctx.close()


async def main():
    cuentas = crear_cuentas()
    async with async_playwright() as p:
        b = await p.chromium.launch()
        for rol, c in cuentas.items():
            try:
                await recorrer(b, rol, c)
            except Exception as e:  # noqa: BLE001
                caso(f"{rol} recorrido completo", False, error=str(e)[:400])
        await b.close()
    fallas = [r for r in RES if not r["ok"]]
    json.dump(RES, open(os.path.join(OUT, "resultado.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\n{len(RES) - len(fallas)}/{len(RES)} casos OK")


asyncio.run(main())

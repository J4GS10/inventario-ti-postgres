"""QA8 (UX): mide en el inventario real las situaciones que el sistema debería alertar proactivamente.

Solo lectura (GET). Usa la sesión guardada del navegador para no gastar códigos TOTP.
Uso (cwd=herramientas-qa): python qa8_datos.py
"""
import asyncio
import datetime as dt
import json
import os
from collections import Counter

from playwright.async_api import async_playwright

from qa8_comun import BASE, OUT, guardar, login, nuevo_contexto

API = f"{BASE}/api/v1"
BOX = {}


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        ctx = await nuevo_contexto(browser)
        page = await ctx.new_page()

        async def on_resp(resp):
            try:
                if ("/login/refresh" in resp.url or "/login/2fa/verify" in resp.url) and resp.ok:
                    d = await resp.json()
                    if d.get("access_token"):
                        BOX["token"] = d["access_token"]
            except Exception:
                pass
        page.on("response", on_resp)
        await login(page)
        await page.reload()
        await page.wait_for_timeout(2500)

        async def get(path):
            r = await page.request.get(f"{API}{path}", headers={"Authorization": f"Bearer {BOX['token']}"})
            return await r.json()

        hoy = dt.date.today()
        async def todo(path):
            out, skip = [], 0
            while True:
                lote = await get(f"{path}?skip={skip}&limit=200")
                if not isinstance(lote, list) or not lote:
                    return out
                out += lote
                if len(lote) < 200:
                    return out
                skip += 200
        activos = await todo("/core/activos")
        estados = {e["EOP_Estado_Operativo"]: e["EOP_Nombre"] for e in await get("/cat/estados-operativos")}
        movs = await todo("/trazabilidad/movimientos")
        vigentes = {m["ACT_Activo"] for m in movs if not m.get("MOV_Fecha_Devolucion")}
        mants = None
        # mantenimientos: probar rutas conocidas
        for path in ("/mantenimiento/",):
            r = await page.request.get(f"{API}{path}", headers={"Authorization": f"Bearer {BOX['token']}"})
            if r.ok:
                mants = await r.json()
                break
        consum = await get("/consumibles")
        personas = await get("/org/personas")
        stats = await get("/stats/dashboard")

        est = Counter(estados.get(a["EOP_Estado_Operativo"]) for a in activos)
        asignado_sin_mov = [a["ACT_Codigo_Interno"] for a in activos
                            if estados.get(a["EOP_Estado_Operativo"]) == "Asignado" and a["ACT_Activo"] not in vigentes]
        disponible_con_mov = [a["ACT_Codigo_Interno"] for a in activos
                              if estados.get(a["EOP_Estado_Operativo"]) in ("Disponible", "En Bodega") and a["ACT_Activo"] in vigentes]
        sin_garantia = sum(1 for a in activos if not a.get("ACT_Fin_Garantia"))
        sin_costo = sum(1 for a in activos if a.get("ACT_Costo") in (None, 0, "0", "0.00"))
        sin_sede = sum(1 for a in activos if not a.get("SED_Sede"))
        sin_host = sum(1 for a in activos if not a.get("ACT_Hostname"))
        gar_vencida_asignada = sum(1 for a in activos if a.get("ACT_Fin_Garantia") and a["ACT_Fin_Garantia"] < hoy.isoformat()
                                   and a["ACT_Activo"] in vigentes)
        inactivos = {p["PER_Persona"] for p in personas if not p.get("PER_Estado", True)}
        inactivos_con_activos = len({m["PER_Persona"] for m in movs if not m.get("MOV_Fecha_Devolucion") and m.get("PER_Persona") in inactivos})
        abiertos = []
        if isinstance(mants, list):
            for m in mants:
                if not m.get("MAN_Fecha_Cierre") and m.get("MAN_Fecha_Ingreso"):
                    d = (hoy - dt.date.fromisoformat(m["MAN_Fecha_Ingreso"][:10])).days
                    abiertos.append(d)
        bajo = [c["CON_Nombre"] for c in consum if c.get("bajo_stock")] if isinstance(consum, list) else consum
        res = {
            "activos": len(activos), "por_estado": dict(est), "movimientos_vigentes": len(vigentes),
            "estado_Asignado_sin_movimiento_vigente": {"n": len(asignado_sin_mov), "ejemplos": asignado_sin_mov[:5]},
            "estado_Disponible_con_movimiento_vigente": {"n": len(disponible_con_mov), "ejemplos": disponible_con_mov[:5]},
            "sin_fin_garantia": sin_garantia, "sin_costo": sin_costo, "sin_sede": sin_sede, "sin_hostname": sin_host,
            "asignados_con_garantia_vencida": gar_vencida_asignada,
            "personas_inactivas_con_activos": inactivos_con_activos,
            "mantenimientos_abiertos": len(abiertos), "abiertos_mas_30_dias": sum(1 for d in abiertos if d > 30),
            "max_dias_abierto": max(abiertos) if abiertos else None,
            "consumibles_bajo_minimo": bajo,
            "dashboard": {k: stats.get(k) for k in ("garantias_por_vencer_total", "garantias_vencidas", "mantenimientos_abiertos",
                                                   "disponibles_bodega", "activos_asignados", "activos_stock")},
        }
        print(json.dumps(res, ensure_ascii=False, indent=1))
        with open(os.path.join(OUT, "datos_situaciones.json"), "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
        await guardar(ctx)
        await browser.close()


asyncio.run(main())

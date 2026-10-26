"""
QA2 — Offboarding parcial por alcance de sede (ADMIN_TI limitado a la sede 1).
Reutiliza la sesión de sa guardada por qa2_negocio.py (salidas/qa2/.sesion_sa.json)
y agrega sus casos a salidas/qa2/resultados.json.
Uso (cwd = herramientas-qa): PYTHONIOENCODING=utf-8 python qa2_offboarding_alcance.py
"""
import os
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
src = open(os.path.join(AQUI, "qa2_negocio.py"), encoding="utf-8").read()
# Cabecera (sesión, utilidades) + setup, sin las secciones de pruebas generales.
cabecera = src[:src.index("# ═════════════════════ 0. DETALLE DE ACTIVO")]
offb = src[src.index("# Offboarding parcial por alcance"):src.index("s, b = asignar(o1[\"ACT_Activo\"], P3")]
final = src[src.index("with open(os.path.join(OUT, \"resultados.json\")"):]
sys.path.insert(0, AQUI)
g = {"__name__": "qa2_off", "__file__": os.path.join(AQUI, "qa2_negocio.py")}
exec(compile(cabecera, "qa2_cabecera", "exec"), g)
exec(compile(offb, "qa2_offboarding", "exec"), g)
import json
ruta = os.path.join(g["OUT"], "resultados.json")
prev = json.load(open(ruta, encoding="utf-8"))
ids = {r["id"] for r in g["RESULTADOS"]}
prev["casos"] = [r for r in prev["casos"] if r["id"] not in ids] + g["RESULTADOS"]
json.dump(prev, open(ruta, "w", encoding="utf-8"), ensure_ascii=False, indent=2, default=str)
print("casos agregados:", sorted(ids))

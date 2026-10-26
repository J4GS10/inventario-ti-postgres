"""QA3 — Activo asignado sin mantenimiento por sede (para transferir/devolver). Usa la sesión compartida de sa."""
import json, os, warnings
warnings.filterwarnings("ignore")
from qa_login import BASE
from qa3_comun import sa_cliente
F = os.path.join(os.path.dirname(os.path.abspath(__file__)), "salidas", "qa3", "estado.json")
E = json.load(open(F, encoding="utf-8"))
sa = sa_cliente()
for letra, s in E["sedes"].items():
    if s.get("activo3"):
        continue
    a = sa.post(f"{BASE}/core/activos", json={"ACT_Serie_Fabricante": f"QA3-{letra}-{E['suf']}-C", "ACT_Fecha_Compra": "2025-01-10",
                "MOD_Modelo": 3, "TAC_Tipo_Activo": 1, "EOP_Estado_Operativo": 1, "SED_Sede": s["sede"]}).json()
    m = sa.post(f"{BASE}/trazabilidad/movimientos", json={"ACT_Activo": a["ACT_Activo"], "PER_Persona": s["persona"],
                "ARE_Area": s["area"], "TMO_Tipo_Movimiento": 2})
    print(letra, a["ACT_Codigo_Interno"], m.status_code)
    s["activo3"], s["codigo3"], s["movimiento3"] = a["ACT_Activo"], a["ACT_Codigo_Interno"], m.json()["MOV_Movimiento"]
json.dump(E, open(F, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

"""QA5 - Conteos CSV vs base con otros agentes escribiendo a la vez.

Cuenta en la base justo antes y justo después del exporte (la réplica puede ir
unos segundos atrasada) y exige: antes-retraso <= filas CSV <= después, y que
todas las filas del CSV existan en la base.
"""
import csv
import io
import json
import os
import time

from qa_login import BASE
from qa5_comun import OUT, cliente
from qa5_reportes import psql, get

SQL = {
    "activos": ('select count(*) from "INV_ACTIVO"', 'select "ACT_Codigo_Interno" from "INV_ACTIVO"', 0),
    "movimientos": ('select count(*) from "INV_MOVIMIENTO"', None, None),
    "consumibles": ('select count(*) from "INV_CONSUMIBLE"', None, None),
    "proveedores": ('select count(*) from "INV_PROVEEDOR"', 'select "PRV_Nombre" from "INV_PROVEEDOR"', 0),
    "ordenes": ('select count(*) from "INV_ORDEN_COMPRA"', 'select "OCO_Numero" from "INV_ORDEN_COMPRA"', 0),
}
RES = []
c = cliente()
for name, (cnt, keys, col) in SQL.items():
    antes = int(psql(cnt))
    time.sleep(4)  # margen para que la réplica alcance el estado "antes"
    r = get(c, f"{BASE}/export/{name}.csv", params={"lang": "es"})
    despues = int(psql(cnt))
    rows = list(csv.reader(io.StringIO(r.content.decode("utf-8-sig"))))[1:]
    n = len(rows)
    extra = []
    if keys:
        db = set(psql(keys).split("\n"))
        extra = [rw[col] for rw in rows if rw[col] not in db and rw[col].lstrip("'") not in db]
    ok = antes <= n <= despues and not extra
    RES.append({"id": f"CSV-CNT-{name[:4].upper()}", "caso": f"Conteo del CSV {name} coherente con la base (usuario global)",
                "obtenido": f"base antes {antes}, CSV {n}, base después {despues}; filas inexistentes en BD: {extra[:3]}",
                "estado": "OK" if ok else "DEFECTO"})
    print(RES[-1])
with open(os.path.join(OUT, "resultados_conteos.json"), "w", encoding="utf-8") as f:
    json.dump(RES, f, ensure_ascii=False, indent=1)

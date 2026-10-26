"""Genera salidas/qa5_informe.md a partir de los resultados JSON de QA5 y de la sección manual de defectos."""
import json
import os

AQUI = os.path.dirname(os.path.abspath(__file__))
Q = os.path.join(AQUI, "salidas", "qa5")


def cargar(n):
    with open(os.path.join(Q, n), encoding="utf-8") as f:
        return json.load(f)


api = cargar("resultados_api.json")
cnt = {r["id"]: r for r in cargar("resultados_conteos.json")}
ui = [r for r in cargar("resultados_ui.json") if r["id"] != "UI-QR"] + cargar("resultados_ui_qr.json")

# Los conteos del run principal chocaron con otros agentes escribiendo a la vez: se usan los del script dedicado.
filas = []
for r in api:
    if r["id"] in cnt:
        c = cnt.pop(r["id"])
        r = dict(r, obtenido=c["obtenido"] + " (qa5_conteos.py: conteo antes/después del exporte)", estado=c["estado"],
                 severidad="" if c["estado"] == "OK" else "alta", evidencia=r["evidencia"])
    filas.append(r)
filas += ui

PASOS = [
    ("CSV-CNT", "Contar en BD antes y después; GET /export/<x>.csv?lang=es como sa", "Filas CSV entre ambos conteos y todas existentes en BD"),
    ("CSV-3L", "Descargar el CSV en es/en/it", "Mismo número de filas"),
    ("CSV-AUD-GOV", "GET /gov/auditoria.csv (ruta de la UI)", "200, CSV con BOM"),
    ("CSV-AUD-ENT", "Abrir auditoría CSV, columna Entidad", "Nombre legible y traducido"),
    ("CSV-AUD-ACC", "Abrir auditoría CSV, columna Acción", "Todas las acciones traducidas"),
    ("CSV-INJ", "Crear datos QA5 con =, +, -, @ y exportar", "Celda prefijada con ' (texto)"),
    ("CSV-ACT", "Exportar activos (ES) y buscar los activos QA5", "Valores coherentes con lo registrado"),
    ("CSV-MOV", "Exportar movimientos y buscar los de QA5", "Nombre/observación íntegros, tipo traducido, devolución ISO"),
    ("CSV-CON", "Exportar consumibles en el idioma", "Unidad y Sí/No traducidos, inyección neutralizada"),
    ("CSV-PRV", "Exportar proveedores (ES)", "Inyección neutralizada, ñ íntegra"),
    ("CSV-ORD", "Exportar órdenes en el idioma", "Fecha ISO, estado traducido, moneda y total"),
    ("CSV-", "GET /export/<x>.csv?lang=<es|en|it> como sa; abrir el archivo", "BOM UTF-8, encabezados traducidos con tildes, nombre <base>_AAAAMMDD_HHMMSS.csv, fechas ISO, sin fórmulas, catálogos en el idioma"),
    ("QR-RES", "GET /core/activos/by-code/<código leído del QR>", "Devuelve el mismo ACT_Activo"),
    ("QR-", "POST /core/activos/etiquetas.pdf; renderizar el PDF y decodificar los QR (OpenCV)", "QR = código interno, textos traducidos, 30 por hoja"),
    ("ACT-D-FECHA", "Devolver un activo y generar la hoja de descargo", "Fecha = fecha de devolución"),
    ("ACT-E-FECHA", "Generar el acta de entrega de una asignación del 2026-06-08", "El acta lleva la fecha de la asignación"),
    ("ACT-DESC-ABIERTO", "GET /trazabilidad/acta/<mov abierto>?tipo=descargo", "Rechazo (400/409) o advertencia"),
    ("ACT-DUP", "POST /acta/lote con el mismo id dos veces", "Una sola fila por activo"),
    ("ACT-MIX", "POST /acta/lote con movimientos de dos personas", "400 CANNOT_MIX_…"),
    ("ACT-", "GET /trazabilidad/acta/{id} y POST /acta/lote (docx/pdf, es/en/it, entrega/descargo con mensajero); abrir con python-docx/pdfplumber", "Textos del idioma, sin restos en español, datos del activo y colaborador, fecha larga localizada, tono formal, nombre de archivo localizado"),
    ("SC-", "Usuarios QA5 (ADMIN_TI y AUDITOR) con alcance solo en la sede QA5 exportan/generan documentos", "Solo datos de su sede; lo ajeno → 404/403"),
    ("UI-", "Playwright: iniciar sesión (ADMIN_TI/AUDITOR QA5), usar el botón Exportar / acciones de Movimientos / Imprimir etiquetas", "Descarga válida, idioma elegido, nombre localizado como el del servidor"),
]


def pasos(cid):
    for pref, p, e in PASOS:
        if cid.startswith(pref):
            return p, e
    return "", ""


def esc(s):
    return str(s).replace("|", "\\|").replace("\n", " ")


tabla = ["| ID | Caso | Pasos | Esperado | Obtenido | Evidencia | Severidad | Estado |",
         "|---|---|---|---|---|---|---|---|"]
for r in filas:
    p, e = pasos(r["id"])
    ev = r.get("evidencia") or ""
    ev = ev.replace("\\", "/").replace("../", "").replace("salidas/", "")
    tabla.append(f"| {r['id']} | {esc(r['caso'])} | {esc(p)} | {esc(e)} | {esc(r['obtenido'])} | {esc(ev)} | "
                 f"{r.get('severidad') or '—'} | {r['estado']} |")

tot = len(filas)
ok = sum(r["estado"] == "OK" for r in filas)
de = sum(r["estado"] == "DEFECTO" for r in filas)
ob = sum(r["estado"] == "OBSERVACIÓN" for r in filas)

with open(os.path.join(Q, "informe_cabecera.md"), encoding="utf-8") as f:
    cabecera = f.read()
with open(os.path.join(Q, "informe_defectos.md"), encoding="utf-8") as f:
    defectos = f.read()

md = cabecera.replace("{TOTAL}", str(tot)).replace("{OK}", str(ok)).replace("{DEF}", str(de)).replace("{OBS}", str(ob))
md += "\n## 2. Casos ejecutados\n\n" + "\n".join(tabla) + "\n\n" + defectos
with open(os.path.join(Q, "..", "qa5_informe.md"), "w", encoding="utf-8") as f:
    f.write(md)
print(tot, ok, de, ob)

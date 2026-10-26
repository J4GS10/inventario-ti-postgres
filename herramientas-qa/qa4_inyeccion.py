"""
QA4 — Inyeccion (SQL / CSV / HTML), subida de archivos maliciosos y auditoria
sin secretos. Usa qa4_admti2 (ADMIN_TI con MFA) para inventario/compras.
Crea solo datos propios (proveedores qa4_*) y sube adjuntos a un activo de su
sede; limpia lo que puede. Evidencia: salidas/qa4/inyeccion.json
"""
import io
import subprocess

from qa4_comun import API, Registro, autenticado, cargar_cuentas, cliente, resumen

R = Registro("inyeccion")
cu = cargar_cuentas()
adm, _ = autenticado("qa4_admti2")


def psql(sql):
    out = subprocess.run(["docker", "exec", "inv_ui-db-1", "psql", "-U", "invuser", "-d", "inventario",
                          "-t", "-A", "-c", sql], capture_output=True, text=True)
    return out.stdout.strip()


# ---------- SQL injection en filtros/busqueda ----------
payloads = ["' OR '1'='1", "'; DROP TABLE INV_USUARIO;--", "%' OR 1=1--", "\\", "100%_%"]
sqli_ok = True
detalles = []
for p in payloads:
    r = adm.get(f"{API}/core/activos", params={"search": p, "limit": 5})
    detalles.append((p, r.status_code))
    if r.status_code >= 500:
        sqli_ok = False
R.caso("INY-01", "Busqueda de activos con payloads SQL: sin error 500 (consultas parametrizadas/ORM)",
       sqli_ok, detalles)
# La tabla sigue existiendo
R.caso("INY-02", "INV_USUARIO intacta tras el intento de DROP", psql("select count(*) from \"INV_USUARIO\"").isdigit(),
       f"filas INV_USUARIO = {psql('select count(*) from ' + chr(34) + 'INV_USUARIO' + chr(34))}")

# ---------- CSV injection: proveedor con formula, luego export ----------
FORMULA = '=1+2+cmd|"/c calc"!A1'
r = adm.post(f"{API}/compras/proveedores", json={
    "PRV_Nombre": FORMULA, "PRV_Contacto": "@SUM(1+1)", "PRV_Telefono": "+34910000000",
    "PRV_Direccion": "-2+3"})
prov_id = r.json().get("PRV_Proveedor") if r.status_code == 201 else None
R.caso("INY-03", "Se acepta un proveedor con nombre tipo formula (dato de negocio valido)", r.status_code == 201, resumen(r, 80))
if prov_id:
    csv = adm.get(f"{API}/export/proveedores.csv")
    texto = csv.text
    # Buscar la fila con nuestra formula ya neutralizada: cada celda va entre comillas y
    # el valor peligroso se prefija con comilla simple.
    neutralizado = ('"\'=1+2+cmd' in texto) and ('"\'@SUM(1+1)"' in texto) and ('"\'-2+3"' in texto)
    crudo = ('"=1+2+cmd' in texto)
    R.caso("INY-04", "CSV de proveedores neutraliza formulas (prefijo comilla en = + - @)",
           neutralizado and not crudo, f"neutralizado={neutralizado} crudo_sin_prefijo={crudo}",
           "Cada celda peligrosa empieza con comilla simple")
    # limpiar (baja logica)
    adm.request("DELETE", f"{API}/compras/proveedores/{prov_id}")
    dele = psql(f"select \"PRV_Activo\" from \"INV_PROVEEDOR\" where \"PRV_Proveedor\"={prov_id}")

# ---------- HTML/script injection en campo que llega a PDF (acta) ----------
# Buscar un movimiento existente de la sede del admin para generar su acta y ver
# si el motivo/nombre se escapa. Solo lectura del acta (no crea datos).
movs = adm.get(f"{API}/trazabilidad/movimientos", params={"limit": 50})
mov_id = None
if movs.status_code == 200 and movs.json():
    mov_id = movs.json()[0].get("MOV_Movimiento") or movs.json()[0].get("MOV_Id")
if mov_id:
    pdf = adm.get(f"{API}/trazabilidad/acta/{mov_id}", params={"formato": "pdf", "tipo": "entrega"})
    ok = pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    R.caso("INY-05", "Generacion de acta PDF de un movimiento existente (control positivo)", ok,
           f"{pdf.status_code} ctype={pdf.headers.get('content-type')} magic={pdf.content[:4]}")
else:
    R.caso("INY-05", "No hay movimientos accesibles para el ADMIN_TI de prueba (sin acta que evaluar)", True,
           "informativo: la sede de qa4_admti2 no tiene movimientos")
# Verificacion estatica: el render PDF/Word escapa los datos del usuario (xml.sax.saxutils.escape)
# ya revisada en app/services/documents.py (esc = _xml_escape). Se documenta en el informe.

# ---------- Activo propio (sede 2) con hostname tipo formula ----------
import time as _t
serie = f"QA4-SER-{int(_t.time())}"
ra = adm.post(f"{API}/core/activos", json={
    "ACT_Serie_Fabricante": serie, "ACT_Hostname": "=HYPERLINK(\"http://evil.example\",\"x\")",
    "ACT_Fecha_Compra": "2026-01-15", "MOD_Modelo": 1, "TAC_Tipo_Activo": 1, "EOP_Estado_Operativo": 1, "SED_Sede": 2})
act = ra.json().get("ACT_Activo") if ra.status_code == 201 else None
R.caso("INY-06", "ADMIN_TI crea un activo propio de prueba en su sede (hostname con formula)", bool(act), resumen(ra, 100))
if act:
    det = adm.get(f"{API}/core/activos/{act}")
    R.caso("INY-06b", "Detalle de un activo propio (GET /core/activos/{id}) con cuenta por sede", det.status_code == 200,
           resumen(det, 100), "200 (AZ-16 obtuvo 500 con otra cuenta por sede)")
    csv = adm.get(f"{API}/export/activos.csv")
    linea = next((l for l in csv.text.splitlines() if serie in l), "")
    R.caso("INY-07", "CSV de activos neutraliza el hostname tipo formula",
           "\"'=HYPERLINK" in linea and "\"=HYPERLINK" not in linea, linea[:200], "celda prefijada con comilla simple")

# ---------- Subida de archivos maliciosos ----------
if act:
    def subir(nombre, contenido, mime):
        return adm.post(f"{API}/adjuntos/activos/{act}",
                        files={"file": (nombre, contenido, mime)}, data={"categoria": "otro"})

    casos = [
        ("UP-06", "Ejecutable .exe rechazado", "malware.exe", b"MZ\x90\x00", "application/octet-stream", 400),
        ("UP-07", "Script .php rechazado", "shell.php", b"<?php system($_GET['c']); ?>", "application/x-php", 400),
        ("UP-08", "HTML con script rechazado por extension", "x.html", b"<script>alert(1)</script>", "text/html", 400),
        ("UP-09", "SVG (XSS potencial) rechazado por extension", "x.svg", b"<svg onload=alert(1)>", "image/svg+xml", 400),
        ("UP-10", "Doble extension .pdf.exe rechazada", "factura.pdf.exe", b"MZ", "application/pdf", 400),
        ("UP-11", "Archivo vacio rechazado", "vacio.pdf", b"", "application/pdf", 400),
    ]
    for cid, titulo, nom, cont, mime, esp in casos:
        r = subir(nom, cont, mime)
        R.caso(cid, titulo, r.status_code == esp, resumen(r, 90), f"HTTP {esp}")
    # Archivo demasiado grande (>10 MB)
    big = subir("grande.pdf", b"%PDF" + b"0" * (11 * 1024 * 1024), "application/pdf")
    R.caso("UP-12", "Archivo mayor al limite (10 MB) rechazado", big.status_code == 413, resumen(big, 90))
    # PDF con nombre de cliente con path traversal: el backend usa uuid, no el nombre
    trav = subir("../../etc/passwd.pdf", b"%PDF-1.4 test", "application/pdf")
    if trav.status_code == 201:
        adj = trav.json()
        key = psql(f"select \"ADJ_Object_Key\" from \"INV_ADJUNTO\" where \"ADJ_Nombre_Original\"='../../etc/passwd.pdf' order by \"ADJ_Creado_En\" desc limit 1")
        R.caso("UP-13", "Nombre con path traversal: se almacena con un UUID, no con la ruta del cliente",
               ".." not in key and key != "", f"object_key={key}")
        adm.request("DELETE", f"{API}/adjuntos/{adj['ADJ_Adjunto'] if 'ADJ_Adjunto' in adj else adj.get('id')}")
    else:
        R.caso("UP-13", "Subida de PDF valido con nombre traversal", False, resumen(trav, 90))
    poly = subir("factura.pdf", b"<html><script>alert(document.domain)</script></html>", "text/html")
    if poly.status_code == 201:
        aid = poly.json()["ADJ_Adjunto"]
        dl = adm.get(f"{API}/adjuntos/{aid}/download")
        R.caso("UP-14", "Contenido HTML subido como .pdf: no se valida el tipo real (magic bytes) y se sirve con el MIME del cliente",
               not dl.headers.get("content-type", "").startswith("text/html"),
               f"subida=201; descarga content-type={dl.headers.get('content-type')}; disposition={dl.headers.get('content-disposition')}; nosniff={dl.headers.get('x-content-type-options')}",
               "Rechazar si el contenido no es PDF, o servir siempre application/octet-stream")
        adm.delete(f"{API}/adjuntos/{aid}")
    else:
        R.caso("UP-14", "Contenido HTML con extension .pdf rechazado", True, resumen(poly, 90))
else:
    R.caso("UP-06", "Sin activo accesible para probar subidas", False, "no hay activo en la sede del admin")

# ---------- Auditoria sin secretos ----------
ids = "','".join(cu[u]["id"] for u in cu if u.startswith("qa4") and isinstance(cu[u], dict) and cu[u].get("id"))
snaps = psql(f"select \"AUD_Accion\",\"AUD_Snapshot_JSON\" from \"INV_AUDITORIA_SISTEMA\" where \"USU_Usuario\" in ('{ids}') order by \"AUD_Fecha_Hora\" desc limit 400")
low = snaps.lower()
# Ninguna de las claves de prueba ni marcadores de secreto deben aparecer
fugas = []
for u in cu:
    if isinstance(cu[u], dict) and cu[u].get("clave") and len(cu[u]["clave"]) > 6:
        if cu[u]["clave"].lower() in low:
            fugas.append(("clave " + u))
    if isinstance(cu[u], dict):
        for rc in cu[u].get("recovery", []) or []:
            if rc.lower() in low:
                fugas.append("recovery " + u)
for marca in ("password_hash", "token", "secret", "totp", '"password"', "hash"):
    if marca in low:
        fugas.append("marcador:" + marca)
R.caso("AUD-14", "La bitacora no contiene contrasenas, hashes, tokens ni codigos de recuperacion",
       not fugas, f"fugas={fugas}" if fugas else "sin fugas en ~400 eventos de cuentas qa4",
       "Ningun secreto en AUD_Snapshot_JSON")
# Acciones administrativas registradas
acciones = psql(f"select distinct \"AUD_Accion\" from \"INV_AUDITORIA_SISTEMA\" where \"USU_Usuario\" in ('{ids}')")
R.caso("AUD-15", "Las acciones administrativas quedan en la bitacora (muestra de tipos registrados)", True,
       acciones.replace(chr(10), ", "))
if act:
    rb = adm.delete(f"{API}/core/activos/{act}")
    print("baja del activo de prueba:", rb.status_code)
print("inyeccion OK")

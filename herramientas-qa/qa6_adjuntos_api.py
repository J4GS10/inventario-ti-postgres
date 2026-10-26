"""
QA6 — Adjuntos y MinIO por API (tras qa6_setup.py).

Subida/descarga con SHA-256, objeto en el bucket, límites de tamaño y tipo,
nombres peligrosos, borrado, alcance por sede y permisos por rol.
Resultados en salidas/qa6/api.json; ids para la prueba de persistencia en estado.json.
"""
import os
import time
import uuid

import httpx

from qa6_comun import (OUT, Registro, cargar_estado, cliente_sa, guardar_estado, jpg_bytes, mc,
                       objeto_en_bucket, pdf_bytes, png_bytes, sha, BUCKET)
from qa_login import BASE, login

R = Registro("api")
e = cargar_estado()
c = cliente_sa()
ACT, ORD, ACT_SUR = e["activo"], e["orden"], e["activo_sur"]
U_ACT, U_ORD = f"{BASE}/adjuntos/activos/{ACT}", f"{BASE}/adjuntos/ordenes/{ORD}"
_ult = [0.0]


def subir(cli, url, nombre, contenido, mime="application/octet-stream", categoria="otro", desc=None):
    # Límite de 30 subidas/min por IP compartido con otros agentes: se espacian.
    espera = 2.5 - (time.time() - _ult[0])
    if espera > 0:
        time.sleep(espera)
    _ult[0] = time.time()
    data = {"categoria": categoria}
    if desc:
        data["descripcion"] = desc
    return cli.post(url, files={"file": (nombre, contenido, mime)}, data=data, timeout=120)


def detalle(r):
    try:
        return r.json().get("detail")
    except Exception:  # noqa: BLE001
        return r.text[:160]


persist = e.setdefault("persistencia", [])


def ciclo(cid, caso, url, nombre, contenido, mime, categoria):
    """Sube, lista, descarga (SHA-256) y confirma en MinIO."""
    r = subir(c, url, nombre, contenido, mime, categoria, f"QA6 {caso}")
    if r.status_code != 201:
        R.caso(cid, caso, False, f"HTTP {r.status_code} {detalle(r)}", "201 y descarga idéntica")
        return None
    a = r.json()
    lista = c.get(url).json()
    en_lista = any(x["ADJ_Adjunto"] == a["ADJ_Adjunto"] for x in lista)
    d = c.get(f"{BASE}/adjuntos/{a['ADJ_Adjunto']}/download")
    existe, sha_b, _ = objeto_en_bucket(a["ADJ_Object_Key"])
    h = sha(contenido)
    ok = (en_lista and d.status_code == 200 and sha(d.content) == h and existe and sha_b == h
          and a["ADJ_Checksum_SHA256"] == h and a["ADJ_Storage_Backend"] == "s3" and a["ADJ_Bucket"] == BUCKET)
    R.caso(cid, caso, ok,
           f"201; en lista={en_lista}; descarga {d.status_code} sha={'=' if sha(d.content) == h else '≠'}; "
           f"MinIO {a['ADJ_Bucket']}/{a['ADJ_Object_Key']} existe={existe} sha={'=' if sha_b == h else '≠'}; "
           f"checksum BD={'=' if a['ADJ_Checksum_SHA256'] == h else '≠'}; Content-Type={d.headers.get('content-type')}; "
           f"Content-Disposition={d.headers.get('content-disposition')}",
           "201, aparece en la lista, descarga con el mismo SHA-256 y objeto en el bucket")
    persist.append({"id": a["ADJ_Adjunto"], "key": a["ADJ_Object_Key"], "sha": h, "caso": cid})
    guardar_estado(e)
    return a


# ── A. Subida y descarga íntegra ──────────────────────────────────────────
SALTAR = set(os.environ.get("QA6_SALTAR", "").split(","))  # p. ej. "A,B" si ya se ejecutaron
if "A" in SALTAR:
    por = {p["caso"]: p for p in persist}
    a_pdf = {"ADJ_Adjunto": por["A01"]["id"], "ADJ_Object_Key": por["A01"]["key"]}
    o_pdf = {"ADJ_Adjunto": por["A04"]["id"], "ADJ_Object_Key": por["A04"]["key"]}
    SHA_PDF = por["A01"]["sha"]
    pdf = pdf_bytes(f"QA6 acta {time.time()}")
    fac = pdf_bytes(f"QA6 FACTURA {time.time()}")
else:
    pdf = pdf_bytes(f"QA6 acta {time.time()}")
    a_pdf = ciclo("A01", "PDF (acta) en activo", U_ACT, "QA6-acta.pdf", pdf, "application/pdf", "acta")
    a_png = ciclo("A02", "PNG (foto) en activo", U_ACT, "QA6-foto.png", png_bytes(), "image/png", "foto")
    a_jpg = ciclo("A03", "JPG (foto) en activo", U_ACT, "QA6-foto.jpg", jpg_bytes(), "image/jpeg", "foto")
    fac = pdf_bytes(f"QA6 FACTURA {time.time()}")
    o_pdf = ciclo("A04", "Factura PDF en orden de compra", U_ORD, "QA6-factura.pdf", fac, "application/pdf", "factura")
    o_jpg = ciclo("A05", "Factura escaneada JPG en orden", U_ORD, "QA6-factura-scan.jpg", jpg_bytes(), "image/jpeg", "factura")
    SHA_PDF = sha(pdf)

# Metadatos del objeto en MinIO (ContentType y sha256 de usuario).
if a_pdf and "A" not in SALTAR:
    _, stat = mc("stat", f"q6/{BUCKET}/{a_pdf['ADJ_Object_Key']}")
    with open(os.path.join(OUT, "A06_minio_stat.txt"), "w", encoding="utf-8") as f:
        f.write(stat)
    R.caso("A06", "Metadatos del objeto en MinIO", "application/pdf" in stat and sha(pdf) in stat,
           " | ".join(l.strip() for l in stat.splitlines() if l.strip())[:400],
           "Content-Type y metadato sha256 del objeto", "salidas/qa6/A06_minio_stat.txt")

# ── B. Límites de tamaño ──────────────────────────────────────────────────
MB = 1024 * 1024
B_CASOS = [] if "B" in SALTAR else None
base = pdf_bytes("QA6 grande")
exacto = base + b"%" + b"0" * (10 * MB - len(base) - 1)
r = subir(c, U_ACT, "QA6-10MB-exacto.pdf", exacto, "application/pdf") if "B" not in SALTAR else None
if r is not None:
  R.caso("B01", "Archivo de 10 MB exactos (límite)", r.status_code == 201, f"HTTP {r.status_code} {detalle(r)}",
       "201 (el límite es inclusivo)")
if r is not None and r.status_code == 201:
    c.delete(f"{BASE}/adjuntos/{r.json()['ADJ_Adjunto']}")
for cid, tam, esperado in (("B02", 10 * MB + 1, "413 FILE_TOO_LARGE_MAX_MB:10"),
                           ("B03", 15 * MB, "413 FILE_TOO_LARGE_MAX_MB:10"),
                           ("B04", 25 * MB, "413 con código de error JSON que la UI entienda")):
    if "B" in SALTAR and cid != "B04":
        continue
    try:
        r = subir(c, U_ACT, f"QA6-{tam // MB}MB.pdf", base + b"0" * (tam - len(base)), "application/pdf")
    except httpx.TransportError as exc:
        # nginx corta la conexión al superar client_max_body_size: se repite con curl para ver la respuesta.
        import subprocess
        tmp = os.path.join(OUT, "_25mb.pdf")
        open(tmp, "wb").write(base + b"0" * (tam - len(base)))
        out = subprocess.run(["curl", "-sSk", "-o", "B04_respuesta.html", "-w", "%{http_code} %{content_type}",
                              "-H", f"Authorization: {c.headers['Authorization']}", "-F", "file=@_25mb.pdf;type=application/pdf",
                              "-F", "categoria=otro", U_ACT], capture_output=True, text=True, cwd=OUT)
        out = out.stdout + out.stderr
        os.remove(tmp)
        cuerpo = open(os.path.join(OUT, "B04_respuesta.html"), encoding="utf-8", errors="replace").read()
        R.caso(cid, f"Archivo de {tam / MB:.2f} MB", False,
               f"httpx: {type(exc).__name__} (conexión cortada); curl: HTTP/content-type={out}; cuerpo={cuerpo[:100]!r}",
               esperado, "salidas/qa6/B04_respuesta.html")
        continue
    d = detalle(r)
    R.caso(cid, f"Archivo de {tam / MB:.2f} MB", r.status_code == 413 and str(d).startswith("FILE_TOO_LARGE"),
           f"HTTP {r.status_code}; content-type={r.headers.get('content-type')}; cuerpo={str(d)[:120]!r}", esperado)
r = subir(c, U_ACT, "QA6-vacio.pdf", b"", "application/pdf")
R.caso("B05", "Archivo vacío (0 bytes)", r.status_code == 400 and detalle(r) == "EMPTY_FILE",
       f"HTTP {r.status_code} {detalle(r)}", "400 EMPTY_FILE")

# ── C. Tipos: lista blanca y contenido ────────────────────────────────────
MZ = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff\x00\x00" + b"\x00" * 48 + b"This program cannot be run in DOS mode.\r\n" + b"\x00" * 200
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(document.domain)"><script>alert(1)</script></svg>'
HTML = b"<html><body><script>alert('QA6 XSS')</script></body></html>"
rechazos = [
    ("C01", "Ejecutable .exe", "QA6-setup.exe", MZ, "application/x-msdownload"),
    ("C02", "SVG con <script>", "QA6-logo.svg", SVG, "image/svg+xml"),
    ("C03", "Página .html", "QA6-pagina.html", HTML, "text/html"),
    ("C04", "Doble extensión factura.pdf.exe", "QA6-factura.pdf.exe", MZ, "application/pdf"),
    ("C05", "Sin extensión", "QA6-sinextension", pdf, "application/pdf"),
    ("C06", "Script .js", "QA6-x.js", b"alert(1)", "application/javascript"),
]
for cid, caso, nombre, cont, mime in rechazos:
    r = subir(c, U_ACT, nombre, cont, mime)
    R.caso(cid, caso, r.status_code == 400 and detalle(r) == "FILE_TYPE_NOT_ALLOWED",
           f"HTTP {r.status_code} {detalle(r)}", "400 FILE_TYPE_NOT_ALLOWED")

# Extensión permitida con contenido falso: el servidor no inspecciona el contenido.
falsos = [
    ("C07", "Ejecutable renombrado a .pdf (MZ)", "QA6-factura-falsa.pdf", MZ, "application/pdf"),
    ("C08", ".jpg con HTML/script y MIME text/html", "QA6-foto-falsa.jpg", HTML, "text/html"),
    ("C09", ".png con SVG+script y MIME image/svg+xml", "QA6-logo-falso.png", SVG, "image/svg+xml"),
    ("C10", ".pdf con MIME inventado", "QA6-mime.pdf", pdf, "application/x-qa6-inventado"),
]
for cid, caso, nombre, cont, mime in falsos:
    r = subir(c, U_ACT, nombre, cont, mime)
    if r.status_code == 201:
        a = r.json()
        d = c.get(f"{BASE}/adjuntos/{a['ADJ_Adjunto']}/download")
        R.caso(cid, caso, False,
               f"ACEPTADO 201 (ADJ_Tipo_MIME={a['ADJ_Tipo_MIME']!r}); al descargar Content-Type={d.headers.get('content-type')!r}, "
               f"nosniff={d.headers.get('x-content-type-options')!r}, disposition={d.headers.get('content-disposition')!r}",
               "400: el contenido (firma/magic bytes) no corresponde a la extensión; MIME derivado por el servidor")
        c.delete(f"{BASE}/adjuntos/{a['ADJ_Adjunto']}")
    else:
        R.caso(cid, caso, r.status_code == 400, f"HTTP {r.status_code} {detalle(r)}", "400")

r = subir(c, U_ACT, "QA6-MAYUS.PDF", pdf, "application/pdf")
R.caso("C11", "Extensión en mayúsculas .PDF", r.status_code == 201, f"HTTP {r.status_code}", "201 (se normaliza)")
if r.status_code == 201:
    c.delete(f"{BASE}/adjuntos/{r.json()['ADJ_Adjunto']}")

# ── D. Nombres de archivo peligrosos o especiales ─────────────────────────
nombres = [
    ("D01", "Ruta ../ (path traversal)", "../../../etc/QA6-passwd.pdf"),
    ("D02", "Ruta ..\\ de Windows", "..\\..\\Windows\\QA6-win.pdf"),
    ("D03", "Caracteres especiales y Unicode", "QA6 Factura ñandú №5 «año» #1 & 50% ; 😀.pdf"),
    ("D04", "Nombre con HTML (XSS almacenado)", "QA6-<img src=x onerror=alert(1)>.pdf"),
    ("D05", "Nombre de 300 caracteres", "QA6-" + "x" * 292 + ".pdf"),
    ("D06", "Nombre con comillas y punto y coma", 'QA6-a"; filename="evil.exe.pdf'),
]
for cid, caso, nombre in nombres:
    cont = pdf_bytes(f"{cid} {time.time()}")
    r = subir(c, U_ACT, nombre, cont, "application/pdf")
    if r.status_code != 201:
        R.caso(cid, caso, r.status_code == 400, f"HTTP {r.status_code} {detalle(r)}", "201 con nombre saneado, o 400")
        continue
    a = r.json()
    d = c.get(f"{BASE}/adjuntos/{a['ADJ_Adjunto']}/download")
    key = a["ADJ_Object_Key"]
    clave_segura = key.startswith(f"activos/{ACT}/") and ".." not in key and "\\" not in key and len(key.split("/")[-1]) == 36
    ok = clave_segura and d.status_code == 200 and sha(d.content) == sha(cont)
    obs = ""
    if cid in ("D01", "D02") and ("/" in a["ADJ_Nombre_Original"] or "\\" in a["ADJ_Nombre_Original"]):
        obs = " [el nombre original se guarda sin sanear]"
    if cid == "D05":
        obs = f" [nombre guardado: {len(a['ADJ_Nombre_Original'])} car., termina en {a['ADJ_Nombre_Original'][-6:]!r}]"
    R.caso(cid, caso, ok,
           f"201; clave MinIO={key}; ADJ_Nombre_Original={a['ADJ_Nombre_Original'][:80]!r}; descarga {d.status_code} "
           f"sha {'=' if sha(d.content) == sha(cont) else '≠'}; disposition={d.headers.get('content-disposition', '')[:140]!r}{obs}",
           "Clave de objeto derivada de un UUID dentro de activos/<id>/; descarga íntegra; nombre sin rutas")
    if cid in ("D03", "D04"):
        e[f"adj_{cid}"] = a["ADJ_Adjunto"]  # se dejan para verlos en la UI
    else:
        c.delete(f"{BASE}/adjuntos/{a['ADJ_Adjunto']}")
guardar_estado(e)

# ── E. Validaciones y errores ─────────────────────────────────────────────
r = subir(c, U_ACT, "QA6-cat.pdf", pdf, "application/pdf", categoria="malware")
R.caso("E01", "Categoría inválida", r.status_code == 400 and detalle(r) == "INVALID_ATTACHMENT_CATEGORY",
       f"HTTP {r.status_code} {detalle(r)}", "400 INVALID_ATTACHMENT_CATEGORY")
r = subir(c, f"{BASE}/adjuntos/activos/{uuid.uuid4()}", "QA6-x.pdf", pdf, "application/pdf")
R.caso("E02", "Subir a un activo inexistente", r.status_code == 404, f"HTTP {r.status_code} {detalle(r)}", "404 ASSET_NOT_FOUND")
r = subir(c, f"{BASE}/adjuntos/ordenes/999999", "QA6-x.pdf", pdf, "application/pdf", "factura")
R.caso("E03", "Subir a una orden inexistente", r.status_code == 404, f"HTTP {r.status_code} {detalle(r)}", "404 PURCHASE_ORDER_NOT_FOUND")
r = c.get(f"{BASE}/adjuntos/{uuid.uuid4()}/download")
R.caso("E04", "Descargar un adjunto inexistente", r.status_code == 404, f"HTTP {r.status_code} {detalle(r)}", "404 ATTACHMENT_NOT_FOUND")
if a_pdf:
    with httpx.Client(verify=False) as anon:
        r = anon.get(f"{BASE}/adjuntos/{a_pdf['ADJ_Adjunto']}/download")
    R.caso("E05", "Descargar sin sesión", r.status_code == 401, f"HTTP {r.status_code} {detalle(r)}", "401")
r = c.get(f"{BASE}/adjuntos/no-es-uuid/download")
R.caso("E06", "Id mal formado", r.status_code == 422, f"HTTP {r.status_code}", "422")

# ── F. Alcance por sede y permisos por rol ────────────────────────────────
cu = e["cuentas"]


def cli_de(k):
    x = httpx.Client(verify=False, timeout=60)
    x.headers["Authorization"] = f"Bearer {login(x, cu[k]['usuario'], cu[k]['clave'])['access_token']}"
    return x


sur, tec1, aud1 = cli_de("tecsur"), cli_de("tec1"), cli_de("aud1")
if a_pdf and o_pdf:
    r = sur.get(f"{BASE}/adjuntos/{a_pdf['ADJ_Adjunto']}/download")
    R.caso("F01", "Técnico de otra sede descarga el adjunto de un activo", r.status_code == 404 and b"%PDF" not in r.content,
           f"HTTP {r.status_code} {detalle(r)}", "404 (no existe para su alcance)")
    r = sur.get(f"{BASE}/adjuntos/{o_pdf['ADJ_Adjunto']}/download")
    R.caso("F02", "Técnico de otra sede descarga la factura de una orden", r.status_code == 404,
           f"HTTP {r.status_code} {detalle(r)}", "404")
    r1, r2 = sur.get(U_ACT), sur.get(U_ORD)
    R.caso("F03", "Técnico de otra sede lista adjuntos del activo y de la orden", r1.status_code == 404 and r2.status_code == 404,
           f"activo HTTP {r1.status_code} {detalle(r1)}; orden HTTP {r2.status_code} {detalle(r2)}", "404 en ambos")
    r = subir(sur, U_ACT, "QA6-intruso.pdf", pdf, "application/pdf")
    R.caso("F04", "Técnico de otra sede sube a un activo ajeno", r.status_code == 404, f"HTTP {r.status_code} {detalle(r)}", "404")
    r = sur.delete(f"{BASE}/adjuntos/{a_pdf['ADJ_Adjunto']}")
    R.caso("F05", "Técnico de otra sede intenta borrar", r.status_code in (403, 404), f"HTTP {r.status_code} {detalle(r)}", "403/404")
    r = tec1.get(f"{BASE}/adjuntos/{a_pdf['ADJ_Adjunto']}/download")
    R.caso("F06", "Técnico de la misma sede descarga (control positivo)", r.status_code == 200 and sha(r.content) == SHA_PDF,
           f"HTTP {r.status_code} sha {'=' if sha(r.content) == SHA_PDF else '≠'}", "200 e íntegro")
    r = subir(tec1, U_ACT, "QA6-tecnico.pdf", pdf_bytes("tec"), "application/pdf", "acta")
    R.caso("F07", "Técnico de la sede sube a un activo de su sede", r.status_code == 201, f"HTTP {r.status_code} {detalle(r)}", "201")
    tec_adj = r.json()["ADJ_Adjunto"] if r.status_code == 201 else None
    r = subir(tec1, U_ORD, "QA6-tec-factura.pdf", fac, "application/pdf", "factura")
    R.caso("F08", "Técnico sube a una orden de compra", r.status_code == 403, f"HTTP {r.status_code} {detalle(r)}", "403 (solo admin)")
    if tec_adj:
        r = tec1.delete(f"{BASE}/adjuntos/{tec_adj}")
        R.caso("F09", "Técnico borra un adjunto (incluso el suyo)", r.status_code == 403, f"HTTP {r.status_code} {detalle(r)}", "403 (solo admin)")
        c.delete(f"{BASE}/adjuntos/{tec_adj}")
    r = subir(tec1, f"{BASE}/adjuntos/activos/{ACT_SUR}", "QA6-x.pdf", pdf, "application/pdf")
    R.caso("F10", "Técnico de la sede 1 sube a un activo de la sede QA6", r.status_code == 404, f"HTTP {r.status_code} {detalle(r)}", "404")
    r = aud1.get(f"{BASE}/adjuntos/{o_pdf['ADJ_Adjunto']}/download")
    r2 = subir(aud1, U_ACT, "QA6-aud.pdf", pdf, "application/pdf")
    r3 = aud1.delete(f"{BASE}/adjuntos/{a_pdf['ADJ_Adjunto']}")
    R.caso("F11", "AUDITOR de la sede: descarga sí, sube y borra no",
           r.status_code == 200 and r2.status_code == 403 and r3.status_code == 403,
           f"descarga {r.status_code}; subida {r2.status_code}; borrado {r3.status_code}", "200 / 403 / 403")

# ── G. Borrado ────────────────────────────────────────────────────────────
cont = pdf_bytes(f"QA6 borrar {time.time()}")
r = subir(c, U_ACT, "QA6-borrar.pdf", cont, "application/pdf")
if r.status_code == 201:
    a = r.json()
    antes, _, _ = objeto_en_bucket(a["ADJ_Object_Key"])
    rd = c.delete(f"{BASE}/adjuntos/{a['ADJ_Adjunto']}")
    time.sleep(2)
    despues, _, _ = objeto_en_bucket(a["ADJ_Object_Key"])
    en_lista = any(x["ADJ_Adjunto"] == a["ADJ_Adjunto"] for x in c.get(U_ACT).json())
    dl = c.get(f"{BASE}/adjuntos/{a['ADJ_Adjunto']}/download")
    R.caso("G01", "Borrar adjunto: sale de la lista, de la BD y del bucket",
           rd.status_code == 204 and antes and not despues and not en_lista and dl.status_code == 404,
           f"DELETE {rd.status_code}; objeto antes={antes}, después={despues}; en lista={en_lista}; descarga {dl.status_code}",
           "204; objeto eliminado de MinIO; 404 al descargar")
    rd2 = c.delete(f"{BASE}/adjuntos/{a['ADJ_Adjunto']}")
    R.caso("G02", "Borrar dos veces el mismo adjunto", rd2.status_code == 404, f"HTTP {rd2.status_code} {detalle(rd2)}", "404")
    aud = c.get(f"{BASE}/gov/auditoria", params={"limit": 200}).json()
    items = aud.get("items", aud) if isinstance(aud, dict) else aud
    ev = [i for i in items if i.get("AUD_Entidad_Afectada") == "INV_ADJUNTO" and "QA6-borrar.pdf" in str(i)]
    acciones = sorted({i.get("AUD_Accion") for i in ev})
    R.caso("G03", "Bitácora de auditoría de subida y borrado", {"CREATE", "DELETE"} <= set(acciones),
           f"eventos INV_ADJUNTO con QA6-borrar.pdf: {acciones}", "CREATE y DELETE registrados")

# ── H. Configuración de MinIO ─────────────────────────────────────────────
_, anon = mc("anonymous", "get", f"q6/{BUCKET}")
_, ver = mc("version", "info", f"q6/{BUCKET}")
_, enc = mc("encrypt", "info", f"q6/{BUCKET}")
R.caso("H01", "Bucket privado (sin acceso anónimo)", "private" in anon, anon.strip(), "private")
R.caso("H02", "Versionado / cifrado del bucket", "un-versioned" not in ver and "not found" not in enc.lower(),
       f"{ver.strip()} | {enc.strip()[:120]}", "versionado o respaldo para recuperar borrados; cifrado en reposo")

guardar_estado(e)
print("\nCasos:", len(R.casos), "OK:", sum(x["ok"] for x in R.casos))

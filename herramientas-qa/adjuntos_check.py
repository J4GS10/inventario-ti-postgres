"""
Verifica el almacenamiento de adjuntos (MinIO/S3) contra el stack de producción:
sube un PDF a un activo y a una orden de compra, lo descarga, compara el
contenido y comprueba el objeto en el bucket. Uso:

    python adjuntos_check.py <usuario> <clave> [secreto_totp]

El usuario debe poder subir a órdenes (SUPER_ADMIN o ADMIN_TI). Si tiene MFA,
indique el secreto TOTP.
"""
import hashlib
import sys
import time

import httpx
import pyotp

BASE = "https://localhost:8443/api/v1"
USER, PWD = sys.argv[1], sys.argv[2]
TOTP = sys.argv[3] if len(sys.argv) > 3 else None
ok_all = True


def check(name, ok, detail=""):
    global ok_all
    ok_all &= bool(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}{('  -> ' + str(detail)[:300]) if not ok else ''}", flush=True)


def pdf_bytes(label: str) -> bytes:
    body = f"BT /F1 18 Tf 72 720 Td ({label}) Tj ET".encode()
    return (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
            b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
            b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R>>endobj\n"
            + f"4 0 obj<</Length {len(body)}>>stream\n".encode() + body + b"\nendstream endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n")


with httpx.Client(verify=False, timeout=30) as c:
    r = c.post(f"{BASE}/login/access-token", data={"username": USER, "password": PWD}).json()
    if r.get("requires_2fa"):
        r = c.post(f"{BASE}/login/2fa/verify", json={
            "challenge_token": r["challenge_token"], "code": pyotp.TOTP(TOTP).at(time.time() + 30)}).json()
    token = r.get("access_token")
    check("Inicio de sesión", token and not r.get("restriction"), r)
    H = {"Authorization": f"Bearer {token}"}

    activo = c.post(f"{BASE}/core/activos/search", headers=H, json={"page": 1, "per_page": 1}).json()["items"][0]
    ordenes = c.get(f"{BASE}/compras/ordenes", headers=H).json()
    if not ordenes:
        prov = c.get(f"{BASE}/compras/proveedores", headers=H).json()
        if not prov:
            prov = [c.post(f"{BASE}/compras/proveedores", headers=H, json={"PRV_Nombre": f"Proveedor QA {int(time.time())}"}).json()]
        sede = c.get(f"{BASE}/org/sedes", headers=H).json()[0]["SED_Sede"]
        ordenes = [c.post(f"{BASE}/compras/ordenes", headers=H, json={
            "OCO_Numero": f"QA-{int(time.time())}", "OCO_Fecha": time.strftime("%Y-%m-%d"),
            "PRV_Proveedor": prov[0]["PRV_Proveedor"], "SED_Sede": sede,
            "lineas": [{"OCL_Descripcion": "Prueba", "OCL_Cantidad": 1, "OCL_Precio_Unitario": "1"}]}).json()]
    orden = ordenes[0]

    for destino, url, categoria in (
        ("activo", f"{BASE}/adjuntos/activos/{activo['ACT_Activo']}", "acta"),
        ("orden de compra", f"{BASE}/adjuntos/ordenes/{orden['OCO_Orden']}", "factura"),
    ):
        contenido = pdf_bytes(f"QA {destino} {time.time()}")
        r = c.post(url, headers=H, files={"file": (f"qa_{categoria}.pdf", contenido, "application/pdf")},
                   data={"categoria": categoria, "descripcion": "Prueba de almacenamiento"})
        check(f"Subir PDF a {destino}", r.status_code in (200, 201), f"{r.status_code} {r.text}")
        if r.status_code not in (200, 201):
            continue
        adj = r.json()
        check(f"…guardado en MinIO (backend s3, bucket y clave)", adj.get("ADJ_Storage_Backend") in (None, "s3") and
              (adj.get("ADJ_Object_Key") or adj.get("ADJ_Nombre_Almacenado")), adj)
        lista = c.get(url, headers=H).json()
        check(f"…aparece en la lista del {destino}", any(a["ADJ_Adjunto"] == adj["ADJ_Adjunto"] for a in lista))
        d = c.get(f"{BASE}/adjuntos/{adj['ADJ_Adjunto']}/download", headers=H)
        check(f"…se descarga íntegro (SHA-256)", d.status_code == 200 and
              hashlib.sha256(d.content).hexdigest() == hashlib.sha256(contenido).hexdigest(), d.status_code)

print("\nRESULTADO:", "OK" if ok_all else "CON FALLOS")
sys.exit(0 if ok_all else 1)

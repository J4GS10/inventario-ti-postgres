"""
QA6 — utilidades comunes para las pruebas de adjuntos y MinIO.

- Estado persistente de los datos QA6 creados (salidas/qa6/estado.json).
- Acceso al bucket de MinIO vía `docker exec inv_ui-minio-1 mc ...`.
- Registro de resultados de casos (salidas/qa6/<fichero>.json).
"""
import hashlib
import json
import os
import subprocess
import time

AQUI = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(AQUI, "salidas", "qa6")
os.makedirs(OUT, exist_ok=True)
ESTADO = os.path.join(OUT, "estado.json")
BUCKET = "lombardi-adjuntos"
MINIO = "inv_ui-minio-1"


def _minio_creds():
    env = {}
    with open(os.path.join(AQUI, "revision.env"), encoding="utf-8") as f:
        for line in f:
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.strip().split("=", 1)
                env[k] = v
    return env["MINIO_ROOT_USER"], env["MINIO_ROOT_PASSWORD"]


def mc(*args: str, binary: bool = False):
    """Ejecuta mc dentro del contenedor de MinIO (alias `q6`)."""
    u, p = _minio_creds()
    cmd = " ".join(["mc", *[f"'{a}'" for a in args]])
    script = f"mc alias set q6 http://localhost:9000 {u} {p} >/dev/null 2>&1; {cmd}"
    env = dict(os.environ, MSYS_NO_PATHCONV="1")
    r = subprocess.run(["docker", "exec", MINIO, "sh", "-c", script], capture_output=True, env=env)
    if binary:
        return r.returncode, r.stdout
    return r.returncode, r.stdout.decode("utf-8", "replace") + r.stderr.decode("utf-8", "replace")


def objeto_en_bucket(key: str):
    """(existe, sha256 del contenido, stat texto)."""
    rc, stat = mc("stat", f"q6/{BUCKET}/{key}")
    if rc != 0:
        return False, None, stat
    rc2, data = mc("cat", f"q6/{BUCKET}/{key}", binary=True)
    return True, hashlib.sha256(data).hexdigest() if rc2 == 0 else None, stat


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def cargar_estado() -> dict:
    if os.path.exists(ESTADO):
        with open(ESTADO, encoding="utf-8") as f:
            return json.load(f)
    return {}


def guardar_estado(e: dict):
    with open(ESTADO, "w", encoding="utf-8") as f:
        json.dump(e, f, ensure_ascii=False, indent=1)


class Registro:
    def __init__(self, nombre: str):
        self.nombre = nombre
        self.casos = []
        self.ruta = os.path.join(OUT, f"{nombre}.json")
        if os.path.exists(self.ruta):
            with open(self.ruta, encoding="utf-8") as f:
                self.casos = [c for c in json.load(f)]

    def caso(self, cid, caso, ok, obtenido, esperado="", evidencia=""):
        self.casos = [c for c in self.casos if c["id"] != cid]
        self.casos.append({"id": cid, "caso": caso, "ok": bool(ok), "esperado": esperado,
                           "obtenido": str(obtenido)[:600], "evidencia": evidencia,
                           "hora": time.strftime("%H:%M:%S")})
        print(f"{'PASS' if ok else 'FAIL'}  {cid} {caso} -> {str(obtenido)[:250]}", flush=True)
        self.guardar()

    def guardar(self):
        with open(self.ruta, "w", encoding="utf-8") as f:
            json.dump(sorted(self.casos, key=lambda c: c["id"]), f, ensure_ascii=False, indent=1)


def pdf_bytes(label: str) -> bytes:
    body = f"BT /F1 18 Tf 72 720 Td ({label}) Tj ET".encode()
    return (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
            b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
            b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R>>endobj\n"
            + f"4 0 obj<</Length {len(body)}>>stream\n".encode() + body
            + b"\nendstream endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n")


def png_bytes() -> bytes:
    import struct
    import zlib

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    w = h = 16
    raw = b"".join(b"\x00" + bytes([200, 60, 60]) * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def jpg_bytes() -> bytes:
    # JPEG mínimo válido (1x1 gris) generado con Pillow si existe; si no, uno fijo.
    try:
        import io

        from PIL import Image
        b = io.BytesIO()
        Image.new("RGB", (32, 32), (40, 120, 200)).save(b, "JPEG")
        return b.getvalue()
    except Exception:  # noqa: BLE001
        import base64
        return base64.b64decode(
            "/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////wAALCAABAAEBAREA/8QAFAABAAAAAAAAAAAAAAAAAAAAA//EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AN//Z")



SESION_SA = os.path.join(OUT, ".sesion_sa.json")


def cliente_sa():
    """
    httpx.Client como sa reutilizando SIEMPRE la misma sesión: token guardado en
    salidas/qa6/.sesion_sa.json; si expiró se usa /login/refresh; solo si no hay
    sesión válida se hace UN login (serializado por qa_login, sin reintentos).
    """
    import httpx

    from qa_login import BASE, cliente_autenticado
    if os.path.exists(SESION_SA):
        with open(SESION_SA, encoding="utf-8") as f:
            s = json.load(f)
        c = httpx.Client(verify=False, timeout=60)
        c.headers["Authorization"] = f"Bearer {s['access']}"
        if c.get(f"{BASE}/me").status_code == 200:
            return c
        r = c.post(f"{BASE}/login/refresh", json={"refresh_token": s["refresh"]})
        if r.status_code == 200 and r.json().get("access_token"):
            s = {"access": r.json()["access_token"], "refresh": r.cookies.get("rtk") or s["refresh"]}
            _guardar_sesion(s)
            c.headers["Authorization"] = f"Bearer {s['access']}"
            return c
        print("  (sesión sa guardada inválida:", r.status_code, r.text[:120], ")")
        c.close()
    c = cliente_autenticado()
    _guardar_sesion({"access": c.headers["Authorization"].split()[1], "refresh": c.cookies.get("rtk")})
    return c


def _guardar_sesion(s):
    with open(SESION_SA, "w", encoding="utf-8") as f:
        json.dump(s, f)


def credenciales_sa():
    from qa_login import credenciales
    return credenciales()

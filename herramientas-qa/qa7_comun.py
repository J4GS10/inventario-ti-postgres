"""QA7: utilidades comunes (cliente, MailHog, psql sobre filas propias)."""
import json
import os
import subprocess
import time

import httpx

from qa_login import BASE, cliente_autenticado

AQUI = os.path.dirname(os.path.abspath(__file__))
SAL = os.path.join(AQUI, "salidas", "qa7")
os.makedirs(SAL, exist_ok=True)
MAILHOG = "http://localhost:18025"


def guardar(nombre: str, data) -> str:
    ruta = os.path.join(SAL, nombre)
    with open(ruta, "w", encoding="utf-8") as f:
        if isinstance(data, str):
            f.write(data)
        else:
            json.dump(data, f, ensure_ascii=False, indent=1, default=str)
    return ruta


def mh_mensajes() -> list[dict]:
    r = httpx.get(f"{MAILHOG}/api/v2/messages?limit=500", timeout=10)
    return r.json().get("items", [])


def mh_borrar() -> None:
    httpx.delete(f"{MAILHOG}/api/v1/messages", timeout=10)


def mh_resumen(m: dict) -> dict:
    import email
    from email import policy
    raw = m["Raw"]["Data"]
    msg = email.message_from_string(raw, policy=policy.default)
    html = ""
    texto = ""
    for part in msg.walk():
        if part.get_content_type() == "text/html":
            html = part.get_content()
        elif part.get_content_type() == "text/plain":
            texto = part.get_content()
    return {"id": m["ID"], "de": msg["From"], "para": [a for a in m["Raw"]["To"]], "cabecera_to": msg["To"],
            "asunto": str(msg["Subject"]), "reply_to": msg["Reply-To"], "html": html, "texto": texto}


def esperar_correos(pred, timeout=90, intervalo=2) -> list[dict]:
    fin = time.time() + timeout
    res = []
    while time.time() < fin:
        res = [mh_resumen(m) for m in mh_mensajes()]
        res = [x for x in res if pred(x)]
        if res:
            return res
        time.sleep(intervalo)
    return res


def psql(sql: str) -> str:
    """Consulta a la BD del stack (solo lectura o filas QA7 propias)."""
    env = {}
    for line in open(os.path.join(AQUI, "revision.env"), encoding="utf-8"):
        if "=" in line and not line.startswith("#"):
            k, v = line.strip().split("=", 1)
            env[k] = v
    cmd = ["docker", "exec", "-i", "inv_ui-db-1", "psql", "-U", env["POSTGRES_USER"], "-d", env["POSTGRES_DB"],
           "-At", "-F", "|", "-c", sql]
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8").stdout.strip()


def integ_base(**kw) -> dict:
    """Payload de integración en modo CORREO contra MailHog."""
    base = {
        "modo": "CORREO", "proveedor": "SMTP", "cuenta_email": "inventario.qa7@empresa.local",
        "cuenta_usuario": None, "nombre_remitente": "QA7 Inventario TI", "smtp_host": "qa7_mailhog",
        "smtp_puerto": 1025, "smtp_seguridad": "NINGUNA", "graph_tenant_id": None, "graph_client_id": None,
        "ad_servidor": None, "ad_base_dn": None, "ad_grupo_base_dn": None, "ad_grupo_admin": None,
        "ad_start_tls": False, "ad_verificar_cert": True, "ad_intervalo_min": 0, "ad_desactivar": True,
        "ad_usar_cuenta_servicio": True, "ad_bind_usuario": None, "cuenta_password": "",
    }
    base.update(kw)
    return base


def integ_desactivada() -> dict:
    return {
        "modo": "DESACTIVADO", "proveedor": "SMTP", "cuenta_email": None, "cuenta_usuario": None,
        "nombre_remitente": "Sistema Inventario", "smtp_host": "", "smtp_puerto": 587, "smtp_seguridad": "STARTTLS",
        "graph_tenant_id": None, "graph_client_id": None, "ad_servidor": None, "ad_base_dn": None,
        "ad_grupo_base_dn": None, "ad_grupo_admin": None, "ad_start_tls": False, "ad_verificar_cert": True,
        "ad_intervalo_min": 0, "ad_desactivar": True, "ad_usar_cuenta_servicio": False, "ad_bind_usuario": "",
        "cuenta_password": "", "graph_client_secret": "", "ad_bind_password": "",
    }

_TOKEN = os.path.join(SAL, ".token_sa")
_COOKIES = os.path.join(SAL, ".cookies_sa")


def _guardar(c: httpx.Client) -> None:
    open(_TOKEN, "w", encoding="utf-8").write(c.headers["Authorization"].split()[1])
    json.dump({ck.name: ck.value for ck in c.cookies.jar}, open(_COOKIES, "w", encoding="utf-8"))


def refrescar(c: httpx.Client) -> bool:
    r = c.post(f"{BASE}/login/refresh")
    if r.status_code == 200 and r.json().get("access_token"):
        c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
        _guardar(c)
        return True
    return False


def cliente() -> httpx.Client:
    """Cliente sa reutilizando token/cookie de refresco; un solo login si no hay otra opción (sin reintentos)."""
    c = httpx.Client(verify=False, timeout=120)
    try:
        c.headers["Authorization"] = "Bearer " + open(_TOKEN, encoding="utf-8").read().strip()
        for k, v in json.load(open(_COOKIES, encoding="utf-8")).items():
            c.cookies.set(k, v, domain="localhost")
        if c.get(f"{BASE}/directorio/estado").status_code == 200 or refrescar(c):
            return c
    except (OSError, ValueError):
        pass
    c = cliente_autenticado()
    c.timeout = httpx.Timeout(120)
    _guardar(c)
    return c


def get(c, path, **kw):
    r = c.get(f"{BASE}{path}", **kw)
    if r.status_code == 401 and refrescar(c):
        r = c.get(f"{BASE}{path}", **kw)
    return r


def req(c, metodo, path, **kw):
    r = c.request(metodo, f"{BASE}{path}", **kw)
    if r.status_code == 401 and refrescar(c):
        r = c.request(metodo, f"{BASE}{path}", **kw)
    return r

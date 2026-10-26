"""QA8 (UX): utilidades comunes — login en la UI con TOTP, idioma y chequeos de texto."""
import json
import os
import re
import time


AQUI = os.path.dirname(os.path.abspath(__file__))
BASE = "https://localhost:8443"
OUT = os.path.join(AQUI, "salidas", "qa8")
os.makedirs(OUT, exist_ok=True)

with open(os.path.join(AQUI, "salidas", "credenciales_qa.json"), encoding="utf-8") as f:
    CRED = json.load(f)

VIEWPORTS = {"desk": (1440, 900), "tab": (820, 1180), "mob": (390, 844)}
LOCALES = {"es": "es-ES", "en": "en-US", "it": "it-IT"}

UUID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
# Claves i18n sin traducir: palabra.palabra(_x) en minúsculas, con al menos un punto y guion bajo o 2 puntos
KEY_RE = re.compile(r"(?<![\w@/.])[a-z]+(?:_[a-z0-9]+)*\.[a-z_]+(?:\.[a-z_0-9]+)*(?![\w@/])")
RAW_RE = re.compile(r"\b(undefined|NaN|null|\[object Object\]|Invalid Date|None)\b")
TECH_RE = re.compile(r"\b[A-Z]{3,}(?:_[A-Z0-9]{2,})+\b")  # códigos crudos tipo ASSET_NOT_FOUND
DOMAINS_OK = ("lombardi", ".com", ".gt", ".local", ".png", ".pdf", ".json", "localhost")


def analizar_texto(texto: str) -> dict:
    """Busca restos técnicos en el texto visible."""
    claves = [k for k in set(KEY_RE.findall(texto))
              if ("_" in k or k.count(".") >= 2) and not any(d in k for d in DOMAINS_OK)]
    return {
        "uuids": sorted(set(UUID_RE.findall(texto)))[:5],
        "claves_i18n": sorted(claves)[:10],
        "crudos": sorted(set(RAW_RE.findall(texto)))[:10],
        "codigos": sorted(set(TECH_RE.findall(texto)))[:10],
    }


ESTADO = os.path.join(OUT, "estado_sesion.json")


def esperar_desbloqueo(max_min=20):
    """Si la cuenta está bloqueada (429), espera sin gastar intentos de TOTP."""
    import httpx
    for _ in range(max_min * 2):
        r = httpx.post(f"{BASE}/api/v1/login/access-token",
                       data={"username": CRED["usuario"], "password": CRED["clave"]}, verify=False)
        if r.status_code != 429:
            return
        print("  (cuenta sa bloqueada; esperando 30 s)", flush=True)
        time.sleep(30)


async def login(page, lang="es"):
    """Login en la UI como sa con TOTP. Reusa la sesión guardada si sigue viva.
    Si el código es rechazado (reuso por otro agente) espera la ventana siguiente
    y reintenta en el mismo desafío (máx. 3 para no bloquear la cuenta)."""
    await page.goto(f"{BASE}/login")
    await page.evaluate(f"localStorage.setItem('app_language', '{lang}')")
    await page.goto(f"{BASE}/activos")
    await page.wait_for_timeout(2500)
    if "/login" not in page.url:
        return True
    esperar_desbloqueo()
    await page.goto(f"{BASE}/login")
    await page.wait_for_load_state("networkidle")
    await page.fill("input[name=username]", CRED["usuario"])
    await page.fill("input[name=password]", CRED["clave"])
    await page.click("button[type=submit]")
    otp = page.locator("input[autocomplete=one-time-code]")
    await otp.wait_for(timeout=15000)
    # Un único intento: el código lo entrega qa_login (serializa entre procesos). Nunca se reintenta.
    from qa_login import codigo_totp_ui
    await otp.fill(codigo_totp_ui(CRED["usuario"], CRED["totp_secret"]))
    await otp.press("Enter")
    try:
        await page.wait_for_url(lambda u: "/login" not in u, timeout=15000)
        await page.wait_for_load_state("networkidle")
        await page.context.storage_state(path=ESTADO)
        return True
    except Exception:
        pass
    raise RuntimeError("No se pudo iniciar sesión como sa")


async def nuevo_contexto(browser, vp="desk", lang="es"):
    w, h = VIEWPORTS[vp]
    kw = {"storage_state": ESTADO} if os.path.exists(ESTADO) else {}
    return await browser.new_context(viewport={"width": w, "height": h}, ignore_https_errors=True,
                                     locale=LOCALES[lang], **kw)


async def guardar(ctx):
    await ctx.storage_state(path=ESTADO)


async def set_lang(page, lang):
    await page.evaluate(f"localStorage.setItem('app_language', '{lang}')")
    await page.reload()
    await page.wait_for_load_state("networkidle")


async def overflow(page) -> int:
    return await page.evaluate("document.documentElement.scrollWidth - window.innerWidth")


async def overflow_elems(page, limit=5):
    """Elementos que se salen del viewport por la derecha (para diagnosticar)."""
    return await page.evaluate("""(limit) => {
      const W = window.innerWidth, out = [];
      for (const el of document.querySelectorAll('body *')) {
        const r = el.getBoundingClientRect();
        if (r.width && r.right > W + 1 && getComputedStyle(el).position !== 'fixed') {
          out.push((el.tagName + '.' + (el.className && el.className.baseVal === undefined ? String(el.className) : '')).slice(0, 90) + ' right=' + Math.round(r.right));
          if (out.length >= limit) break;
        }
      }
      return out;
    }""", limit)

"""Aplica una paleta vía API, captura páginas clave y mide contraste real en el DOM."""
import asyncio
import sys

import httpx
from playwright.async_api import async_playwright

BASE = "https://localhost:8443"
OUT, PWD, BG, PRI, SEC, TAG = sys.argv[1:7]
ROUTES = ["/", "/activos", "/config", "/empleados"]

CONTRAST_JS = """
() => {
  const lum = (c) => { const m = c.match(/\\d+(\\.\\d+)?/g).map(Number); const [r,g,b] = m.slice(0,3).map(v => { v/=255; return v<=0.03928? v/12.92 : Math.pow((v+0.055)/1.055, 2.4); }); return 0.2126*r+0.7152*g+0.0722*b; };
  const parse = (c) => { const m = (c.match(/[\d.]+/g) || []).map(Number); return {r:m[0]||0,g:m[1]||0,b:m[2]||0,a:m.length>3?m[3]:1}; };
  // Compone los fondos semitransparentes sobre el fondo opaco del ancestro.
  const bgOf = (el) => { const layers = []; while (el) { const c = parse(getComputedStyle(el).backgroundColor); if (c.a > 0) { layers.push(c); if (c.a >= 1) break; } el = el.parentElement; }
    let base = {r:255,g:255,b:255}; for (let i = layers.length-1; i >= 0; i--) { const l = layers[i]; base = {r:l.r*l.a+base.r*(1-l.a), g:l.g*l.a+base.g*(1-l.a), b:l.b*l.a+base.b*(1-l.a)}; }
    return `rgb(${base.r},${base.g},${base.b})`; };
  let worst = 99, sample = '', n = 0;
  for (const el of document.querySelectorAll('body *')) {
    if (!el.childNodes.length || ![...el.childNodes].some(x => x.nodeType === 3 && x.textContent.trim())) continue;
    const st = getComputedStyle(el); if (st.visibility === 'hidden' || st.display === 'none' || +st.opacity === 0) continue;
    const r = el.getBoundingClientRect(); if (!r.width || !r.height) continue;
    if (el.closest('[aria-disabled="true"],.Mui-disabled,[disabled]')) continue;
    const a = lum(st.color), b = lum(bgOf(el)); const ratio = (Math.max(a,b)+0.05)/(Math.min(a,b)+0.05);
    n++; if (ratio < worst) { worst = ratio; sample = el.textContent.trim().slice(0,40); }
  }
  return {worst: Math.round(worst*100)/100, sample, n};
}
"""


async def main():
    with httpx.Client(verify=False) as c:
        tok = c.post(f"{BASE}/api/v1/login/access-token", data={"username": "sa", "password": PWD}).json()["access_token"]
        r = c.put(f"{BASE}/api/v1/gov/config", headers={"Authorization": f"Bearer {tok}"},
                  json={"SYS_Color_Fondo": BG, "SYS_Color_Primario": PRI, "SYS_Color_Secundario": SEC})
        assert r.status_code == 200, r.text
    async with async_playwright() as p:
        b = await p.chromium.launch()
        ctx = await b.new_context(viewport={"width": 1440, "height": 900}, ignore_https_errors=True)
        page = await ctx.new_page()
        await page.goto(f"{BASE}/login")
        await page.fill("input[name=username]", "sa")
        await page.fill("input[name=password]", PWD)
        await page.click("button[type=submit]")
        await page.wait_for_url(lambda u: "/login" not in u)
        for route in ROUTES:
            await page.goto(f"{BASE}{route}")
            await page.wait_for_load_state("networkidle")
            await page.wait_for_timeout(600)
            name = route.strip("/").replace("/", "_") or "dashboard"
            await page.screenshot(path=f"{OUT}/{TAG}_{name}.png")
            res = await page.evaluate(CONTRAST_JS)
            print(f"{TAG} {route}: contraste mínimo de texto {res['worst']}:1 ({res['n']} textos) → «{res['sample']}»")
        # Un diálogo abierto (formulario)
        await page.goto(f"{BASE}/activos")
        await page.wait_for_load_state("networkidle")
        await page.get_by_role("button", name="Nuevo activo").click()
        await page.wait_for_timeout(800)
        await page.screenshot(path=f"{OUT}/{TAG}_dialog.png")
        await b.close()


asyncio.run(main())

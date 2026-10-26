"""
QA4 — Pruebas sin autenticación: cabeceras de seguridad, CORS con origen ajeno,
endpoints públicos, enumeración básica (usuario inexistente) y exposición del edge.
No toca ninguna cuenta existente. Evidencia: salidas/qa4/anonimo.json
"""
import json
import os
import time

import httpx

ORIGEN = "https://localhost:8443"
API = f"{ORIGEN}/api/v1"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "salidas", "qa4", "anonimo.json")
res = {}


def h(r):
    return {k.lower(): v for k, v in r.headers.items()}


with httpx.Client(verify=False, timeout=30) as c:
    # 1. Cabeceras en SPA y en API
    for nombre, url in [("spa", f"{ORIGEN}/"), ("api_config", f"{API}/gov/config"),
                        ("api_404", f"{API}/no-existe"), ("api_login_err", None)]:
        if url:
            r = c.get(url)
        else:
            r = c.post(f"{API}/login/access-token", data={"username": "qa4_noexiste_zz", "password": "x"})
        res[f"headers_{nombre}"] = {"status": r.status_code, "headers": h(r)}

    # 2. CORS con origen ajeno (preflight y simple)
    for origin in ["https://evil.example", "null", "https://localhost:8443.evil.example", "https://localhost:8443"]:
        pre = c.options(f"{API}/login/access-token", headers={
            "Origin": origin, "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,authorization"})
        simple = c.get(f"{API}/gov/config", headers={"Origin": origin})
        res[f"cors_{origin}"] = {
            "preflight_status": pre.status_code,
            "preflight_acao": pre.headers.get("access-control-allow-origin"),
            "preflight_acac": pre.headers.get("access-control-allow-credentials"),
            "simple_acao": simple.headers.get("access-control-allow-origin"),
            "simple_acac": simple.headers.get("access-control-allow-credentials"),
        }

    # 3. Endpoints de infraestructura expuestos
    for p in ["/metrics", "/health", "/health/full", "/docs", "/redoc", "/openapi.json",
              "/api/v1/openapi.json", "/api/v1/docs", "/api/docs", "/.env", "/.git/config"]:
        r = c.get(ORIGEN + p)
        res[f"edge_{p}"] = {"status": r.status_code, "ctype": r.headers.get("content-type"),
                            "len": len(r.content), "muestra": r.text[:120]}

    # 4. Config pública: qué expone
    r = c.get(f"{API}/gov/config")
    res["gov_config_publica"] = {"status": r.status_code, "claves": sorted(r.json().keys()) if r.status_code == 200 else r.text}

    # 5. Usuario inexistente: mensaje genérico y tiempo
    tiempos = []
    for i in range(5):
        t = time.perf_counter()
        r = c.post(f"{API}/login/access-token", data={"username": f"qa4_noexiste_{i}", "password": "Cualquiera123"})
        tiempos.append(round((time.perf_counter() - t) * 1000))
    res["login_inexistente"] = {"status": r.status_code, "body": r.text, "ms": tiempos}

    # 6. Anti-enumeración en el restablecimiento (identificador inexistente)
    r = c.post(f"{API}/login/password-reset/request", json={"identifier": "qa4_noexiste@x.com"})
    res["reset_inexistente"] = {"status": r.status_code, "body": r.text}

    # 7. Refresh / logout sin credenciales
    r = c.post(f"{API}/login/refresh")
    res["refresh_sin_token"] = {"status": r.status_code, "body": r.text}
    r = c.post(f"{API}/login/refresh", json={"refresh_token": "a.b.c"})
    res["refresh_basura"] = {"status": r.status_code, "body": r.text}
    # alg=none
    import base64
    def b64(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()
    none_tok = b64({"alg": "none", "typ": "JWT"}) + "." + b64(
        {"sub": "sa", "type": "access", "role": "SUPER_ADMIN", "jti": "x", "exp": int(time.time()) + 600}) + "."
    r = c.get(f"{API}/me", headers={"Authorization": f"Bearer {none_tok}"})
    res["jwt_alg_none"] = {"status": r.status_code, "body": r.text}
    r = c.get(f"{API}/core/activos", headers={"Authorization": "Bearer x"})
    res["bearer_vacio"] = {"status": r.status_code, "body": r.text[:200]}

    # 8. Login con JSON en lugar de form / inyección SQL en el usuario
    for u in ["' OR '1'='1", "sa'--", "sa\" OR 1=1--", "a" * 5000]:
        r = c.post(f"{API}/login/access-token", data={"username": u, "password": "x' OR '1'='1"})
        res[f"login_sqli_{u[:20]}"] = {"status": r.status_code, "body": r.text[:200]}

    # 9. Métodos HTTP y TRACE
    r = c.request("TRACE", f"{ORIGEN}/")
    res["trace"] = {"status": r.status_code}

    # 10. HTTP → HTTPS
    try:
        r = httpx.get("http://localhost:8081/", follow_redirects=False, timeout=10)
        res["http_redirect"] = {"status": r.status_code, "location": r.headers.get("location")}
    except Exception as e:  # noqa: BLE001
        res["http_redirect"] = {"error": str(e)}

os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "w", encoding="utf-8") as f:
    json.dump(res, f, ensure_ascii=False, indent=1)
print(json.dumps(res, ensure_ascii=False, indent=1))

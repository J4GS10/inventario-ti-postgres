"""QA7: estado de la integración de correo/AD, cola de correos y reglas (solo lectura)."""
import json
import sys

from qa_login import BASE, cliente_autenticado

c = cliente_autenticado()
out = {}
for k, path in [("integracion", "/directorio/integracion"), ("estado_ad", "/directorio/estado"),
                ("outbox", "/gov/email-outbox"), ("reglas", "/directorio/notificaciones/reglas")]:
    r = c.get(f"{BASE}{path}")
    out[k] = {"status": r.status_code, "body": r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text[:300]}
dest = sys.argv[1] if len(sys.argv) > 1 else "salidas/qa7/estado.json"
with open(dest, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(json.dumps(out, ensure_ascii=False, indent=1)[:6000])

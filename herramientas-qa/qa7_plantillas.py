"""QA7: renderiza todas las plantillas de correo con datos de ejemplo (se ejecuta DENTRO de un
contenedor desechable basado en la imagen del backend; no toca el stack).
Uso: docker run --rm --network none --env-file revision.env -v <herramientas-qa>:/qa inv_ui-backend python /qa/qa7_plantillas.py
"""
import json
import re

from app.core.email import _SUBJECTS, _TEMPLATES, _render

ctx_op = {"operator_name": "QA7 Operador Pérez", "operator_role": "SUPER_ADMIN", "reply_to": "qa7.op@empresa.local"}
CTX = {
    "asignacion": {"codigo": "LAP-0001", "serie": "SN123", "tipo": "Laptop", "marca": "Dell", "modelo": "Latitude 5440",
                   "hostname": "LOMB-QA7", "persona_nombre": "María José", "fecha": "2026-09-28T15:32:11.123456",
                   "area": "QA7-Área", "observacion": "Entrega <b>con</b> cargador"},
    "devolucion": {"codigo": "LAP-0001", "persona_nombre": "María José", "fecha": "2026-09-28T15:32:11.123456"},
    "transferencia": {"codigo": "LAP-0001", "origen_nombre": "Ana García", "destino_nombre": "Juan Pérez", "fecha": "2026-09-28T15:32:11"},
    "baja": {"codigo": "LAP-0001", "serie": "SN123", "fecha": "2026-09-28", "motivo": "Baja lógica solicitada por administrador"},
    "offboarding": {"persona_nombre": "Con Activos", "persona_email": "x@empresa.local", "num_activos": 2,
                    "activos_lista": ["LAP-1", "MON-2"], "usuario_desactivado": "Sí", "fecha": "2026-09-28T15:32:11.123456"},
    "mantenimiento_abierto": {"codigo": "LAP-0001", "tipo": "Correctivo", "persona_nombre": "Ana García",
                              "descripcion": "No enciende", "fecha": "2026-09-28T15:32:11.123456"},
    "mantenimiento_cerrado": {"codigo": "LAP-0001", "costo": "1234567.50 USD", "fecha": "2026-09-28T15:32:11.123456", "estado_final": "Asignado"},
    "stock_bajo": {"codigo": "Tóner HP 85A", "stock_actual": 1, "stock_minimo": 5, "unidad": "unidad", "categoria": "Impresión"},
    "password_changed": {"persona_nombre": "Ana", "username": "qa7", "metodo": "RESET", "fecha": "2026-09-28 15:32:11", "ip": "10.0.0.1"},
    "2fa_code": {"persona_nombre": "Ana", "username": "qa7", "code": "123456", "minutos": 10},
    "password_reset": {"persona_nombre": "Ana", "username": "qa7", "token": "TOKEN-SECRETO", "reset_url": "https://x/reset?token=T", "minutos": 30},
    "garantia_por_vencer": {"total": 2, "dias": 30, "items": [{"codigo": "LAP-1", "fin": "2026-10-01", "estado": "Por vencer", "dias": 3},
                                                              {"codigo": "LAP-2", "fin": "2026-09-01", "estado": "Vencida", "dias": -27}]},
    "ad_sync_pendientes": {"total": 1, "items": [{"nombre": "Con Activos", "email": "c@empresa.local", "activos": 1}]},
    "nuevo_dispositivo": {"persona_nombre": "Ana", "username": "qa7", "dispositivo": "Chrome en Windows", "fecha": "2026-09-28 15:32", "ip": "10.0.0.1"},
    "cuenta_desactivada_inactividad": {"persona_nombre": "Ana", "username": "qa7", "dias": 90},
}
TUTEO = re.compile(r"\b(tu|tus|te|ti|contigo|puedes|debes|haz|revisa|recuerda|sincronizas|tienes|quieres|hola)\b", re.I)
out = {}
for name in _TEMPLATES:
    subject, html = _render(name, {**ctx_op, **CTX.get(name, {})})
    texto = re.sub(r"<style.*?</style>", "", html, flags=re.S)
    texto = re.sub(r"<[^>]+>", " ", texto)
    texto = re.sub(r"\s+", " ", texto).strip()
    out[name] = {
        "asunto": subject,
        "asunto_sin_tildes": bool(re.search(r"(Garantias|contrasena|Codigo|verificacion|sesion)", subject)),
        "texto": texto,
        "posibles_tuteos": sorted(set(m.group(0) for m in TUTEO.finditer(texto)) - {"Hola", "hola"}),
        "llaves_sin_resolver": re.findall(r"\{\{|\}\}|\{[a-z_]+\}", subject + html),
    }
    open(f"/qa/salidas/qa7/plantilla_{name}.html", "w", encoding="utf-8").write(html)
json.dump(out, open("/qa/salidas/qa7/plantillas_render.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(json.dumps(out, ensure_ascii=False, indent=1))

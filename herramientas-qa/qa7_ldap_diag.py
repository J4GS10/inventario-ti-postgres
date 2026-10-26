"""QA7 — diagnóstico: ejecuta el cliente LDAP real de la app contra qa7_samba (contenedor desechable)."""
import asyncio, json
from app.integrations import active_directory as ad
from app.services.integration_config import AdConfig
cfg = AdConfig(enabled=True, servers=("ldaps://dc01.empresa.local",), base_dn="OU=QA7-Empleados,DC=empresa,DC=local",
               group_base_dn="OU=QA7-Grupos,DC=empresa,DC=local", verify_cert=False,
               bind_user="svc_qa7@empresa.local", bind_password="Qa7-Svc-Passw0rd!")
cli = ad.LdapDirectoryClient(cfg, use_breaker=False)
rows = cli._paged(cfg.base_dn, cfg.user_filter, ad._USER_ATTRIBUTES)
print("filas LDAP:", len(rows), "| tipo de 'attributes':", type(rows[0]).__mro__ if rows else None)
print("isinstance(dict):", isinstance(rows[0], dict) if rows else None)
print("_first(objectGUID):", ad._first(rows[0], "objectGUID") if rows else None)
print("usuarios convertidos por _to_user:", len([u for u in (ad._to_user(r) for r in rows) if u]))
# Qué se obtendría si _first aceptara cualquier Mapping
from collections.abc import Mapping
def first(e, a):
    raw = e.get(a) if isinstance(e, Mapping) else None
    return (raw[0] if raw else None) if isinstance(raw, list) else raw
ad._first = first
us = [u for u in (ad._to_user(r) for r in rows) if u]
print(json.dumps([{"email": u.email, "nombre": u.given_name, "apellido": u.surname, "dep": u.department, "cargo": u.title,
                   "jefe": u.manager_dn, "habilitado": u.enabled} for u in us], ensure_ascii=False, indent=0))
try:
    print("miembros grupo admin:", cli._members_sync("QA7-GG-Inventario-TI"))
    print("miembros grupo con espacios:", cli._members_sync("QA7 Notificaciones Mantenimiento"))
    print("grupos:", [g.nombre for g in asyncio.run(cli.search_groups("QA7"))])
except Exception as e:
    print("ERROR grupos:", type(e).__name__, e)

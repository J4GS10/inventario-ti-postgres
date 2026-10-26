"""QA7 — cómo quedarían los nombres/jefes al sincronizar (lógica real de directory_sync, sin BD)."""
from collections.abc import Mapping
from app.integrations import active_directory as ad
from app.services.directory_sync import _split
from app.services.integration_config import AdConfig
def first(e, a):
    raw = e.get(a) if isinstance(e, Mapping) else None
    return (raw[0] if raw else None) if isinstance(raw, list) else raw
ad._first = first  # solo para poder evaluar el resto de la lógica
cfg = AdConfig(enabled=True, servers=("ldaps://dc01.empresa.local",), base_dn="OU=QA7-Empleados,DC=empresa,DC=local",
               verify_cert=False, bind_user="svc_qa7@empresa.local", bind_password="Qa7-Svc-Passw0rd!")
rows = ad.LdapDirectoryClient(cfg, use_breaker=False)._paged(cfg.base_dn, cfg.user_filter, ad._USER_ATTRIBUTES)
us = [u for u in (ad._to_user(r) for r in rows) if u]
dns = {u.dn.lower(): u.email for u in us}
for u in sorted(us, key=lambda x: x.email):
    n1, n2 = _split(u.given_name or u.username); a1, a2 = _split(u.surname or "-")
    print(f"{u.email} | nombre1={n1!r} nombre2={n2!r} apellido1={a1!r} apellido2={a2!r} | dep={u.department!r} | "
          f"jefe={dns.get((u.manager_dn or '').lower(), '(fuera de la OU)' if u.manager_dn else '-')} | habilitado={u.enabled}")

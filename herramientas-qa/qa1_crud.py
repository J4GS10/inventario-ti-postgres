"""
QA1 — Pruebas CRUD por API (catálogos, ubicaciones, organización, activos,
software/licencias, consumibles, proveedores y órdenes de compra).

Crea solo datos con prefijo QA1- y registra cada caso en
salidas/qa1/api_resultados.json (lo usa el informe salidas/qa1_informe.md).

Uso (cwd = herramientas-qa):
    PYTHONIOENCODING=utf-8 python qa1_crud.py
"""
import json
import os
import time
import uuid
from datetime import date, timedelta

from qa_login import BASE
from qa1_sesion import cliente_sa, refrescar

AQUI = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(AQUI, "salidas", "qa1")
os.makedirs(OUT, exist_ok=True)
TAG = time.strftime("%H%M%S")          # sufijo para poder re-ejecutar
P = f"QA1-{TAG}"                          # prefijo de nombres
ES = json.load(open(os.path.join(AQUI, "..", "inventarioTI-frontend", "src", "locales", "es.json"),
                    encoding="utf-8"))["api_errors"]

RESULTS = []
c = cliente_sa()   # reutiliza token/refresh; login único, sin reintentos
c.headers["Accept-Language"] = "es"


def call(method, path, **kw):
    for _ in range(3):
        r = c.request(method, BASE + path, **kw)
        if r.status_code == 401 and refrescar(c):   # token expirado: refresh, no login
            continue
        if r.status_code == 429:           # rate limit compartido: esperar
            time.sleep(20)
            continue
        return r
    return r


def body(r):
    try:
        return r.json()
    except Exception:
        return r.text[:300]


def detail(r):
    b = body(r)
    return b.get("detail") if isinstance(b, dict) else None


def extract(r):
    b = body(r)
    s = json.dumps(b, ensure_ascii=False) if not isinstance(b, str) else b
    return f"HTTP {r.status_code} {s[:260]}"


def traducido(r):
    d = detail(r)
    if not isinstance(d, str):
        return None
    code = d.split(":")[0]
    return ES.get(code)


def caso(cid, area, nombre, pasos, esperado, r, ok, sev="media", nota="", obtenido=None):
    evid = extract(r) if r is not None else ""
    if r is not None:
        with open(os.path.join(OUT, f"{cid}.json"), "w", encoding="utf-8") as f:
            json.dump({"status": r.status_code, "request": f"{r.request.method} {r.request.url}",
                       "body": body(r)}, f, ensure_ascii=False, indent=1, default=str)
    tr = traducido(r) if r is not None else None
    if r is not None and r.status_code >= 400 and isinstance(detail(r), str):
        evid += f" | UI(es): {tr!r}" if tr else " | UI(es): SIN TRADUCCIÓN → mensaje genérico"
    RESULTS.append({"id": cid, "area": area, "caso": nombre, "pasos": pasos, "esperado": esperado,
                    "obtenido": obtenido or (f"HTTP {r.status_code}" if r is not None else "") + (f" — {nota}" if nota else ""),
                    "evidencia": evid, "severidad": "-" if ok else sev,
                    "estado": "OK" if ok else ("OBSERVACIÓN" if sev == "obs" else "DEFECTO")})
    print(("OK   " if ok else "FAIL ") + cid, nombre, "→", evid[:200])


def st(r, *codes):
    return r.status_code in codes


def tiene(r, code):
    d = detail(r)
    return isinstance(d, str) and d.startswith(code)


# ════════════════════════════════════════════════════════════════════════
# 1. CATÁLOGOS
# ════════════════════════════════════════════════════════════════════════
A = "Catálogos"
r = call("POST", "/cat/marcas", json={"MAR_Nombre": f"{P} Marca"})
caso("CAT-01", A, "Crear marca", "POST /cat/marcas nombre válido", "201", r, st(r, 201), "alta")
marca = body(r)["MAR_Marca"]
r2 = call("GET", f"/cat/marcas/{marca}")
caso("CAT-02", A, "Leer marca", "GET /cat/marcas/{id}", "200 con el nombre", r2, st(r2, 200) and body(r2)["MAR_Nombre"] == f"{P} Marca")
r = call("POST", "/cat/marcas", json={"MAR_Nombre": f"{P} Marca"})
caso("CAT-03", A, "Marca duplicada (mismo texto)", "POST mismo nombre", "400/409 BRAND_ALREADY_EXISTS traducido", r, tiene(r, "BRAND_ALREADY_EXISTS") and traducido(r))
r = call("POST", "/cat/marcas", json={"MAR_Nombre": f"{P} MARCA".lower()})
caso("CAT-04", A, "Marca duplicada con distinta capitalización", "POST mismo nombre en minúsculas", "Rechazo por duplicado", r, not st(r, 201), "media",
     "acepta duplicado que solo difiere en mayúsculas" if st(r, 201) else "")
dup_ci_marca = body(r).get("MAR_Marca") if st(r, 201) else None
r = call("POST", "/cat/marcas", json={"MAR_Nombre": ""})
caso("CAT-05", A, "Marca con nombre vacío", "POST MAR_Nombre=''", "422 VALIDATION_ERROR", r, st(r, 422))
r = call("POST", "/cat/marcas", json={"MAR_Nombre": "     "})
caso("CAT-06", A, "Marca con nombre solo espacios", "POST MAR_Nombre='     '", "422 (nombre en blanco)", r, st(r, 422), "media",
     "se crea una marca con nombre en blanco" if st(r, 201) else "")
if st(r, 201):
    call("DELETE", f"/cat/marcas/{body(r)['MAR_Marca']}")
r = call("POST", "/cat/marcas", json={"MAR_Nombre": "X" * 101})
caso("CAT-07", A, "Marca nombre > 100 caracteres", "POST 101 caracteres", "422", r, st(r, 422))
r = call("PATCH", f"/cat/marcas/{marca}", json={"MAR_Nombre": f"{P} Marca Editada"})
caso("CAT-08", A, "Editar marca", "PATCH nombre", "200 y nombre cambiado", r, st(r, 200) and body(r)["MAR_Nombre"].endswith("Editada"))
r = call("POST", "/cat/marcas", json={"MAR_Nombre": f"{P} Marca B"})
marca_b = body(r)["MAR_Marca"]
r = call("PATCH", f"/cat/marcas/{marca_b}", json={"MAR_Nombre": f"{P} Marca Editada"})
caso("CAT-09", A, "Editar marca a un nombre ya existente", "PATCH marca B con el nombre de la marca A", "400/409 BRAND_ALREADY_EXISTS", r,
     tiene(r, "BRAND_ALREADY_EXISTS"), "media", "no valida duplicado en edición" if st(r, 200) else "")
r = call("GET", "/cat/marcas/999999")
caso("CAT-10", A, "Leer marca inexistente", "GET /cat/marcas/999999", "404 BRAND_NOT_FOUND", r, st(r, 404))

# Tipo de conexión
r = call("POST", "/cat/tipos-conexion", json={"TCN_Nombre": f"{P} USB"[:50]})
tcn = body(r)["TCN_Tipo_Conexion"]
caso("CAT-11", A, "Crear tipo de conexión", "POST /cat/tipos-conexion", "201", r, st(r, 201))
r = call("POST", "/cat/tipos-conexion", json={"TCN_Nombre": f"{P} USB"[:50]})
caso("CAT-12", A, "Tipo de conexión duplicado", "POST mismo nombre", "400/409 con mensaje de duplicado", r, not st(r, 201) and traducido(r) and "INTEGRITY" not in str(detail(r)),
     "media", "se permite duplicado" if st(r, 201) else "")
if st(r, 201):
    call("DELETE", f"/cat/tipos-conexion/{body(r)['TCN_Tipo_Conexion']}")

# Tipo de activo con prefijo
r = call("POST", "/cat/tipos-activo", json={"TAC_Nombre": f"{P} Tipo", "TAC_Prefijo": f"Q{TAG[-4:]}"})
caso("CAT-13", A, "Crear tipo de activo con prefijo", "POST /cat/tipos-activo prefijo Q####", "201", r, st(r, 201), "alta")
tipo = body(r)["TAC_Tipo_Activo"]; prefijo = body(r)["TAC_Prefijo"]
r = call("POST", "/cat/tipos-activo", json={"TAC_Nombre": f"{P} Tipo"})
caso("CAT-14", A, "Tipo de activo duplicado", "POST mismo nombre", "400 ASSET_TYPE_ALREADY_EXISTS", r, tiene(r, "ASSET_TYPE_ALREADY_EXISTS"))
r = call("POST", "/cat/tipos-activo", json={"TAC_Nombre": f"{P} Tipo2", "TAC_Prefijo": prefijo})
caso("CAT-15", A, "Dos tipos de activo con el mismo prefijo", "POST otro tipo con el mismo prefijo", "Rechazo (los códigos automáticos colisionarían)", r,
     not st(r, 201), "alta", "acepta prefijo repetido" if st(r, 201) else "")
tipo2 = body(r).get("TAC_Tipo_Activo") if st(r, 201) else None
r = call("POST", "/cat/tipos-activo", json={"TAC_Nombre": f"{P} Tipo3", "TAC_Prefijo": "a b-%"})
caso("CAT-16", A, "Prefijo con espacios/símbolos/minúsculas", "POST prefijo 'a b-%'", "422 (formato de prefijo)", r, st(r, 422), "baja",
     f"acepta prefijo '{body(r).get('TAC_Prefijo')}'" if st(r, 201) else "")
if st(r, 201):
    call("DELETE", f"/cat/tipos-activo/{body(r)['TAC_Tipo_Activo']}")
r = call("PATCH", f"/cat/tipos-activo/{tipo}", json={"TAC_Aplica_Depreciacion": False})
caso("CAT-17", A, "Editar tipo de activo", "PATCH TAC_Aplica_Depreciacion=false", "200", r, st(r, 200) and body(r)["TAC_Aplica_Depreciacion"] is False)

# Modelo
r = call("POST", "/cat/modelos", json={"MOD_Nombre": f"{P} Modelo", "MAR_Marca": marca, "TAC_Tipo_Activo": tipo, "TCN_Tipo_Conexion": tcn, "MOD_Anio_Lanzamiento": 2024})
caso("CAT-18", A, "Crear modelo", "POST /cat/modelos marca+tipo+conexión", "201", r, st(r, 201), "alta")
modelo = body(r)["MOD_Modelo"]
r = call("POST", "/cat/modelos", json={"MOD_Nombre": f"{P} Modelo", "MAR_Marca": marca, "TAC_Tipo_Activo": tipo})
caso("CAT-19", A, "Modelo duplicado en la misma marca", "POST mismo nombre y marca", "400/409 con mensaje de duplicado", r,
     not st(r, 201) and bool(traducido(r)) and "INTEGRITY" not in str(detail(r)), "media", "se crea un modelo duplicado" if st(r, 201) else "")
if st(r, 201):
    call("DELETE", f"/cat/modelos/{body(r)['MOD_Modelo']}")
r = call("POST", "/cat/modelos", json={"MOD_Nombre": f"{P} ModeloX", "MAR_Marca": 999999})
caso("CAT-20", A, "Modelo con marca inexistente", "POST MAR_Marca=999999", "404 BRAND_NOT_FOUND traducido", r, st(r, 404) and bool(traducido(r)), "baja")
r = call("POST", "/cat/modelos", json={"MOD_Nombre": f"{P} ModeloY", "MAR_Marca": marca, "MOD_Anio_Lanzamiento": 1800})
caso("CAT-21", A, "Modelo con año fuera de rango", "POST año 1800", "422", r, st(r, 422))
r = call("PATCH", f"/cat/modelos/{modelo}", json={"MOD_Anio_Lanzamiento": 2025})
caso("CAT-22", A, "Editar modelo", "PATCH año 2025", "200", r, st(r, 200) and body(r)["MOD_Anio_Lanzamiento"] == 2025)
r = call("GET", f"/cat/modelos-flat?q={P}")
caso("CAT-23", A, "Búsqueda de modelos (modelos-flat?q=)", "GET /cat/modelos-flat?q=QA1-…", "200 y aparece el modelo", r, st(r, 200) and any(m["MOD_Modelo"] == modelo for m in body(r)))
r = call("GET", f"/cat/modelos?marca_id={marca}&tipo_id={tipo}")
caso("CAT-24", A, "Filtro de modelos por marca y tipo", "GET /cat/modelos?marca_id&tipo_id", "200 con el modelo", r, st(r, 200) and len(body(r)) >= 1)

# Estados operativos
r = call("POST", "/cat/estados-operativos", json={"EOP_Nombre": f"{P} Estado"[:50], "EOP_Descripcion": "QA"})
caso("CAT-25", A, "Crear estado operativo", "POST /cat/estados-operativos", "201", r, st(r, 201))
eop = body(r)["EOP_Estado_Operativo"]
r = call("POST", "/cat/estados-operativos", json={"EOP_Nombre": f"{P} Estado"[:50]})
caso("CAT-26", A, "Estado operativo duplicado", "POST mismo nombre", "400/409 con mensaje de duplicado", r, not st(r, 201) and bool(traducido(r)) and "INTEGRITY" not in str(detail(r)),
     "media", "se permite duplicado" if st(r, 201) else "")
if st(r, 201):
    call("DELETE", f"/cat/estados-operativos/{body(r)['EOP_Estado_Operativo']}")
r = call("PATCH", "/cat/estados-operativos/1", json={"EOP_Descripcion": "Listo para asignación"})
caso("CAT-27", A, "Editar estado del sistema (Disponible)", "PATCH /cat/estados-operativos/1", "400 CANNOT_MODIFY_SYSTEM_OPERATIONAL_STATUS traducido", r,
     tiene(r, "CANNOT_MODIFY_SYSTEM_OPERATIONAL_STATUS") and bool(traducido(r)), "media")
r = call("DELETE", "/cat/estados-operativos/1")
caso("CAT-28", A, "Borrar estado del sistema (Disponible)", "DELETE /cat/estados-operativos/1", "400/409 CANNOT_DELETE_SYSTEM_OPERATIONAL_STATUS traducido", r,
     tiene(r, "CANNOT_DELETE_SYSTEM_OPERATIONAL_STATUS") and bool(traducido(r)), "media")
r = call("PATCH", f"/cat/estados-operativos/{eop}", json={"EOP_Descripcion": "QA editado"})
caso("CAT-29", A, "Editar estado propio", "PATCH descripción", "200", r, st(r, 200))

# Tipos de especificación
r = call("POST", "/cat/tipos-especificacion", json={"TES_Nombre": f"{P} RAM", "TES_Unidad_Medida": "GB"})
caso("CAT-30", A, "Crear tipo de especificación", "POST /cat/tipos-especificacion", "201", r, st(r, 201))
tes = body(r)["TES_Tipo_Especificacion"]
r = call("POST", "/cat/tipos-especificacion", json={"TES_Nombre": f"{P} RAM", "TES_Unidad_Medida": "GB"})
caso("CAT-31", A, "Tipo de especificación duplicado", "POST mismo nombre", "400/409 con mensaje de duplicado", r, not st(r, 201) and bool(traducido(r)) and "INTEGRITY" not in str(detail(r)),
     "media", "se permite duplicado" if st(r, 201) else "")
if st(r, 201):
    call("DELETE", f"/cat/tipos-especificacion/{body(r)['TES_Tipo_Especificacion']}")
r = call("POST", "/cat/tipos-especificacion", json={"TES_Nombre": f"{P} Disco", "TES_Unidad_Medida": "X" * 21})
caso("CAT-32", A, "Unidad de medida > 20 caracteres", "POST unidad de 21 caracteres", "422", r, st(r, 422))

# ════════════════════════════════════════════════════════════════════════
# 2. UBICACIONES
# ════════════════════════════════════════════════════════════════════════
A = "Ubicaciones"
iso = "Q" + TAG[-3:]
r = call("POST", "/geo/paises", json={"PAI_Nombre": f"{P} País", "PAI_Codigo_ISO": iso})
caso("GEO-01", A, "Crear país", "POST /geo/paises", "201", r, st(r, 201), "alta")
pais = body(r)["PAI_Pais"]
r = call("POST", "/geo/paises", json={"PAI_Nombre": f"{P} País 2", "PAI_Codigo_ISO": iso})
caso("GEO-02", A, "País con código ISO duplicado", "POST otro país mismo ISO", "400/409 con mensaje de duplicado", r,
     not st(r, 201) and bool(traducido(r)) and "INTEGRITY" not in str(detail(r)), "media", "se permite ISO repetido" if st(r, 201) else "")
if st(r, 201):
    call("DELETE", f"/geo/paises/{body(r)['PAI_Pais']}")
r = call("POST", "/geo/paises", json={"PAI_Nombre": f"{P} País", "PAI_Codigo_ISO": "Z" + TAG[-3:]})
caso("GEO-03", A, "País con nombre duplicado", "POST mismo nombre otro ISO", "400/409 con mensaje de duplicado", r,
     not st(r, 201) and bool(traducido(r)) and "INTEGRITY" not in str(detail(r)), "media", "se permite nombre repetido" if st(r, 201) else "")
if st(r, 201):
    call("DELETE", f"/geo/paises/{body(r)['PAI_Pais']}")
r = call("POST", "/geo/paises", json={"PAI_Nombre": f"{P} País 3", "PAI_Codigo_ISO": "1$"})
caso("GEO-04", A, "Código ISO con formato inválido", "POST ISO '1$'", "422", r, st(r, 422), "baja", "acepta ISO '1$'" if st(r, 201) else "")
if st(r, 201):
    call("DELETE", f"/geo/paises/{body(r)['PAI_Pais']}")
r = call("POST", "/geo/estados", json={"EST_Nombre": f"{P} Depto", "PAI_Pais": pais})
caso("GEO-05", A, "Crear estado/departamento geográfico", "POST /geo/estados", "201", r, st(r, 201), "alta")
est = body(r)["EST_Estado"]
r = call("POST", "/geo/estados", json={"EST_Nombre": f"{P} Depto X", "PAI_Pais": 999999})
caso("GEO-06", A, "Estado con país inexistente", "POST PAI_Pais=999999", "404 COUNTRY_NOT_FOUND traducido", r, st(r, 404) and bool(traducido(r)), "baja")
r = call("POST", "/geo/municipios", json={"MUN_Nombre": f"{P} Muni", "EST_Estado": est})
mun = body(r)["MUN_Municipio"]
caso("GEO-07", A, "Crear municipio", "POST /geo/municipios", "201", r, st(r, 201), "alta")
r = call("POST", "/geo/sedes", json={"SED_Nombre": f"{P} Sede", "MUN_Municipio": mun, "SED_Direccion_Calle": "Calle QA"})
sede = body(r)["SED_Sede"]
caso("GEO-08", A, "Crear sede", "POST /geo/sedes", "201", r, st(r, 201), "alta")
r = call("POST", "/geo/sedes", json={"SED_Nombre": "AB", "MUN_Municipio": mun})
caso("GEO-09", A, "Sede con nombre de 2 caracteres", "POST SED_Nombre='AB'", "422 (mínimo 3)", r, st(r, 422))
r = call("POST", "/geo/edificios", json={"EDI_Nombre": f"{P} Torre", "SED_Sede": sede})
edi = body(r)["EDI_Edificio"]
caso("GEO-10", A, "Crear edificio", "POST /geo/edificios", "201", r, st(r, 201), "alta")
r = call("POST", "/geo/niveles", json={"NIV_Numero_Piso": "1", "NIV_Alias": f"{P} Piso", "EDI_Edificio": edi})
niv = body(r)["NIV_Nivel"]
caso("GEO-11", A, "Crear nivel", "POST /geo/niveles", "201", r, st(r, 201), "alta")
r = call("POST", "/geo/areas", json={"ARE_Nombre": f"{P} Área", "NIV_Nivel": niv})
area = body(r)["ARE_Area"]
caso("GEO-12", A, "Crear área", "POST /geo/areas", "201", r, st(r, 201), "alta")
r = call("POST", "/geo/areas", json={"ARE_Nombre": f"{P} Área 2", "NIV_Nivel": niv, "ARE_Tipo_Acceso": "Z" * 300})
caso("GEO-13", A, "Área con tipo de acceso arbitrario/largo", "POST ARE_Tipo_Acceso de 300 caracteres", "422 (valor fuera de catálogo)", r, st(r, 422), "baja",
     "" if st(r, 422) else "no valida ARE_Tipo_Acceso")
if st(r, 201):
    call("DELETE", f"/geo/areas/{body(r)['ARE_Area']}")
r = call("GET", "/geo/areas/all")
caso("GEO-14", A, "Jerarquía completa de áreas", "GET /geo/areas/all", "200 y contiene el área QA1", r, st(r, 200) and f"{P} Área" in r.text)
r = call("PATCH", f"/geo/sedes/{sede}", json={"SED_Direccion_Numero": "10-20"})
caso("GEO-15", A, "Editar sede", "PATCH número de dirección", "200", r, st(r, 200))
for cid, path, code in [("GEO-16", f"/geo/paises/{pais}", "CANNOT_DELETE_COUNTRY_HAS_STATES"),
                        ("GEO-17", f"/geo/estados/{est}", "CANNOT_DELETE_STATE_HAS_MUNICIPALITIES"),
                        ("GEO-18", f"/geo/municipios/{mun}", "CANNOT_DELETE_MUNICIPALITY_HAS_SITES"),
                        ("GEO-19", f"/geo/sedes/{sede}", "CANNOT_DELETE_SITE_HAS_BUILDINGS"),
                        ("GEO-20", f"/geo/edificios/{edi}", "CANNOT_DELETE_BUILDING_HAS_LEVELS"),
                        ("GEO-21", f"/geo/niveles/{niv}", "CANNOT_DELETE_LEVEL_HAS_AREAS")]:
    r = call("DELETE", path)
    caso(cid, A, f"Borrar {path.split('/')[2][:-1]} con hijos", f"DELETE {path}", f"409 {code} con mensaje traducido", r,
         tiene(r, code) and bool(traducido(r)), "media",
         "bloquea bien, pero el código no tiene traducción en la UI (se ve mensaje genérico)" if tiene(r, code) and not traducido(r) else "")

# ════════════════════════════════════════════════════════════════════════
# 3. ORGANIZACIÓN
# ════════════════════════════════════════════════════════════════════════
A = "Organización"
r = call("POST", "/org/departamentos", json={"DEP_Nombre": f"{P} Depto", "DEP_Codigo_Costos": "CC-QA1"})
dep = body(r)["DEP_Departamento"]
caso("ORG-01", A, "Crear departamento", "POST /org/departamentos", "201", r, st(r, 201), "alta")
r = call("POST", "/org/departamentos", json={"DEP_Nombre": f"{P} Depto"})
caso("ORG-02", A, "Departamento duplicado", "POST mismo nombre", "DEPARTMENT_ALREADY_EXISTS", r, tiene(r, "DEPARTMENT_ALREADY_EXISTS"))
r = call("POST", "/org/departamentos", json={"DEP_Nombre": f"{P} Depto B"})
dep_b = body(r)["DEP_Departamento"]
r = call("PATCH", f"/org/departamentos/{dep_b}", json={"DEP_Nombre": f"{P} Depto"})
caso("ORG-03", A, "Editar departamento a un nombre existente", "PATCH depto B con el nombre de A", "DEPARTMENT_ALREADY_EXISTS", r,
     tiene(r, "DEPARTMENT_ALREADY_EXISTS"), "media", "no valida duplicado en edición" if st(r, 200) else "")
r = call("PATCH", f"/org/departamentos/{dep_b}", json={"DEP_Activo": False})
caso("ORG-04", A, "Desactivar departamento", "PATCH DEP_Activo=false", "200 y DEP_Activo=false", r, st(r, 200) and body(r)["DEP_Activo"] is False)
r = call("POST", "/org/departamentos", json={"DEP_Nombre": "AB"})
caso("ORG-05", A, "Departamento con nombre corto", "POST 'AB'", "422", r, st(r, 422))
r = call("POST", "/org/cargos", json={"CAR_Nombre": f"{P} Jefe TI", "CAR_Es_Jefatura": True})
cargo = body(r)["CAR_Cargo"]
caso("ORG-06", A, "Crear cargo", "POST /org/cargos", "201", r, st(r, 201), "alta")
r = call("POST", "/org/cargos", json={"CAR_Nombre": f"{P} Jefe TI"})
caso("ORG-07", A, "Cargo duplicado", "POST mismo nombre", "400/409 con mensaje de duplicado", r, not st(r, 201) and bool(traducido(r)) and "INTEGRITY" not in str(detail(r)),
     "media", "se permite cargo duplicado" if st(r, 201) else "")
if st(r, 201):
    call("DELETE", f"/org/cargos/{body(r)['CAR_Cargo']}")

def persona(nombre, email, **extra):
    d = {"PER_Primer_Nombre": nombre, "PER_Primer_Apellido": "QA1Apellido", "PER_Email_Corporativo": email,
         "DEP_Departamento": dep, "CAR_Cargo": cargo, "SED_Sede": sede}
    d.update(extra)
    return call("POST", "/org/personas", json=d)

em_jefe = f"qa1.jefe.{TAG}@lombardi.local"
r = persona("QA1Jefe", em_jefe)
caso("ORG-08", A, "Crear persona (jefe) con correo .local", "POST /org/personas", "201", r, st(r, 201), "alta")
jefe = body(r)["PER_Persona"]
r = persona("QA1Emp", f"qa1.emp.{TAG}@lombardi.local", PER_Jefe=jefe, PER_Telefono="+502 5555-0000")
caso("ORG-09", A, "Crear persona con jefe inmediato", "POST con PER_Jefe", "201 y PER_Jefe guardado", r, st(r, 201) and body(r).get("PER_Jefe") == jefe, "alta")
emp = body(r)["PER_Persona"]
r = persona("QA1Dup", em_jefe)
caso("ORG-10", A, "Persona con correo duplicado", "POST mismo correo", "400 EMAIL_ALREADY_EXISTS", r, tiene(r, "EMAIL_ALREADY_EXISTS"))
r = persona("QA1Dup", em_jefe.upper())
caso("ORG-11", A, "Correo duplicado en mayúsculas", "POST mismo correo en MAYÚSCULAS", "400 EMAIL_ALREADY_EXISTS", r, tiene(r, "EMAIL_ALREADY_EXISTS"), "alta",
     "acepta el mismo correo en mayúsculas (la parte local no se normaliza)" if st(r, 201) else "")
if st(r, 201):
    call("DELETE", f"/org/personas/{body(r)['PER_Persona']}")
r = persona("QA1Bad", "no-es-correo")
caso("ORG-12", A, "Persona con correo inválido", "POST correo 'no-es-correo'", "422", r, st(r, 422))
r = persona("QA1Tel", f"qa1.tel.{TAG}@lombardi.local", PER_Telefono="abc-xyz")
caso("ORG-13", A, "Teléfono con letras", "POST PER_Telefono='abc-xyz'", "422 (formato de teléfono)", r, st(r, 422), "baja",
     "acepta teléfono con letras" if st(r, 201) else "")
if st(r, 201):
    call("DELETE", f"/org/personas/{body(r)['PER_Persona']}")
r = persona("QA1Jx", f"qa1.jx.{TAG}@lombardi.local", PER_Jefe=str(uuid.uuid4()))
caso("ORG-14", A, "Persona con jefe inexistente", "POST PER_Jefe=uuid aleatorio", "404 MANAGER_NOT_FOUND traducido", r, tiene(r, "MANAGER_NOT_FOUND") and bool(traducido(r)))
r = call("PATCH", f"/org/personas/{jefe}", json={"PER_Jefe": jefe})
caso("ORG-15", A, "Persona como su propio jefe", "PATCH PER_Jefe = sí misma", "400 PERSON_CANNOT_BE_OWN_MANAGER", r, tiene(r, "PERSON_CANNOT_BE_OWN_MANAGER"))
r = call("PATCH", f"/org/personas/{jefe}", json={"PER_Jefe": emp})
caso("ORG-16", A, "Ciclo de jefatura (A jefe de B y B jefe de A)", "PATCH jefe.PER_Jefe = empleado (cuyo jefe es el jefe)", "400 (ciclo de jerarquía)", r, not st(r, 200), "baja",
     "permite un ciclo de jefatura A↔B" if st(r, 200) else "")
if st(r, 200):
    call("PATCH", f"/org/personas/{jefe}", json={"PER_Jefe": None})
r = persona("QA1Otro", f"qa1.otro.{TAG}@lombardi.local")
otro = body(r)["PER_Persona"]
r = call("PATCH", f"/org/personas/{otro}", json={"PER_Email_Corporativo": em_jefe})
caso("ORG-17", A, "Editar persona con el correo de otra", "PATCH PER_Email_Corporativo = correo del jefe", "400 EMAIL_ALREADY_EXISTS traducido", r,
     tiene(r, "EMAIL_ALREADY_EXISTS"), "alta",
     "no hay validación en edición; responde el genérico INTEGRITY_CONSTRAINT_VIOLATED" if tiene(r, "INTEGRITY") else ("ACEPTA correo duplicado" if st(r, 200) else ""))
r = call("PATCH", f"/org/personas/{otro}", json={"DEP_Departamento": dep_b})
caso("ORG-18", A, "Asignar persona a un departamento desactivado", "PATCH DEP_Departamento = depto inactivo", "400 (departamento inactivo)", r, not st(r, 200), "baja",
     "permite asignar a un departamento inactivo" if st(r, 200) else "")
r = call("DELETE", f"/org/personas/{otro}")
r2 = call("GET", f"/org/personas/{otro}")
caso("ORG-19", A, "Baja lógica de persona", "DELETE /org/personas/{id} y GET", "204 y PER_Estado=false", r, st(r, 204) and body(r2).get("PER_Estado") is False)
r = call("DELETE", f"/org/cargos/{cargo}")
caso("ORG-20", A, "Borrar cargo con personas", "DELETE cargo en uso", "409 CANNOT_DELETE_POSITION_HAS_PERSONS traducido", r, tiene(r, "CANNOT_DELETE_POSITION_HAS_PERSONS") and bool(traducido(r)),
     "media", "bloquea bien, pero sin traducción en la UI" if tiene(r, "CANNOT_DELETE_POSITION_HAS_PERSONS") else "")
r = call("DELETE", f"/org/departamentos/{dep}")
caso("ORG-21", A, "Borrar departamento con personas", "DELETE depto en uso", "409 CANNOT_DELETE_DEPARTMENT_HAS_PERSONS traducido", r, tiene(r, "CANNOT_DELETE_DEPARTMENT_HAS_PERSONS") and bool(traducido(r)))
r = call("GET", "/org/departamentos/resumen")
caso("ORG-22", A, "Resumen de departamentos", "GET /org/departamentos/resumen", "200", r, st(r, 200))
r = call("GET", f"/org/personas/{emp}/contexto")
caso("ORG-23", A, "Contexto de persona (jefe sugerido)", "GET /org/personas/{id}/contexto", "200 con jefe", r, st(r, 200) and jefe in r.text)

# ════════════════════════════════════════════════════════════════════════
# 4. ACTIVOS Y ESPECIFICACIONES (moneda)
# ════════════════════════════════════════════════════════════════════════
A = "Activos"
hoy = date.today()
def activo(serie, **extra):
    d = {"ACT_Serie_Fabricante": serie, "ACT_Fecha_Compra": str(hoy - timedelta(days=30)),
         "ACT_Fin_Garantia": str(hoy + timedelta(days=700)), "MOD_Modelo": modelo, "TAC_Tipo_Activo": tipo,
         "EOP_Estado_Operativo": 1, "SED_Sede": sede}
    d.update(extra)
    r = call("POST", "/core/activos", json=d)
    if r.status_code == 201:
        CODES[r.json()["ACT_Activo"]] = r.json()["ACT_Codigo_Interno"]
    return r

CODES = {}
def det(aid):
    """Detalle por código (GET /core/activos/{id} responde 500, ver ACT-18)."""
    return call("GET", f"/core/activos/by-code/{CODES[aid]}")

r = activo(f"{P}-SER1", ACT_Costo="1234.56", ACT_Moneda="USD", especificaciones=[{"TES_Tipo_Especificacion": tes, "ESP_Valor": "16"}])
caso("ACT-01", A, "Crear activo con código automático, costo en USD y especificación", "POST /core/activos sin código, ACT_Moneda=USD", f"201, código {prefijo}…, moneda USD", r,
     st(r, 201) and body(r)["ACT_Codigo_Interno"].startswith(prefijo) and body(r)["ACT_Moneda"] == "USD" and len(body(r)["especificaciones"]) == 1, "bloqueante")
act1 = body(r)["ACT_Activo"]; cod1 = body(r)["ACT_Codigo_Interno"]
r = activo(f"{P}-SER2", ACT_Costo="100", ACT_Moneda="chf")
caso("ACT-02", A, "Moneda en minúsculas se normaliza", "POST ACT_Moneda='chf'", "201 y ACT_Moneda=CHF", r, st(r, 201) and body(r)["ACT_Moneda"] == "CHF")
act2 = body(r)["ACT_Activo"]
r = activo(f"{P}-SER3", ACT_Costo="100")
caso("ACT-03", A, "Moneda por defecto", "POST sin ACT_Moneda", "201 y ACT_Moneda=GTQ", r, st(r, 201) and body(r)["ACT_Moneda"] == "GTQ")
act3 = body(r)["ACT_Activo"]
r = activo(f"{P}-SER4", ACT_Costo="100", ACT_Moneda="EUR")
caso("ACT-04", A, "Moneda no admitida", "POST ACT_Moneda='EUR'", "422 UNSUPPORTED_CURRENCY", r, st(r, 422))
r = activo(f"{P}-SER5", ACT_Costo="-1")
caso("ACT-05", A, "Costo negativo", "POST ACT_Costo=-1", "422", r, st(r, 422))
r = activo(f"{P}-SER6", ACT_Costo="10.555")
caso("ACT-06", A, "Costo con 3 decimales", "POST ACT_Costo=10.555", "422", r, st(r, 422))
r = activo(f"{P}-SER7", ACT_Costo="99999999999.99")
caso("ACT-07", A, "Costo por encima de 12 dígitos", "POST ACT_Costo=99 999 999 999.99", "422", r, st(r, 422))
r = activo(f"{P}-SER8", ACT_Fin_Garantia=str(hoy - timedelta(days=60)))
caso("ACT-08", A, "Garantía anterior a la compra", "POST ACT_Fin_Garantia < ACT_Fecha_Compra", "400 WARRANTY_DATE_CANNOT_BE_BEFORE_PURCHASE_DATE traducido", r,
     tiene(r, "WARRANTY_DATE_CANNOT_BE_BEFORE_PURCHASE_DATE") and bool(traducido(r)))
r = activo(f"{P}-SER9", ACT_Fecha_Compra=str(hoy + timedelta(days=400)), ACT_Fin_Garantia=None)
caso("ACT-09", A, "Fecha de compra en el futuro", "POST ACT_Fecha_Compra = hoy+400 días", "422/400 (fecha futura)", r, not st(r, 201), "baja",
     "acepta fecha de compra a más de un año en el futuro" if st(r, 201) else "")
act9 = body(r).get("ACT_Activo") if st(r, 201) else None
r = activo(f"{P}-SER1")
caso("ACT-10", A, "Serie duplicada", "POST misma serie", "400 SERIAL_NUMBER_ALREADY_EXISTS", r, tiene(r, "SERIAL_NUMBER_ALREADY_EXISTS"))
r = activo(f"{P}-ser1".lower())
caso("ACT-11", A, "Serie duplicada con distinta capitalización", "POST misma serie en minúsculas", "400 SERIAL_NUMBER_ALREADY_EXISTS", r, tiene(r, "SERIAL_NUMBER_ALREADY_EXISTS"), "media",
     "acepta la misma serie en minúsculas" if st(r, 201) else "")
act11 = body(r).get("ACT_Activo") if st(r, 201) else None
r = activo(f" {P}-SER1 ")
caso("ACT-12", A, "Serie duplicada con espacios", "POST ' serie ' con espacios", "400 (se recorta y detecta duplicado)", r, not st(r, 201), "media",
     "acepta la misma serie con espacios alrededor" if st(r, 201) else "")
act12 = body(r).get("ACT_Activo") if st(r, 201) else None
r = activo(f"{P}-SER13", ACT_Codigo_Interno=cod1)
caso("ACT-13", A, "Código interno duplicado", "POST ACT_Codigo_Interno existente", "400 ASSET_CODE_ALREADY_EXISTS", r, tiene(r, "ASSET_CODE_ALREADY_EXISTS"))
r = activo("AB")
caso("ACT-14", A, "Serie de 2 caracteres", "POST serie 'AB'", "422", r, st(r, 422))
r = activo(f"{P}-SER15", ACT_Activo_Padre=str(uuid.uuid4()))
caso("ACT-15", A, "Activo padre inexistente", "POST ACT_Activo_Padre aleatorio", "404 PARENT_ASSET_NOT_FOUND", r, tiene(r, "PARENT_ASSET_NOT_FOUND"))
r = activo(f"{P}-SER16", MOD_Modelo=999999)
caso("ACT-16", A, "Modelo inexistente", "POST MOD_Modelo=999999", "404 MODEL_NOT_FOUND traducido", r, st(r, 404) and bool(traducido(r)), "baja")
r = call("POST", "/cat/tipos-activo", json={"TAC_Nombre": f"{P} SinPrefijo"})
tipo_sp = body(r)["TAC_Tipo_Activo"]
r = activo(f"{P}-SER17", TAC_Tipo_Activo=tipo_sp)
caso("ACT-17", A, "Tipo sin prefijo y sin código", "POST con tipo sin prefijo", "400 ASSET_TYPE_HAS_NO_PREFIX_… traducido", r, tiene(r, "ASSET_TYPE_HAS_NO_PREFIX") and bool(traducido(r)))
call("DELETE", f"/cat/tipos-activo/{tipo_sp}")

r = call("GET", f"/core/activos/{act1}")
caso("ACT-18", A, "Detalle del activo por id", "GET /core/activos/{id}", "200 con modelo, marca, tipo, estado, moneda", r,
     st(r, 200) and body(r)["modelo"]["marca"]["MAR_Marca"] == marca and body(r)["ACT_Moneda"] == "USD", "alta",
     "500 para CUALQUIER activo (también los reales): MissingGreenlet al serializar modelo/tipo/estado" if st(r, 500) else "")
r = call("GET", f"/core/activos/by-code/{cod1}")
caso("ACT-19", A, "Buscar por código", "GET /core/activos/by-code/{codigo}", "200", r, st(r, 200))
r = call("PATCH", f"/core/activos/{act1}", json={"ACT_Costo": "999.99", "ACT_Moneda": "GTQ", "ACT_Hostname": "QA1-HOST"})
caso("ACT-20", A, "Editar costo y moneda", "PATCH ACT_Costo=999.99, ACT_Moneda=GTQ", "200, costo y moneda cambiados", r,
     st(r, 200) and body(r)["ACT_Moneda"] == "GTQ" and str(body(r)["ACT_Costo"]).startswith("999.99"), "alta")
r = call("PATCH", f"/core/activos/{act1}", json={"ACT_Moneda": "EUR"})
caso("ACT-21", A, "Editar a moneda no admitida", "PATCH ACT_Moneda=EUR", "422", r, st(r, 422))
nueva_fc = str(hoy - timedelta(days=90)); nueva_fg = str(hoy + timedelta(days=1000))
r = call("PATCH", f"/core/activos/{act1}", json={"ACT_Fecha_Compra": nueva_fc, "ACT_Fin_Garantia": nueva_fg})
r2 = det(act1)
ok = body(r2)["ACT_Fecha_Compra"] == nueva_fc and body(r2)["ACT_Fin_Garantia"] == nueva_fg
caso("ACT-22", A, "Editar fechas de compra y garantía (lo que envía el formulario)", "PATCH ACT_Fecha_Compra y ACT_Fin_Garantia, luego GET", "Fechas actualizadas", r, ok, "alta",
     obtenido=f"HTTP {r.status_code} pero las fechas NO cambian (GET: compra={body(r2)['ACT_Fecha_Compra']}, garantía={body(r2)['ACT_Fin_Garantia']}); se ignoran en silencio" if not ok else None)
r = call("PATCH", f"/core/activos/{act1}", json={"ACT_Costo": "99999999999999.99"})
caso("ACT-23", A, "Editar costo desbordado", "PATCH ACT_Costo=99 999 999 999 999.99", "422 (igual que al crear)", r, st(r, 422), "media",
     "sin max_digits en ActivoUpdate: llega a la base" if not st(r, 422) else "")
r = call("PATCH", f"/core/activos/{act1}", json={"ACT_Costo": "12.345"})
caso("ACT-24", A, "Editar costo con 3 decimales", "PATCH ACT_Costo=12.345", "422", r, st(r, 422), "baja",
     f"acepta y guarda {body(r).get('ACT_Costo') if isinstance(body(r), dict) else ''}" if st(r, 200) else "")
r = call("PATCH", f"/core/activos/{act1}", json={"TAC_Tipo_Activo": 999999})
caso("ACT-25", A, "Editar a tipo de activo inexistente", "PATCH TAC_Tipo_Activo=999999", "404 ASSET_TYPE_NOT_FOUND", r, tiene(r, "ASSET_TYPE_NOT_FOUND"), "baja",
     "no se valida el tipo en edición" if not tiene(r, "ASSET_TYPE_NOT_FOUND") else "")
r = call("PATCH", f"/core/activos/{act2}", json={"ACT_Serie_Fabricante": f"{P}-SER1"})
caso("ACT-26", A, "Editar serie a una existente", "PATCH serie de act2 = serie de act1", "400 NEW_SERIAL_NUMBER_ALREADY_EXISTS", r, tiene(r, "NEW_SERIAL_NUMBER_ALREADY_EXISTS"))
r = call("PATCH", f"/core/activos/{act2}", json={"ACT_Codigo_Interno": cod1})
caso("ACT-27", A, "Editar código a uno existente", "PATCH código de act2 = código de act1", "400 NEW_ASSET_CODE_ALREADY_EXISTS", r, tiene(r, "NEW_ASSET_CODE_ALREADY_EXISTS"))
r = call("PATCH", f"/core/activos/{act3}", json={"SED_Sede": None})
r2 = det(act3)
caso("ACT-28", A, "Editar enviando SED_Sede=null (formulario con sede vacía)", "PATCH SED_Sede=null, GET", "Rechazo o conservar sede", r,
     body(r2).get("SED_Sede") is not None, "media",
     obtenido=f"HTTP {r.status_code}; sede resultante={body(r2).get('SED_Sede')}")
call("PATCH", f"/core/activos/{act3}", json={"SED_Sede": sede})
r = call("PATCH", f"/core/activos/{act3}", json={"ACT_Costo": None})
r2 = det(act3)
caso("ACT-29", A, "Quitar el costo del activo", "PATCH ACT_Costo=null", "200 y costo vacío", r, st(r, 200) and body(r2)["ACT_Costo"] is None, "baja")

# Especificaciones
r = call("GET", f"/core/activos/{act1}/especificaciones")
caso("ESP-01", A, "Listar especificaciones", "GET /core/activos/{id}/especificaciones", "200 con nombre y unidad", r, st(r, 200) and body(r)[0]["TES_Unidad_Medida"] == "GB")
esp1 = body(r)[0]["ESP_Especificacion"]
r = call("POST", f"/core/activos/{act1}/especificaciones", json={"TES_Tipo_Especificacion": tes, "ESP_Valor": "32"})
caso("ESP-02", A, "Misma especificación dos veces en un activo", "POST mismo TES al mismo activo", "Rechazo o reemplazo (no dos valores de RAM)", r,
     not st(r, 201), "baja", "permite dos valores del mismo tipo en el activo" if st(r, 201) else "")
r = call("POST", f"/core/activos/{act1}/especificaciones", json={"TES_Tipo_Especificacion": tes, "ESP_Valor": ""})
caso("ESP-03", A, "Especificación con valor vacío", "POST ESP_Valor=''", "422", r, st(r, 422))
r = call("PATCH", f"/core/especificaciones/{esp1}", json={"ESP_Valor": "64"})
caso("ESP-04", A, "Editar especificación", "PATCH ESP_Valor=64", "204", r, st(r, 204))
r = call("DELETE", f"/cat/tipos-especificacion/{tes}")
caso("ESP-05", A, "Borrar tipo de especificación en uso", "DELETE tipo usado por un activo", "409 CANNOT_DELETE_SPECIFICATION_TYPE_IN_USE traducido", r,
     tiene(r, "CANNOT_DELETE_SPECIFICATION_TYPE_IN_USE") and bool(traducido(r)), "media", "bloquea bien, pero sin traducción" if tiene(r, "CANNOT_DELETE_SPECIFICATION_TYPE_IN_USE") else "")
r = call("DELETE", f"/core/especificaciones/{esp1}")
caso("ESP-06", A, "Borrar especificación", "DELETE /core/especificaciones/{id}", "204", r, st(r, 204))

# Borrar catálogos en uso
r = call("DELETE", f"/cat/marcas/{marca}")
caso("CAT-33", "Catálogos", "Borrar marca en uso (tiene modelos)", "DELETE marca", "409 CANNOT_DELETE_BRAND_IN_USE traducido", r, tiene(r, "CANNOT_DELETE_BRAND_IN_USE") and bool(traducido(r)))
r = call("DELETE", f"/cat/modelos/{modelo}")
caso("CAT-34", "Catálogos", "Borrar modelo en uso (tiene activos)", "DELETE modelo", "409 CANNOT_DELETE_MODEL_IN_USE traducido", r, tiene(r, "CANNOT_DELETE_MODEL_IN_USE") and bool(traducido(r)))
r = call("DELETE", f"/cat/tipos-activo/{tipo}")
caso("CAT-35", "Catálogos", "Borrar tipo de activo en uso", "DELETE tipo", "409 CANNOT_DELETE_ASSET_TYPE_IN_USE traducido", r, tiene(r, "CANNOT_DELETE_ASSET_TYPE_IN_USE") and bool(traducido(r)))
r = call("DELETE", f"/cat/tipos-conexion/{tcn}")
caso("CAT-36", "Catálogos", "Borrar tipo de conexión en uso", "DELETE tipo de conexión usado por modelo", "409 CANNOT_DELETE_CONNECTION_TYPE_IN_USE traducido", r,
     tiene(r, "CANNOT_DELETE_CONNECTION_TYPE_IN_USE") and bool(traducido(r)), "media", "bloquea bien, pero sin traducción" if tiene(r, "CANNOT_DELETE_CONNECTION_TYPE_IN_USE") else "")
call("PATCH", f"/core/activos/{act2}", json={"EOP_Estado_Operativo": eop})
r = call("DELETE", f"/cat/estados-operativos/{eop}")
caso("CAT-37", "Catálogos", "Borrar estado operativo en uso", "DELETE estado asignado a un activo", "409 CANNOT_DELETE_OPERATIONAL_STATUS_IN_USE traducido", r,
     tiene(r, "CANNOT_DELETE_OPERATIONAL_STATUS_IN_USE") and bool(traducido(r)), "media", "bloquea bien, pero sin traducción" if tiene(r, "CANNOT_DELETE_OPERATIONAL_STATUS_IN_USE") else "")
call("PATCH", f"/core/activos/{act2}", json={"EOP_Estado_Operativo": 1})

# Búsqueda / paginación
r = call("POST", "/core/activos/search", json={"q": P, "page": 1, "per_page": 2})
caso("ACT-30", A, "Búsqueda paginada (página 1)", "POST /core/activos/search q=QA1-…, per_page=2", "200, 2 ítems y total ≥ 3", r,
     st(r, 200) and len(body(r)["items"]) == 2 and body(r)["total"] >= 3)
tot = body(r)["total"] if st(r, 200) else 0
r = call("POST", "/core/activos/search", json={"q": P, "page": 2, "per_page": 2})
caso("ACT-31", A, "Búsqueda paginada (página 2)", "page=2 per_page=2", "Resto de ítems sin repetir", r, st(r, 200) and len(body(r)["items"]) == min(2, tot - 2))
r = call("POST", "/core/activos/search", json={"q": P, "tipo_activo_id": tipo, "estado_operativo_id": 1, "sede_id": sede, "page": 1, "per_page": 50})
caso("ACT-32", A, "Filtros combinados tipo+estado+sede", "search con filtros", "200 y todos cumplen el filtro", r,
     st(r, 200) and all(i["TAC_Tipo_Activo"] == tipo and i["EOP_Estado_Operativo"] == 1 for i in body(r)["items"]))
r = call("POST", "/core/activos/search", json={"per_page": 201})
caso("ACT-33", A, "per_page por encima del máximo", "per_page=201", "422", r, st(r, 422))
r = call("POST", "/core/activos/search", json={"q": "%_%", "page": 1, "per_page": 5})
caso("ACT-34", A, "Búsqueda con comodines SQL", "q='%_%'", "200 sin tratar % como comodín", r, st(r, 200) and body(r)["total"] == 0, "baja")
r = call("POST", "/core/activos/search", json={"fecha_compra_start": str(hoy), "fecha_compra_end": str(hoy - timedelta(days=10))})
caso("ACT-35", A, "Rango de fechas invertido", "fecha_compra_start > fecha_compra_end", "422 o lista vacía con aviso", r, st(r, 200, 422), "obs",
     "devuelve 200 vacío sin avisar del rango incoherente" if st(r, 200) else "")

# Baja lógica
r = call("DELETE", f"/core/activos/{act3}")
r2 = det(act3)
caso("ACT-36", A, "Baja lógica de activo", "DELETE /core/activos/{id} y GET", "204 y estado Baja", r, st(r, 204) and body(r2)["estado_operativo"]["EOP_Nombre"] == "Baja")

# ════════════════════════════════════════════════════════════════════════
# 5. SOFTWARE Y LICENCIAS
# ════════════════════════════════════════════════════════════════════════
A = "Software"
r = call("POST", "/soft/tipos-licencia", json={"TLI_Nombre": f"{P} Suscr"[:50]})
tli = body(r)["TLI_Tipo_Licencia"]
caso("SOF-01", A, "Crear tipo de licencia", "POST /soft/tipos-licencia", "201", r, st(r, 201))
r = call("POST", "/soft/tipos-licencia", json={"TLI_Nombre": f"{P} Suscr"[:50]})
caso("SOF-02", A, "Tipo de licencia duplicado", "POST mismo nombre", "400/409 con mensaje de duplicado", r, not st(r, 201) and bool(traducido(r)) and "INTEGRITY" not in str(detail(r)),
     "media", "se permite duplicado" if st(r, 201) else "")
if st(r, 201):
    call("DELETE", f"/soft/tipos-licencia/{body(r)['TLI_Tipo_Licencia']}")
r = call("POST", "/soft/software", json={"SOF_Nombre": f"{P} Office", "SOF_Version": "2024", "SOF_Fabricante": "QA1 Corp"})
sof = body(r)["SOF_Software"]
caso("SOF-03", A, "Crear software", "POST /soft/software", "201", r, st(r, 201))
r = call("POST", "/soft/software", json={"SOF_Nombre": f"{P} Office", "SOF_Version": "2024", "SOF_Fabricante": "QA1 Corp"})
caso("SOF-04", A, "Software duplicado (nombre+versión)", "POST mismo nombre y versión", "400/409 con mensaje de duplicado", r, not st(r, 201) and bool(traducido(r)) and "INTEGRITY" not in str(detail(r)),
     "media", "se permite duplicado" if st(r, 201) else "")
if st(r, 201):
    call("DELETE", f"/soft/software/{body(r)['SOF_Software']}")
r = call("PATCH", f"/soft/software/{sof}", json={"SOF_Version": "2024 LTSC"})
caso("SOF-05", A, "Editar software", "PATCH versión", "200", r, st(r, 200))
k = lambda n: f"{P}-KEY-{n}"
lic_body = {"SOF_Software": sof, "TLI_Tipo_Licencia": tli, "LIC_Cantidad_Total": 3,
            "LIC_Fecha_Vencimiento": str(hoy + timedelta(days=365)),
            "LIC_Claves": [{"LCL_Clave_Activacion": k(i), "LCL_Referencia": f"Ref {i}"} for i in range(3)]}
r = call("POST", "/soft/licencias", json=lic_body)
caso("SOF-06", A, "Crear licencia con 3 claves individuales", "POST /soft/licencias con LIC_Claves", "201, 3 claves disponibles", r,
     st(r, 201) and body(r)["LIC_Claves_Total"] == 3 and body(r)["LIC_Claves_Disponibles"] == 3, "alta")
lic = body(r)["LIC_Licencia"]
r = call("GET", f"/soft/licencias/{lic}/claves")
caso("SOF-07", A, "Ver claves individuales", "GET /soft/licencias/{id}/claves", "200 con las 3 claves en claro (auditado)", r, st(r, 200) and len(body(r)) == 3 and body(r)[0]["LCL_Clave_Activacion"].startswith(P))
r = call("POST", "/soft/licencias", json={**lic_body, "LIC_Cantidad_Total": 2, "LIC_Claves": [{"LCL_Clave_Activacion": f"{P}-X{i}"} for i in range(3)]})
caso("SOF-08", A, "Cantidad distinta al número de claves", "POST cantidad 2 con 3 claves", "422 LICENSE_KEY_COUNT_MISMATCH traducido", r, st(r, 422) and "LICENSE_KEY_COUNT_MISMATCH" in r.text, "baja",
     "responde VALIDATION_ERROR genérico; el código va dentro de errors[].msg y la UI no lo muestra" if st(r, 422) and detail(r) == "VALIDATION_ERROR" else "")
r = call("POST", "/soft/licencias", json={**lic_body, "LIC_Cantidad_Total": 2, "LIC_Claves": [{"LCL_Clave_Activacion": f"{P}-D"}, {"LCL_Clave_Activacion": f"{P}-D"}]})
caso("SOF-09", A, "Claves repetidas en la misma petición", "POST 2 claves iguales", "400 DUPLICATE_LICENSE_KEY_IN_REQUEST", r, tiene(r, "DUPLICATE_LICENSE_KEY_IN_REQUEST"))
r = call("POST", "/soft/licencias", json={**lic_body, "LIC_Cantidad_Total": 1, "LIC_Claves": [{"LCL_Clave_Activacion": k(0)}]})
caso("SOF-10", A, "Clave que ya existe en otra licencia", "POST clave ya registrada", "409 LICENSE_KEY_ALREADY_EXISTS", r, tiene(r, "LICENSE_KEY_ALREADY_EXISTS"))
r = call("POST", "/soft/licencias", json={**lic_body, "LIC_Cantidad_Total": 1, "LIC_Claves": [], "LIC_Fecha_Vencimiento": str(hoy - timedelta(days=1))})
caso("SOF-11", A, "Licencia con vencimiento pasado", "POST vencimiento ayer", "400 LICENSE_EXPIRATION_IN_THE_PAST", r, tiene(r, "LICENSE_EXPIRATION_IN_THE_PAST"))
r = call("POST", "/soft/licencias", json={**lic_body, "LIC_Cantidad_Total": 0, "LIC_Claves": []})
caso("SOF-12", A, "Licencia con cantidad 0", "POST LIC_Cantidad_Total=0", "422", r, st(r, 422))
r = call("POST", "/soft/licencias", json={**lic_body, "LIC_Cantidad_Total": -5, "LIC_Claves": []})
caso("SOF-13", A, "Licencia con cantidad negativa", "POST LIC_Cantidad_Total=-5", "422", r, st(r, 422))
# instalar en dos activos para probar reducción y borrado
for a in (act1, act2):
    ri = call("POST", "/soft/instalaciones", json={"ACT_Activo": a, "LIC_Licencia": lic})
caso("SOF-14", A, "Instalar licencia (toma una clave)", "POST /soft/instalaciones en 2 activos", "201", ri, st(ri, 201))
r = call("GET", f"/soft/licencias/{lic}")
caso("SOF-15", A, "Contadores tras instalar", "GET licencia", "Usada=2, claves asignadas=2", r, st(r, 200) and body(r)["LIC_Cantidad_Usada"] == 2 and body(r)["LIC_Claves_Asignadas"] == 2)
r = call("PATCH", f"/soft/licencias/{lic}", json={"LIC_Cantidad_Total": 1})
caso("SOF-16", A, "Reducir cantidad por debajo de lo usado", "PATCH LIC_Cantidad_Total=1 con 2 usadas", "400 con mensaje traducido", r,
     tiene(r, "CANNOT_REDUCE_BELOW_USED") and bool(traducido(r)), "media", "bloquea bien (CANNOT_REDUCE_BELOW_USED:2) pero el código no está traducido" if tiene(r, "CANNOT_REDUCE_BELOW_USED") else "")
r = call("PATCH", f"/soft/licencias/{lic}", json={"LIC_Cantidad_Total": 10})
r2 = call("GET", f"/soft/licencias/{lic}")
caso("SOF-17", A, "Aumentar cantidad de licencia con claves individuales", "PATCH cantidad=10 (hay 3 claves)", "Pedir las claves nuevas o impedir incoherencia", r,
     not st(r, 200), "obs", obtenido=f"HTTP {r.status_code}: queda total={body(r2).get('LIC_Cantidad_Total')} con {body(r2).get('LIC_Claves_Total')} claves y no existe endpoint para añadir claves a una licencia existente")
call("PATCH", f"/soft/licencias/{lic}", json={"LIC_Cantidad_Total": 3})
r = call("PATCH", f"/soft/licencias/{lic}", json={"LIC_Fecha_Vencimiento": str(hoy - timedelta(days=30))})
caso("SOF-18", A, "Editar vencimiento a una fecha pasada", "PATCH LIC_Fecha_Vencimiento = hace 30 días", "400 LICENSE_EXPIRATION_IN_THE_PAST (como al crear)", r,
     tiene(r, "LICENSE_EXPIRATION_IN_THE_PAST"), "baja", "la edición no aplica la validación del alta" if st(r, 200) else "")
call("PATCH", f"/soft/licencias/{lic}", json={"LIC_Fecha_Vencimiento": str(hoy + timedelta(days=365))})
r = call("DELETE", f"/soft/licencias/{lic}")
caso("SOF-19", A, "Borrar licencia con instalaciones activas", "DELETE licencia en uso", "409 con mensaje traducido", r,
     st(r, 409) and bool(traducido(r)), "media", f"bloquea bien ({detail(r)}) pero el código no está traducido" if st(r, 409) and not traducido(r) else "")
r = call("DELETE", f"/soft/software/{sof}")
caso("SOF-20", A, "Borrar software con licencias", "DELETE software", "409 CANNOT_DELETE_SOFTWARE_HAS_LICENSES", r, tiene(r, "CANNOT_DELETE_SOFTWARE_HAS_LICENSES") and bool(traducido(r)))
r = call("DELETE", f"/soft/tipos-licencia/{tli}")
caso("SOF-21", A, "Borrar tipo de licencia en uso", "DELETE tipo", "409 CANNOT_DELETE_TYPE_HAS_LICENSES", r, tiene(r, "CANNOT_DELETE_TYPE_HAS_LICENSES") and bool(traducido(r)))
r = call("GET", f"/soft/licencias?software_id={sof}")
caso("SOF-22", A, "Listar licencias por software", "GET /soft/licencias?software_id=", "200 con 1 licencia", r, st(r, 200) and len(body(r)) == 1)
for a in (act1, act2):
    call("POST", "/soft/instalaciones/desinstalar", json={"ACT_Activo": a, "LIC_Licencia": lic})

# ════════════════════════════════════════════════════════════════════════
# 6. CONSUMIBLES
# ════════════════════════════════════════════════════════════════════════
A = "Consumibles"
r = call("POST", "/consumibles", json={"CON_Nombre": f"{P} Tóner", "CON_Categoria": "Impresión", "CON_Stock_Minimo": 5, "CON_Stock_Actual": 3, "SED_Sede": sede})
con = body(r)["CON_Consumible"]
caso("CON-01", A, "Crear consumible con stock inicial bajo el mínimo", "POST /consumibles stock 3, mínimo 5", "201 y bajo_stock=true", r, st(r, 201) and body(r)["bajo_stock"] is True)
r = call("POST", "/consumibles", json={"CON_Nombre": f"{P} Tóner"})
caso("CON-02", A, "Consumible duplicado", "POST mismo nombre", "409 CONSUMABLE_ALREADY_EXISTS", r, tiene(r, "CONSUMABLE_ALREADY_EXISTS"))
r = call("POST", "/consumibles", json={"CON_Nombre": f"{P} tóner".upper()})
caso("CON-03", A, "Consumible duplicado con otra capitalización", "POST nombre en MAYÚSCULAS", "409 CONSUMABLE_ALREADY_EXISTS", r, tiene(r, "CONSUMABLE_ALREADY_EXISTS"), "baja",
     "acepta duplicado en mayúsculas" if st(r, 201) else "")
if st(r, 201):
    call("DELETE", f"/consumibles/{body(r)['CON_Consumible']}")
r = call("POST", "/consumibles", json={"CON_Nombre": f"{P} Neg", "CON_Stock_Actual": -1})
caso("CON-04", A, "Stock inicial negativo", "POST CON_Stock_Actual=-1", "422", r, st(r, 422))
r = call("POST", "/consumibles", json={"CON_Nombre": f"{P} Neg2", "CON_Stock_Minimo": -1})
caso("CON-05", A, "Stock mínimo negativo", "POST CON_Stock_Minimo=-1", "422", r, st(r, 422))
r = call("GET", "/consumibles?bajo_stock=true")
caso("CON-06", A, "Filtro bajo stock", "GET /consumibles?bajo_stock=true", "200 e incluye el consumible", r, st(r, 200) and any(x["CON_Consumible"] == con for x in body(r)))
r = call("POST", "/consumibles", json={"CON_Nombre": f"{P} Papel"})
con_b = body(r)["CON_Consumible"]
r = call("PATCH", f"/consumibles/{con_b}", json={"CON_Nombre": f"{P} Tóner"})
caso("CON-07", A, "Editar consumible a un nombre existente", "PATCH nombre = otro consumible", "409 CONSUMABLE_ALREADY_EXISTS", r, tiene(r, "CONSUMABLE_ALREADY_EXISTS"), "media",
     "no valida duplicado en edición" if st(r, 200) else (f"responde {detail(r)}" if not tiene(r, "CONSUMABLE_ALREADY_EXISTS") else ""))
r = call("POST", f"/consumibles/{con}/entrada", json={"MOC_Cantidad": 10, "MOC_Motivo": "QA1 compra"})
caso("CON-08", A, "Entrada de stock", "POST /{id}/entrada 10", "201 y stock 13", r, st(r, 201) and body(r)["MOC_Stock_Resultante"] == 13)
r = call("POST", f"/consumibles/{con}/salida", json={"MOC_Cantidad": 100})
caso("CON-09", A, "Salida mayor al stock", "POST /{id}/salida 100", "400/409 INSUFFICIENT_STOCK traducido", r, tiene(r, "INSUFFICIENT_STOCK") and bool(traducido(r)))
r = call("POST", f"/consumibles/{con}/salida", json={"MOC_Cantidad": 0})
caso("CON-10", A, "Salida con cantidad 0", "POST salida 0", "422", r, st(r, 422))
r = call("PATCH", f"/consumibles/{con}", json={"CON_Activo": False})
caso("CON-11", A, "Desactivar consumible", "PATCH CON_Activo=false", "200", r, st(r, 200) and body(r)["CON_Activo"] is False)
r = call("POST", f"/consumibles/{con}/salida", json={"MOC_Cantidad": 1})
caso("CON-12", A, "Mover stock de un consumible desactivado", "POST salida sobre consumible inactivo", "Rechazo (inactivo)", r, not st(r, 201), "baja",
     "permite movimientos sobre un consumible desactivado" if st(r, 201) else "")
r = call("DELETE", f"/consumibles/{con}")
caso("CON-13", A, "Borrar consumible con movimientos", "DELETE", "409 CANNOT_DELETE_CONSUMABLE_HAS_MOVEMENTS", r, tiene(r, "CANNOT_DELETE_CONSUMABLE_HAS_MOVEMENTS"))
r = call("GET", f"/consumibles/{con}/movimientos")
caso("CON-14", A, "Historial de movimientos", "GET /{id}/movimientos", "200 con la entrada", r, st(r, 200) and len(body(r)) >= 1)

# ════════════════════════════════════════════════════════════════════════
# 7. PROVEEDORES Y ÓRDENES
# ════════════════════════════════════════════════════════════════════════
A = "Compras"
r = call("POST", "/compras/proveedores", json={"PRV_Nombre": f"{P} Proveedor", "PRV_Email": "ventas@proveedor-qa1.com", "PRV_Identificacion_Fiscal": "1234567-8"})
prv = body(r)["PRV_Proveedor"]
caso("COM-01", A, "Crear proveedor", "POST /compras/proveedores", "201", r, st(r, 201), "alta")
r = call("POST", "/compras/proveedores", json={"PRV_Nombre": f"{P} Proveedor"})
caso("COM-02", A, "Proveedor duplicado", "POST mismo nombre", "409 SUPPLIER_ALREADY_EXISTS", r, tiene(r, "SUPPLIER_ALREADY_EXISTS"))
r = call("POST", "/compras/proveedores", json={"PRV_Nombre": f"{P} Prov Mail", "PRV_Email": "correo-malo"})
caso("COM-03", A, "Proveedor con correo inválido", "POST PRV_Email='correo-malo'", "422", r, st(r, 422))
r = call("POST", "/compras/proveedores", json={"PRV_Nombre": f"{P} Prov NIT", "PRV_Identificacion_Fiscal": "1234567-8"})
caso("COM-04", A, "Proveedor con identificación fiscal repetida", "POST mismo NIT", "Rechazo por NIT duplicado", r, not st(r, 201), "baja",
     "acepta dos proveedores con el mismo NIT" if st(r, 201) else "")
if st(r, 201):
    call("DELETE", f"/compras/proveedores/{body(r)['PRV_Proveedor']}")
r = call("POST", "/compras/proveedores", json={"PRV_Nombre": f"{P} Prov B"})
prv_b = body(r)["PRV_Proveedor"]
r = call("PATCH", f"/compras/proveedores/{prv_b}", json={"PRV_Nombre": f"{P} Proveedor"})
caso("COM-05", A, "Editar proveedor a un nombre existente", "PATCH nombre = otro proveedor", "409 SUPPLIER_ALREADY_EXISTS", r, tiene(r, "SUPPLIER_ALREADY_EXISTS"), "media",
     "no valida duplicado en edición" if st(r, 200) else (f"responde {detail(r)}" if not tiene(r, "SUPPLIER_ALREADY_EXISTS") else ""))
r = call("PATCH", f"/compras/proveedores/{prv_b}", json={"PRV_Activo": False})
caso("COM-06", A, "Desactivar proveedor", "PATCH PRV_Activo=false", "200", r, st(r, 200) and body(r)["PRV_Activo"] is False)
r = call("GET", "/compras/proveedores?solo_activos=true")
caso("COM-07", A, "Filtro de proveedores activos", "GET ?solo_activos=true", "No incluye el desactivado", r, st(r, 200) and not any(x["PRV_Proveedor"] == prv_b for x in body(r)))

def orden(num, moneda, lineas, prov=None, **extra):
    d = {"OCO_Numero": num, "OCO_Fecha": str(hoy), "OCO_Moneda": moneda, "PRV_Proveedor": prov or prv, "SED_Sede": sede, "lineas": lineas}
    d.update(extra)
    return call("POST", "/compras/ordenes", json=d)

L = [{"OCL_Descripcion": "Laptop QA1", "OCL_Cantidad": 2, "OCL_Precio_Unitario": "750.25"},
     {"OCL_Descripcion": "Tóner QA1", "OCL_Cantidad": 3, "OCL_Precio_Unitario": "10.10", "CON_Consumible": con_b}]
ordenes = {}
for i, mon in enumerate(("GTQ", "USD", "CHF")):
    r = orden(f"{P}-OC{i}", mon, L)
    ordenes[mon] = body(r).get("OCO_Orden")
    caso(f"COM-{8 + i:02d}", A, f"Crear orden en {mon}", f"POST /compras/ordenes OCO_Moneda={mon}", f"201, moneda {mon}, total 1530.80", r,
         st(r, 201) and body(r)["OCO_Moneda"] == mon and float(body(r)["OCO_Total"]) == 1530.80, "alta")
r = orden(f"{P}-OCE", "EUR", L)
caso("COM-11", A, "Orden en moneda no admitida", "OCO_Moneda=EUR", "422", r, st(r, 422))
r = orden(f"{P}-OC0", "GTQ", L)
caso("COM-12", A, "Número de orden duplicado", "POST mismo OCO_Numero", "409 PURCHASE_ORDER_NUMBER_EXISTS", r, tiene(r, "PURCHASE_ORDER_NUMBER_EXISTS"))
r = orden(f"{P}-OCN", "GTQ", [{"OCL_Descripcion": "x", "OCL_Cantidad": 1, "OCL_Precio_Unitario": "-5"}])
caso("COM-13", A, "Línea con precio negativo", "OCL_Precio_Unitario=-5", "422", r, st(r, 422))
r = orden(f"{P}-OCZ", "GTQ", [{"OCL_Descripcion": "x", "OCL_Cantidad": 0}])
caso("COM-14", A, "Línea con cantidad 0", "OCL_Cantidad=0", "422", r, st(r, 422))
r = orden(f"{P}-OCB", "GTQ", [{"OCL_Descripcion": "x", "OCL_Cantidad": 1, "OCL_Precio_Unitario": "99999999999999999.999"}])
caso("COM-15", A, "Línea con precio desbordado / 3 decimales", "OCL_Precio_Unitario=99999999999999999.999", "422", r, st(r, 422), "media",
     "sin max_digits/decimal_places en LineaCreate" if not st(r, 422) else "")
r = orden(f"{P}-OCV", "GTQ", [])
caso("COM-16", A, "Orden sin líneas", "lineas=[]", "422 (una orden necesita líneas)", r, not st(r, 201), "baja",
     "acepta una orden vacía con total 0" if st(r, 201) else "")
r = orden(f"{P}-OCI", "GTQ", L[:1], prov=prv_b)
caso("COM-17", A, "Orden a proveedor desactivado", "PRV_Proveedor inactivo", "Rechazo (proveedor inactivo)", r, not st(r, 201), "baja",
     "permite comprar a un proveedor desactivado" if st(r, 201) else "")
r = orden(f"{P}-OCF", "GTQ", L[:1], OCO_Fecha=str(hoy + timedelta(days=500)))
caso("COM-18", A, "Orden con fecha muy futura", "OCO_Fecha=hoy+500", "Rechazo o advertencia", r, not st(r, 201), "obs",
     "acepta fecha de orden a 500 días" if st(r, 201) else "")
r = call("GET", "/compras/ordenes?estado=BORRADOR")
caso("COM-19", A, "Filtro de órdenes por estado", "GET ?estado=BORRADOR", "200 y todas en BORRADOR", r, st(r, 200) and all(o["OCO_Estado"] == "BORRADOR" for o in body(r)))
r = call("GET", "/compras/ordenes?estado=INVENTADO")
caso("COM-20", A, "Filtro con estado inválido", "GET ?estado=INVENTADO", "422 o lista vacía", r, st(r, 200, 422), "obs")

# Recepción de la orden USD → activos con moneda de la orden
ou = ordenes["USD"]
det = body(call("GET", f"/compras/ordenes/{ou}"))
lin_act = next(l["OCL_Linea"] for l in det["lineas"] if l["OCL_Descripcion"].startswith("Laptop"))
lin_con = next(l["OCL_Linea"] for l in det["lineas"] if l["OCL_Descripcion"].startswith("Tóner"))
rec = {"activos": [{"OCL_Linea": lin_act, "ACT_Serie_Fabricante": f"{P}-REC{i}", "MOD_Modelo": modelo, "TAC_Tipo_Activo": tipo,
                    "ACT_Fecha_Compra": str(hoy), "ACT_Costo": "750.25"} for i in range(3)]}
r = call("POST", f"/compras/ordenes/{ou}/recibir", json=rec)
caso("COM-21", A, "Recibir más activos que la cantidad de la línea", "3 activos para línea de 2", "400 RECEIPT_QUANTITY_EXCEEDS_ORDER_LINE", r, tiene(r, "RECEIPT_QUANTITY_EXCEEDS_ORDER_LINE"))
rec["activos"] = rec["activos"][:2]
rec["consumibles"] = [{"OCL_Linea": lin_con, "CON_Consumible": con_b, "cantidad": 3}]
r = call("POST", f"/compras/ordenes/{ou}/recibir", json=rec)
caso("COM-22", A, "Recibir orden USD", "POST /recibir 2 activos + 3 consumibles", "200, 2 activos creados, estado RECIBIDA", r,
     st(r, 200) and body(r)["activos_creados"] == 2, "alta")
codigos = body(r).get("activos_codigos", []) if st(r, 200) else []
if codigos:
    ra = call("GET", f"/core/activos/by-code/{codigos[0]}")
    caso("COM-23", A, "Activo recibido toma la moneda de la orden", "GET activo recibido", "ACT_Moneda=USD, costo 750.25", ra,
         st(ra, 200) and body(ra)["ACT_Moneda"] == "USD" and str(body(ra)["ACT_Costo"]).startswith("750.25"), "alta")
rc = call("GET", f"/consumibles/{con_b}")
caso("COM-24", A, "Recepción suma stock del consumible", "GET consumible", "stock 3", rc, st(rc, 200) and body(rc)["CON_Stock_Actual"] == 3)
r = call("PATCH", f"/compras/ordenes/{ordenes['CHF']}/estado", json={"OCO_Estado": "CANCELADA"})
caso("COM-25", A, "Cancelar orden", "PATCH estado CANCELADA", "200", r, st(r, 200) and body(r)["OCO_Estado"] == "CANCELADA")
r = call("PATCH", f"/compras/ordenes/{ordenes['CHF']}/estado", json={"OCO_Estado": "CANCELADA"})
caso("COM-26", A, "Cancelar orden ya cancelada", "PATCH CANCELADA otra vez", "400 ORDER_ALREADY_CANCELLED", r, tiene(r, "ORDER_ALREADY_CANCELLED"))
r = call("PATCH", f"/compras/ordenes/{ou}/estado", json={"OCO_Estado": "BORRADOR"})
caso("COM-27", A, "Volver a BORRADOR una orden RECIBIDA", "PATCH estado BORRADOR sobre orden recibida", "Rechazo (transición inválida)", r, not st(r, 200), "media",
     "permite reabrir una orden recibida (podría recibirse dos veces)" if st(r, 200) else "")
if st(r, 200):
    r2 = call("POST", f"/compras/ordenes/{ou}/recibir", json={"consumibles": [{"OCL_Linea": lin_con, "CON_Consumible": con_b, "cantidad": 3}]})
    caso("COM-28", A, "Recibir dos veces la misma orden (tras reabrirla)", "POST /recibir de nuevo", "Rechazo", r2, not st(r2, 200), "alta",
         "se suman otra vez 3 unidades de stock" if st(r2, 200) else "")
r = call("PATCH", f"/compras/ordenes/{ordenes['GTQ']}/estado", json={"OCO_Estado": "PAGADA"})
caso("COM-29", A, "Estado de orden inválido", "PATCH OCO_Estado=PAGADA", "422", r, st(r, 422))
r = call("DELETE", f"/compras/proveedores/{prv}")
caso("COM-30", A, "Borrar proveedor con órdenes", "DELETE proveedor", "409 CANNOT_DELETE_SUPPLIER_HAS_ORDERS", r, tiene(r, "CANNOT_DELETE_SUPPLIER_HAS_ORDERS"))
r = call("GET", "/compras/garantias?dias=3650")
caso("COM-31", A, "Garantías", "GET /compras/garantias", "200 e incluye activos QA1", r, st(r, 200) and P in r.text)
r = call("GET", "/stats/dashboard") if False else None

# ════════════════════════════════════════════════════════════════════════
# 8. BORRADOS FINALES LIMPIOS (entidades sin uso)
# ════════════════════════════════════════════════════════════════════════
A = "Borrado"
r = call("DELETE", f"/cat/marcas/{marca_b}")
caso("DEL-01", A, "Borrar marca sin uso", "DELETE marca B", "204", r, st(r, 204))
r = call("DELETE", f"/geo/areas/{area}")
caso("DEL-02", A, "Borrar área sin uso", "DELETE área", "204", r, st(r, 204))
r = call("DELETE", f"/compras/proveedores/{prv_b}")
caso("DEL-03", A, "Borrar proveedor sin órdenes", "DELETE proveedor B", "204", r, st(r, 204))
r = call("DELETE", f"/org/departamentos/{dep_b}")
caso("DEL-04", A, "Borrar departamento sin personas activas (tiene una persona dada de baja)", "DELETE depto B", "204 o 409 claro", r, st(r, 204, 409) and (st(r, 204) or bool(traducido(r))), "baja")

with open(os.path.join(OUT, "api_resultados.json"), "w", encoding="utf-8") as f:
    json.dump({"prefijo": P, "ids": {"marca": marca, "tipo": tipo, "modelo": modelo, "sede": sede, "act1": act1, "cod1": cod1,
                                     "dep": dep, "cargo": cargo, "jefe": jefe, "emp": emp, "sof": sof, "lic": lic, "con": con,
                                     "prv": prv, "ordenes": ordenes, "dup_ci_marca": dup_ci_marca},
               "casos": RESULTS}, f, ensure_ascii=False, indent=1)
ok = sum(1 for x in RESULTS if x["estado"] == "OK")
print(f"\n{len(RESULTS)} casos — OK {ok} — DEFECTO {sum(1 for x in RESULTS if x['estado']=='DEFECTO')} — OBS {sum(1 for x in RESULTS if x['estado']=='OBSERVACIÓN')}")

"""
Convierte el Excel «Inventario GT.xlsx» en un JSON limpio y validado que luego
carga `python -m app.importar_inventario_gt` dentro del contenedor backend.

Se ejecuta en el equipo local (necesita openpyxl, que no está en la imagen):

    python scripts/excel_inventario_gt.py "C:/Lombardi/Inventario GT.xlsx" salida.json

Hojas que usa:
  - INVENTARIO: un activo por fila (todas las categorías, también insumos).
  - MOVIMIENTO: historial de asignaciones (se reproduce en orden cronológico).
  - CATALOGO: categorías con su prefijo, marcas y ubicaciones.
  - Estaciones T: solo personas y su área (departamento). Los equipos de esa
    hoja que no están en INVENTARIO no se crean (no traen marca, modelo ni código).

Todas las correcciones que se aplican quedan en `incidencias` del JSON para
revisarlas; no se descarta ninguna fila en silencio.
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime

import openpyxl

EMAIL_DOMAIN = "lombardi-gt.example.com"

# --- Normalización de catálogos ------------------------------------------------

# Categoría del Excel (normalizada) -> (nombre en el sistema, prefijo, aplica depreciación)
CATEGORIAS = {
    "laptop": ("Laptop", "LAP", True),
    "desktop": ("Desktop", "DES", True),
    "monitor": ("Monitor", "MON", True),
    "mouse": ("Mouse", "MOU", True),
    "teclado": ("Teclado", "TEC", True),
    "docking": ("Docking", "DCK", True),
    "headset": ("Headset", "HDS", True),
    "impresora": ("Impresora", "IMP", True),
    "telefono": ("Teléfono", "TEL", True),
    "tablet": ("Tablet", "TAB", True),
    "ups": ("UPS", "UPS", True),
    "switch": ("Switch", "SWT", True),
    "router": ("Router", "RTR", True),
    "access point": ("Access Point", "APN", True),
    "cable": ("Cable", "CAB", False),
    "otro": ("Otro", "OTR", True),
    "disco": ("Disco", "DIS", True),
    "tinta": ("Tinta", "TIN", False),
    "memoria ram": ("Memoria RAM", "RAM", True),
    "bateria": ("Batería", "BAT", False),
    "limpia pantalla": ("Limpia pantalla", "PNT", False),
    "power adpater": ("Cargador de laptop", "CAR", True),
    "cargador laptop": ("Cargador de laptop", "CAR", True),
    "camara": ("Cámara", "CAM", True),
    "adaptador": ("Adaptador", "ADA", True),
    "pasta termica": ("Pasta térmica", "PST", False),
    "kit limpieza": ("Kit de limpieza", "KLI", False),
    "brochas": ("Brochas", "BRO", False),
    "herramientas": ("Herramientas", "HER", True),
    "aspiradora": ("Aspiradora", "ASP", True),
    "speaker": ("Bocina", "APE", True),
    "smart home": ("Smart Home", "SMH", True),
    "aire comprimido": ("Aire comprimido", "ARC", False),
    "limpia contacto": ("Limpia contactos", "LCT", False),
    "espuma limpiadora": ("Espuma limpiadora", "ELM", False),
    "desengrasante": ("Desengrasante", "PRL", False),
    "cargador docking": ("Cargador de docking", "CAD", True),
}

MARCAS = {
    "kingstom": "Kingston", "power sonic": "Power Sonic", "aps": "APC", "apc": "APC",
    "artic": "Arctic", "acer": "Acer", "benq": "BenQ", "stanley": "Stanley", "vtech": "VTech",
    "panfore": "Panfore", "wavlink": "Wavlink", "generico": "Genérico", "etouch": "Etouch",
    "brocs": "BROCS", "noc": "NOC", "lg": "LG", "giq": "GIQ", "hp": "HP", "dell": "Dell",
}

# Ubicación del Excel -> (área en el sistema, sede, tipo de acceso)
UBICACIONES = {
    "bodega it": ("Bodega IT", "GT", "Restringido"),
    "oficina 902": ("Oficina 902", "GT", "General"),
    "oficina 905": ("Oficina 905", "GT", "General"),
    "homeoffice": ("Home office", "GT", "General"),
    "servicio tecnico": ("Servicio técnico", "GT", "Restringido"),
    "estacion dibujo": ("Estación de dibujo", "GT", "General"),
    "limpieza y mantenimineto": ("Limpieza y mantenimiento", "GT", "General"),
    "oficina peru": ("Oficina Perú", "PE", "General"),
}
UBICACIONES_SIN_DATOS = {"oficina ecuador", "oficina chile"}

# Personas: variantes del mismo nombre -> nombre canónico (el del catálogo).
ALIAS_PERSONA = {
    "hans sievold": "Hans Vielman",
    "melani perez": "Melany Perez",
    "jefrey illescas": "Jeffrey Illescas",
    "hortencia makepeace/impresora matri.i": "Hortencia Makepeace",
}
NO_PERSONAS = {"n/a", "dibujo gt", "ingenieria gt", "oficina 902", "administracion", "bodega it", ""}

# Área de la hoja Estaciones -> (departamento, cargo)
DEPARTAMENTOS_ESTACIONES = {
    "ingenieria": ("Ingeniería", None),
    "estructurales": ("Estructurales", None),
    "recepcion": ("Recepción", None),
    "dibujo": ("Dibujo", None),
    "administracion": ("Administración", None),
    "informatica": ("Informática", None),
    "jefe de dibujantes": ("Dibujo", "Jefe de dibujantes"),
}
DEP_POR_CLASIFICAR = "Por clasificar"
CARGO_GENERICO = "Colaborador"
# Personas sin estación pero con área deducible del propio Excel.
DEP_CONOCIDO = {"Carmen Limpieza": "Limpieza y mantenimiento"}

# Equipos compartidos de las estaciones de dibujo (Dib-PC14 = PCGT01014): el
# custodio es el jefe de dibujantes.
CUSTODIO_ESTACIONES = "Edder Gallo"

SERIES_GENERICAS = {"", "generico", "genérico", "n/a", "na", "none"}


def norm(v) -> str:
    s = unicodedata.normalize("NFKD", str(v or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().casefold()


def limpio(v) -> str:
    return re.sub(r"\s+", " ", str(v if v is not None else "")).strip()


def texto_serie(v) -> str:
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return limpio(v)


def fecha(v) -> str | None:
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return None


def codigo_normal(v) -> str:
    c = limpio(v).upper()
    m = re.fullmatch(r"([A-Z]{3})-?(\d+)", c)
    return f"{m.group(1)}-{int(m.group(2)):05d}" if m else c


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", ".", norm(s)).strip(".")


def main(xlsx: str, salida: str) -> None:
    wb = openpyxl.load_workbook(xlsx, data_only=True)
    inc: list[dict] = []

    def incidencia(tipo, detalle, **extra):
        inc.append({"tipo": tipo, "detalle": detalle, **extra})

    # ------------------------------------------------------------------ CATALOGO
    cat = wb["CATALOGO"]
    hdr = [limpio(c) for c in next(cat.iter_rows(min_row=4, max_row=4, values_only=True))]
    col = {h: i for i, h in enumerate(hdr)}
    cat_rows = list(cat.iter_rows(min_row=5, values_only=True))
    usuarios_cat = [limpio(r[col["Usuarios"]]) for r in cat_rows if limpio(r[col["Usuarios"]])]
    for r in cat_rows:
        c = limpio(r[col["Categorías"]])
        if c and norm(c) not in CATEGORIAS:
            raise SystemExit(f"Categoría del catálogo sin mapeo: {c!r}")
        u = limpio(r[col["Ubicaciones"]])
        if u and norm(u) not in UBICACIONES and norm(u) not in UBICACIONES_SIN_DATOS:
            raise SystemExit(f"Ubicación del catálogo sin mapeo: {u!r}")
    for u in sorted(UBICACIONES_SIN_DATOS):
        incidencia("UBICACION_NO_CREADA", f"'{u}' está en el catálogo pero ningún equipo la usa y no trae "
                   "ciudad ni dirección; créela desde Ubicaciones cuando la necesite.")

    tipos = {}
    for nombre, prefijo, dep in CATEGORIAS.values():
        tipos[nombre] = {"nombre": nombre, "prefijo": prefijo, "deprecia": dep}

    # ------------------------------------------------------------------ PERSONAS
    personas: dict[str, dict] = {}

    def canon(nombre) -> str | None:
        n = limpio(nombre)
        if norm(n) in NO_PERSONAS or norm(n).startswith("dib-pc"):
            return None
        return ALIAS_PERSONA.get(norm(n), n)

    def persona(nombre, dep=None, cargo=None, sede="GT"):
        n = canon(nombre)
        if not n:
            return None
        p = personas.setdefault(n, {"nombre": n, "departamento": None, "cargo": None, "sede": sede})
        if dep and not p["departamento"]:
            p["departamento"] = dep
        if cargo and not p["cargo"]:
            p["cargo"] = cargo
        return n

    est = wb["Estaciones T"]
    estaciones = []
    hdr_e = [limpio(c) for c in next(est.iter_rows(min_row=2, max_row=2, values_only=True))]
    for r in est.iter_rows(min_row=3, values_only=True):
        if not any(r):
            continue
        quien, area = limpio(r[1]), limpio(r[2])
        mapa = DEPARTAMENTOS_ESTACIONES.get(norm(area))
        if canon(quien):
            if mapa:
                persona(quien, mapa[0], mapa[1])
            else:
                persona(quien)
                incidencia("PERSONA_SIN_DEPARTAMENTO",
                           f"{canon(quien)}: su área en Estaciones es '{area}' (no es un departamento); "
                           f"queda en '{DEP_POR_CLASIFICAR}'.")
            if norm(quien) in ALIAS_PERSONA:
                incidencia("NOMBRE_UNIFICADO", f"'{quien}' (Estaciones) se unificó con '{canon(quien)}' del catálogo.")
        series = {hdr_e[i]: texto_serie(v) for i, v in enumerate(r) if i >= 3 and texto_serie(v)}
        estaciones.append({"asignado": quien, "area": area, "series": series})

    for u in usuarios_cat:
        persona(u)
    for nombre, dep in DEP_CONOCIDO.items():
        if nombre in personas:
            personas[nombre]["departamento"] = dep
    for r in cat_rows:
        resp = limpio(r[col["Responsables"]])
        if resp.lower().startswith("it - "):
            persona(resp[5:], "Informática")

    # ------------------------------------------------------------------ INVENTARIO
    inv = wb["INVENTARIO"]
    filas = []
    for i, r in enumerate(inv.iter_rows(min_row=4, values_only=True), start=4):
        r = r[1:]
        if not any(v not in (None, "") for v in r):
            continue
        filas.append((i, r))

    activos = []
    codigos_vistos: dict[str, int] = {}
    series_vistas: dict[str, str] = {}
    marcas, modelos = {}, {}
    usados_por_prefijo = defaultdict(set)
    for _, r in filas:
        c = codigo_normal(r[0])
        m = re.fullmatch(r"([A-Z]{3})-(\d{5})", c)
        if m:
            usados_por_prefijo[m.group(1)].add(int(m.group(2)))

    for fila, r in filas:
        (codigo_x, categoria, descripcion, marca, modelo, serie, hostname, estado,
         usuario, ubicacion, f_entrada, responsable, factura, observ, control) = r[:15]
        codigo = codigo_normal(codigo_x)
        if codigo != limpio(codigo_x):
            incidencia("CODIGO_NORMALIZADO", f"Fila {fila}: '{codigo_x}' -> '{codigo}'.", codigo=codigo)
        cat_key = norm(categoria)
        if cat_key not in CATEGORIAS:
            raise SystemExit(f"Fila {fila}: categoría sin mapeo {categoria!r}")
        tipo, prefijo, _ = CATEGORIAS[cat_key]
        if not codigo.startswith(prefijo + "-"):
            raise SystemExit(f"Fila {fila}: el código {codigo} no corresponde al prefijo {prefijo}")

        if codigo in codigos_vistos:
            # Se conserva el código para la fila que respalda el historial de
            # movimientos (por la serie en Estaciones) y la otra toma el primer
            # número libre del prefijo.
            libre = next(n for n in range(1, 100000) if n not in usados_por_prefijo[prefijo])
            usados_por_prefijo[prefijo].add(libre)
            nuevo = f"{prefijo}-{libre:05d}"
            anterior = activos[codigos_vistos[codigo]]
            serie_txt = texto_serie(serie)
            en_estacion = any(serie_txt in e["series"].values() for e in estaciones)
            if en_estacion:
                incidencia("CODIGO_DUPLICADO",
                           f"{codigo} estaba repetido. La serie {serie_txt} (fila {fila}) aparece en Estaciones "
                           f"y conserva {codigo}; la serie {anterior['serie_original']} (fila {anterior['fila']}) "
                           f"pasa a {nuevo}.", codigo=nuevo)
                anterior["codigo"] = nuevo
                codigos_vistos[nuevo] = codigos_vistos[codigo]
            else:
                incidencia("CODIGO_DUPLICADO", f"{codigo} estaba repetido; la fila {fila} pasa a {nuevo}.", codigo=nuevo)
                codigo = nuevo
        codigos_vistos[codigo] = len(activos)

        marca_n = MARCAS.get(norm(marca), limpio(marca) or "Genérico")
        desc = limpio(descripcion)
        if tipo == "Monitor" and marca_n == "Samsung" and desc.upper().startswith("HP "):
            incidencia("MARCA_CORREGIDA", f"{codigo}: la marca decía Samsung pero la descripción es '{desc}'; se usa HP.",
                       codigo=codigo)
            marca_n = "HP"
        if norm(marca) != norm(marca_n) and norm(marca) in MARCAS and MARCAS[norm(marca)] != limpio(marca):
            pass  # unificación de mayúsculas o erratas de la marca (Kingstom, APS, ARTIC…)
        marcas[norm(marca_n)] = marca_n

        mod_cod = limpio(modelo)
        if norm(mod_cod) in SERIES_GENERICAS:
            mod_cod = ""
        if desc.startswith("Pro P,"):
            desc = f"Dell Pro {mod_cod} {desc[len('Pro P,'):].strip(' ,')}".strip()
        clave_mod = (tipo, norm(marca_n), norm(mod_cod) or norm(desc))
        if clave_mod not in modelos:
            nombre_mod = f"{desc} ({mod_cod})" if mod_cod and norm(mod_cod) not in norm(desc) else (desc or mod_cod)
            modelos[clave_mod] = {"nombre": nombre_mod[:150], "marca": marca_n, "tipo": tipo}

        serie_txt = texto_serie(serie)
        if norm(serie_txt) in SERIES_GENERICAS:
            serie_final = codigo
            motivo_serie = "sin serie del fabricante"
        elif norm(serie_txt) in series_vistas:
            serie_final = f"{serie_txt} ({codigo})"
            motivo_serie = f"serie repetida con {series_vistas[norm(serie_txt)]}"
            incidencia("SERIE_REPETIDA", f"{codigo}: la serie {serie_txt} ya la tiene {series_vistas[norm(serie_txt)]}; "
                       f"se registra como '{serie_final}'.", codigo=codigo)
        else:
            serie_final, motivo_serie = serie_txt, None
            series_vistas[norm(serie_txt)] = codigo

        ubic_key = norm(ubicacion)
        if not ubic_key:
            ubic_key = "bodega it"
            incidencia("SIN_UBICACION", f"{codigo}: sin estado ni ubicación en el Excel; se registra disponible en Bodega IT.",
                       codigo=codigo)
        if ubic_key not in UBICACIONES:
            raise SystemExit(f"Fila {fila}: ubicación sin mapeo {ubicacion!r}")
        f_ent = fecha(f_entrada)
        if not f_ent:
            incidencia("SIN_FECHA", f"{codigo}: sin fecha de entrada; se usa la de la fila anterior del mismo tipo.",
                       codigo=codigo)
        elif f_ent.startswith("2024-"):
            incidencia("FECHA_REVISAR", f"{codigo}: fecha de entrada {f_ent} (¿2026?). Se respeta tal como está.",
                       codigo=codigo)

        resp = limpio(responsable)
        activos.append({
            "fila": fila,
            "codigo": codigo,
            "tipo": tipo,
            "marca": marca_n,
            "modelo_clave": list(clave_mod),
            "serie": serie_final[:100],
            "serie_original": serie_txt,
            "motivo_serie": motivo_serie,
            "hostname": limpio(hostname) or None,
            "estado_excel": limpio(estado) or None,
            "ubicacion": UBICACIONES[ubic_key][0],
            "fecha_entrada": f_ent,
            "responsable": resp[5:] if resp.lower().startswith("it - ") else (resp or None),
            "factura": limpio(factura) or None,
            "observaciones": limpio(observ) or None,
        })

    # Fecha faltante: la del activo anterior del mismo tipo.
    for i, a in enumerate(activos):
        if not a["fecha_entrada"]:
            previo = next((b["fecha_entrada"] for b in reversed(activos[:i])
                           if b["tipo"] == a["tipo"] and b["fecha_entrada"]), None)
            a["fecha_entrada"] = previo or date.today().isoformat()

    por_codigo = {a["codigo"]: a for a in activos}

    # ------------------------------------------------------------------ MOVIMIENTO
    mov = wb["MOVIMIENTO"]
    movimientos = []
    for fila, r in enumerate(mov.iter_rows(min_row=5, values_only=True), start=5):
        if not any(v not in (None, "") for v in r):
            continue
        (f_mov, tipo_mov, codigo_x, est_ant, est_nuevo, usu_ant, usu_nuevo, ubi_ant, ubi_nueva,
         documento, responsable, motivo, ticket, _control) = r[:14]
        codigo = codigo_normal(codigo_x)
        if codigo not in por_codigo:
            raise SystemExit(f"MOVIMIENTO fila {fila}: código inexistente {codigo_x!r}")
        if norm(tipo_mov) != "asignacion":
            raise SystemExit(f"MOVIMIENTO fila {fila}: tipo no soportado {tipo_mov!r}")
        destino = limpio(usu_nuevo)
        estacion = None
        if norm(destino).startswith("dib-pc"):
            estacion = destino
            custodio = CUSTODIO_ESTACIONES
        else:
            custodio = canon(destino)
            if custodio and custodio not in personas:
                persona(custodio)
        ubic_key = norm(ubi_nueva)
        if ubic_key not in UBICACIONES:
            raise SystemExit(f"MOVIMIENTO fila {fila}: ubicación sin mapeo {ubi_nueva!r}")
        sede = UBICACIONES[ubic_key][1]
        if custodio and personas[custodio]["sede"] != sede:
            personas[custodio]["sede"] = sede
        resp = limpio(responsable)
        movimientos.append({
            "fila": fila,
            "fecha": fecha(f_mov),
            "codigo": codigo,
            "persona": custodio,
            "estacion": estacion,
            "area": UBICACIONES[ubic_key][0],
            "motivo": limpio(motivo) or None,
            "ticket": limpio(ticket) or None,
            "documento": limpio(documento) or None,
            "responsable": resp[5:] if resp.lower().startswith("it - ") else (resp or None),
            "usuario_anterior": canon(usu_ant),
        })

    if any(m["estacion"] for m in movimientos):
        incidencia("CUSTODIA_ESTACIONES",
                   f"Los equipos asignados a las estaciones Dib-PC14 a Dib-PC19 quedan bajo custodia de "
                   f"{CUSTODIO_ESTACIONES} (jefe de dibujantes), en el área 'Estación de dibujo'; la estación "
                   f"queda anotada en la observación del movimiento.")

    # Reproducción en seco para detectar duplicados y conflictos con INVENTARIO.
    abiertos: dict[str, dict] = {}
    for m in sorted(movimientos, key=lambda x: (x["fecha"], x["fila"])):
        a = por_codigo[m["codigo"]]
        if m["fecha"] < a["fecha_entrada"]:
            incidencia("FECHA_MOVIMIENTO_ANTERIOR",
                       f"{m['codigo']}: asignado el {m['fecha']} pero su fecha de entrada es {a['fecha_entrada']}; "
                       f"la entrada se registra el {m['fecha']}.", codigo=m["codigo"])
            a["fecha_entrada"] = m["fecha"]
        prev = abiertos.get(m["codigo"])
        if prev and prev["persona"] == m["persona"] and prev["estacion"] == m["estacion"]:
            m["omitir"] = True
            incidencia("MOVIMIENTO_REPETIDO",
                       f"{m['codigo']}: asignación repetida a {m['persona']} el {m['fecha']} (fila {m['fila']}); "
                       f"ya estaba asignado desde el {prev['fecha']}. Se omite.", codigo=m["codigo"])
            continue
        m["transferencia"] = prev is not None
        abiertos[m["codigo"]] = m

    for a in activos:
        abierto = abiertos.get(a["codigo"])
        if a["estado_excel"] == "Asignado" and not abierto:
            pistas = [e["asignado"] for e in estaciones if a["serie_original"] and a["serie_original"] in e["series"].values()]
            incidencia("ASIGNADO_SIN_MOVIMIENTO",
                       f"{a['codigo']} ({a['tipo']}, serie {a['serie_original'] or 's/n'}): figura 'Asignado' en "
                       f"INVENTARIO pero no hay movimiento que diga a quién; queda disponible en {a['ubicacion']}."
                       + (f" En Estaciones aparece con: {', '.join(pistas)}." if pistas else ""), codigo=a["codigo"])
        elif a["estado_excel"] == "Disponible" and abierto:
            incidencia("DISPONIBLE_CON_MOVIMIENTO",
                       f"{a['codigo']}: figura 'Disponible' en INVENTARIO pero MOVIMIENTO lo asigna a "
                       f"{abierto['persona']}{' (' + abierto['estacion'] + ')' if abierto['estacion'] else ''} "
                       f"el {abierto['fecha']}; se respeta la bitácora y queda asignado.", codigo=a["codigo"])

    # Departamento y cargo por defecto (son obligatorios en el sistema).
    for p in personas.values():
        if not p["departamento"]:
            p["departamento"] = DEP_POR_CLASIFICAR
            if not any(i["tipo"] == "PERSONA_SIN_DEPARTAMENTO" and p["nombre"] in i["detalle"] for i in inc):
                incidencia("PERSONA_SIN_DEPARTAMENTO", f"{p['nombre']}: el Excel no indica su área; "
                           f"queda en '{DEP_POR_CLASIFICAR}'.")
        p["cargo"] = p["cargo"] or CARGO_GENERICO
        partes = p["nombre"].split()
        p["primer_nombre"] = partes[0]
        p["primer_apellido"] = partes[-1] if len(partes) > 1 else "(sin apellido)"
        p["segundo_nombre"] = " ".join(partes[1:-1]) or None if len(partes) > 2 else None
        p["email"] = f"{slug(p['primer_nombre'])}.{slug(p['primer_apellido'])}@{EMAIL_DOMAIN}"
    emails = Counter(p["email"] for p in personas.values())
    dup = [e for e, n in emails.items() if n > 1]
    if dup:
        raise SystemExit(f"Correos generados repetidos: {dup}")

    resultado = {
        "fuente": xlsx.replace("\\", "/").rsplit("/", 1)[-1],
        "generado": datetime.now().isoformat(timespec="seconds"),
        "email_dominio": EMAIL_DOMAIN,
        "tipos": list(tipos.values()),
        "marcas": sorted(marcas.values(), key=str.casefold),
        "modelos": [{"clave": list(k), **v} for k, v in modelos.items()],
        "ubicaciones": [{"area": a, "sede": s, "acceso": t} for a, s, t in dict.fromkeys(UBICACIONES.values())],
        "personas": sorted(personas.values(), key=lambda p: p["nombre"]),
        "activos": activos,
        "movimientos": sorted(movimientos, key=lambda x: (x["fecha"], x["fila"])),
        "incidencias": inc,
    }
    with open(salida, "w", encoding="utf-8") as fh:
        json.dump(resultado, fh, ensure_ascii=False, indent=1)

    print(f"Activos: {len(activos)}  Tipos: {len(tipos)}  Marcas: {len(marcas)}  Modelos: {len(modelos)}")
    print(f"Personas: {len(personas)}  Movimientos: {len(movimientos)} "
          f"({sum(1 for m in movimientos if m.get('omitir'))} repetidos)")
    print("Incidencias por tipo:", dict(Counter(i["tipo"] for i in inc)))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    main(sys.argv[1], sys.argv[2])

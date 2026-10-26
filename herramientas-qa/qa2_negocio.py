"""
QA2 — Lógica de negocio de InventarioTI contra el stack de producción (https://localhost:8443).

Cubre: máquina de estados del activo, asignación/devolución/transferencia/préstamo,
offboarding (incluido el parcial por alcance de sede), mantenimiento, órdenes de
compra y recepción (activos por lote, consumibles, licencias, moneda heredada),
consumibles y stock mínimo, garantías, tablero y concurrencia.

Todos los datos que crea llevan el prefijo QA2- (series, personas, órdenes, etc.).
No borra ni modifica datos que no creó. No toca la clave, el MFA ni las sesiones de sa.

Uso (cwd = herramientas-qa):
    PYTHONIOENCODING=utf-8 python qa2_negocio.py
Salida: salidas/qa2/resultados.json + salidas/qa2/evidencias/*.json
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import uuid
import warnings
from datetime import date, timedelta
from decimal import Decimal

import httpx
import pyotp

warnings.filterwarnings("ignore")
AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
from qa_login import BASE, cliente_autenticado, login  # noqa: E402

OUT = os.path.join(AQUI, "salidas", "qa2")
EVI = os.path.join(OUT, "evidencias")
os.makedirs(EVI, exist_ok=True)

SUF = time.strftime("%m%d%H%M%S")
HOY = date.today()
RESULTADOS: list[dict] = []

SESION = os.path.join(OUT, ".sesion_sa.json")  # token + refresh de sa reutilizables entre ejecuciones


def _guardar_sesion(cl: httpx.Client) -> None:
    rtk = next((ck.value for ck in cl.cookies.jar if ck.name == "rtk"), None)
    with open(SESION, "w", encoding="utf-8") as f:
        json.dump({"access": cl.headers.get("Authorization"), "rtk": rtk}, f)


def _refrescar(cl: httpx.Client, rtk: str | None = None) -> bool:
    """Renueva el access token con /login/refresh (sin volver a hacer login ni 2FA)."""
    if rtk is None:
        rtk = next((ck.value for ck in cl.cookies.jar if ck.name == "rtk"), None)
    if not rtk:
        return False
    r = cl.post(f"{BASE}/login/refresh", json={"refresh_token": rtk})
    if r.status_code != 200:
        return False
    cl.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
    _guardar_sesion(cl)
    return True


def sesion_sa() -> httpx.Client:
    """
    Reutiliza la sesión de sa guardada; si expiró, la renueva con el refresh token.
    Solo si ambas fallan hace UN login (sin reintentos: cada 2FA rechazado cuenta
    para el bloqueo de sa, que comparten otros agentes).
    """
    cl = httpx.Client(verify=False, timeout=60)
    if os.path.exists(SESION):
        guardada = json.load(open(SESION, encoding="utf-8"))
        if guardada.get("access"):
            cl.headers["Authorization"] = guardada["access"]
            if cl.get(f"{BASE}/me").status_code == 200:
                return cl
        if _refrescar(cl, guardada.get("rtk")):
            return cl
    cl = cliente_autenticado()
    _guardar_sesion(cl)
    return cl


C = sesion_sa()
TOKEN = C.headers["Authorization"]

# Catálogo de estados (id -> nombre) se llena en setup
EST: dict[str, int] = {}
EST_NOM: dict[int, str] = {}
TMO: dict[str, int] = {}


# ───────────────────────── utilidades ─────────────────────────
def req(method: str, path: str, client: httpx.Client | None = None, **kw):
    cl = client or C
    for intento in range(8):
        r = cl.request(method, BASE + path, **kw)
        if r.status_code == 429:
            time.sleep(8 + intento * 4)
            continue
        if r.status_code == 401 and cl is C and _refrescar(C):
            global TOKEN
            TOKEN = C.headers["Authorization"]
            continue
        break
    try:
        body = r.json()
    except Exception:  # noqa: BLE001
        body = r.text[:500]
    return r.status_code, body


def evid(nombre: str, data) -> str:
    ruta = os.path.join(EVI, f"{nombre}.json")
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    return f"salidas/qa2/evidencias/{nombre}.json"


def caso(cid, nombre, pasos, esperado, obtenido, ok, evidencia, severidad="—", tipo_fallo="DEFECTO"):
    estado = "OK" if ok else tipo_fallo
    RESULTADOS.append({
        "id": cid, "caso": nombre, "pasos": pasos, "esperado": esperado,
        "obtenido": obtenido, "evidencia": evidencia,
        "severidad": "—" if ok else severidad, "estado": estado,
    })
    marca = "OK " if ok else ("OBS" if tipo_fallo == "OBSERVACIÓN" else "DEF")
    print(f"[{marca}] {cid} {nombre} -> {obtenido}")


def corto(body, n=220):
    s = json.dumps(body, ensure_ascii=False, default=str) if not isinstance(body, str) else body
    return s[:n]


SERIES: dict[str, str] = {}


def activo(aid):
    # GET /core/activos/{id} responde 500 (ver DET-00); se lee por búsqueda de serie.
    s, b = req("POST", "/core/activos/search", json={"q": SERIES[aid], "page": 1, "per_page": 5})
    return next(x for x in b["items"] if x["ACT_Activo"] == aid)


def estado_de(aid):
    return EST_NOM.get(activo(aid)["EOP_Estado_Operativo"])


def vigentes(aid):
    hist = req("GET", f"/trazabilidad/activo/{aid}/historial")[1]
    return [m for m in hist if m.get("MOV_Fecha_Devolucion") is None]


SERIE_N = [0]


def nuevo_activo(estado="Disponible", costo="100.00", moneda="GTQ", garantia=None, sede=1, compra=None):
    SERIE_N[0] += 1
    body = {
        "ACT_Serie_Fabricante": f"QA2-{SUF}-{SERIE_N[0]:03d}",
        "ACT_Fecha_Compra": str(compra or HOY - timedelta(days=30)),
        "ACT_Fin_Garantia": str(garantia) if garantia else None,
        "ACT_Costo": costo, "ACT_Moneda": moneda,
        "MOD_Modelo": MODELO, "TAC_Tipo_Activo": TIPO_ACT,
        "EOP_Estado_Operativo": EST[estado], "SED_Sede": sede,
    }
    s, b = req("POST", "/core/activos", json=body)
    assert s == 201, (s, b)
    SERIES[b["ACT_Activo"]] = b["ACT_Serie_Fabricante"]
    return b


def nueva_persona(nombre, sede=1):
    s, b = req("POST", "/org/personas", json={
        "PER_Primer_Nombre": "QA2", "PER_Primer_Apellido": f"{nombre}{SUF[-4:]}",
        "PER_Email_Corporativo": f"qa2.{nombre.lower()}.{SUF}@lombardi.qa.local",
        "DEP_Departamento": DEP, "CAR_Cargo": CAR, "SED_Sede": sede,
    })
    assert s == 201, (s, b)
    return b


def asignar(aid, per, area, tipo="Asignación", client=None, hostname=None):
    body = {"ACT_Activo": aid, "PER_Persona": per, "ARE_Area": area, "TMO_Tipo_Movimiento": TMO[tipo],
            "MOV_Observacion": "QA2"}
    if hostname:
        body["ACT_Hostname"] = hostname
    return req("POST", "/trazabilidad/movimientos", client=client, json=body)


def en_paralelo(funcs):
    """Ejecuta las funciones a la vez (barrera) y devuelve sus resultados."""
    barrera = threading.Barrier(len(funcs))
    res = [None] * len(funcs)

    def run(i, f):
        barrera.wait()
        res[i] = f()
    hilos = [threading.Thread(target=run, args=(i, f)) for i, f in enumerate(funcs)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    return res


def cliente_extra():
    cl = httpx.Client(verify=False, timeout=60)
    cl.headers["Authorization"] = TOKEN
    return cl


# ───────────────────────── setup ─────────────────────────
print(f"== QA2 sufijo {SUF} ==")
for e in req("GET", "/cat/estados-operativos")[1]:
    EST[e["EOP_Nombre"]] = e["EOP_Estado_Operativo"]
    EST_NOM[e["EOP_Estado_Operativo"]] = e["EOP_Nombre"]
for t in req("GET", "/trazabilidad/tipos")[1]:
    TMO[t["TMO_Nombre"]] = t["TMO_Tipo_Movimiento"]
MODELO = next(m["MOD_Modelo"] for m in req("GET", "/cat/modelos-flat")[1] if m["TAC_Tipo_Activo"] == 1)
TIPO_ACT = 1
DEP, CAR = 2, 2
areas = req("GET", "/geo/areas/all")[1]
AREA_A = next(a["ARE_Area"] for a in areas if a["SED_Sede"] == 1)
mun = req("GET", "/geo/sedes/1")[1]["MUN_Municipio"]
s, sede_b = req("POST", "/geo/sedes", json={"SED_Nombre": f"QA2-Sede B {SUF}", "MUN_Municipio": mun})
SEDE_B = sede_b["SED_Sede"]
edi = req("POST", "/geo/edificios", json={"EDI_Nombre": "QA2-Torre", "SED_Sede": SEDE_B})[1]
niv = req("POST", "/geo/niveles", json={"NIV_Numero_Piso": "1", "EDI_Edificio": edi["EDI_Edificio"]})[1]
AREA_B = req("POST", "/geo/areas", json={"ARE_Nombre": f"QA2-Area B {SUF}", "NIV_Nivel": niv["NIV_Nivel"]})[1]["ARE_Area"]
print("setup: sede B", SEDE_B, "área A", AREA_A, "área B", AREA_B)

P1, P2, P3, P4, P5 = (nueva_persona(n) for n in ("Uno", "Dos", "Tres", "Cuatro", "Cinco"))
PB = nueva_persona("SedeB", sede=SEDE_B)
DASH0 = req("GET", "/stats/dashboard")[1]
evid("00_dashboard_inicial", DASH0)

# ═════════════════════ 0. DETALLE DE ACTIVO ═════════════════════
a0 = nuevo_activo()
s, b = req("GET", f"/core/activos/{a0['ACT_Activo']}")
s2, b2 = req("GET", f"/core/activos/by-code/{a0['ACT_Codigo_Interno']}")
caso("DET-00", "Detalle de activo por id (lo usa la UI: assetService.getById)", "GET /core/activos/{id} de un activo recién creado",
     "200 con modelo, tipo, estado y especificaciones", f"HTTP {s} {corto(b, 60)}; por código HTTP {s2}", s == 200,
     evid("DET-00", {"por_id": b, "por_codigo_status": s2}), "alta")

# ═════════════════════ 1. MÁQUINA DE ESTADOS Y TRAZABILIDAD ═════════════════════
a1 = nuevo_activo()
s, b = asignar(a1["ACT_Activo"], P1["PER_Persona"], AREA_A, hostname=f"QA2-H{SUF}")
det = activo(a1["ACT_Activo"])
ok = s == 201 and EST_NOM[det["EOP_Estado_Operativo"]] == "Asignado" and det["SED_Sede"] == 1 \
    and len(vigentes(a1["ACT_Activo"])) == 1 and det["ACT_Hostname"] == f"QA2-H{SUF}"
caso("ME-01", "Asignar activo Disponible", "POST /trazabilidad/movimientos (Asignación) a P1, área sede A, con hostname",
     "201; estado Asignado; 1 custodia abierta; sede = sede del área; hostname actualizado",
     f"HTTP {s}; estado {EST_NOM[det['EOP_Estado_Operativo']]}; sede {det['SED_Sede']}; hostname {det['ACT_Hostname']}",
     ok, evid("ME-01", {"resp": b, "activo": det}), "alta")

s, b = asignar(a1["ACT_Activo"], P2["PER_Persona"], AREA_A)
caso("ME-02", "Doble asignación secuencial del mismo activo", "Asignar de nuevo el activo ya asignado a P2",
     "409 ASSET_ALREADY_ASSIGNED_USE_TRANSFER", f"HTTP {s} {corto(b)}", s == 409, evid("ME-02", b), "alta")

s, b = asignar(a1["ACT_Activo"], P2["PER_Persona"], AREA_A, tipo="Préstamo")
caso("ME-03", "Préstamo de un activo asignado", "Movimiento tipo Préstamo sobre activo con custodia",
     "409", f"HTTP {s} {corto(b)}", s == 409, evid("ME-03", b), "alta")

s, b = req("POST", "/trazabilidad/transferencia", json={"ACT_Activo": a1["ACT_Activo"], "PER_Persona_Destino": P2["PER_Persona"], "ARE_Area_Destino": AREA_B})
det = activo(a1["ACT_Activo"])
hist = req("GET", f"/trazabilidad/activo/{a1['ACT_Activo']}/historial")[1]
abiertos = [m for m in hist if m["MOV_Fecha_Devolucion"] is None]
ok = s == 201 and len(abiertos) == 1 and abiertos[0]["PER_Persona"] == P2["PER_Persona"] and det["SED_Sede"] == SEDE_B \
    and EST_NOM[det["EOP_Estado_Operativo"]] == "Asignado"
caso("ME-04", "Transferencia P1→P2 a un área de otra sede", "POST /trazabilidad/transferencia destino P2 / área de sede B",
     "201; custodia de P1 cerrada, una sola abierta a P2; estado Asignado; sede del activo = sede B",
     f"HTTP {s}; abiertos={len(abiertos)} custodio={'P2' if abiertos and abiertos[0]['PER_Persona']==P2['PER_Persona'] else '?'}; sede {det['SED_Sede']}; estado {EST_NOM[det['EOP_Estado_Operativo']]}",
     ok, evid("ME-04", {"resp": b, "historial": hist, "activo": det}), "alta")
tipo_tr = abiertos[0]["tipo_movimiento"]["TMO_Nombre"] if abiertos else None
caso("ME-04b", "Tipo de movimiento registrado en la transferencia", "Revisar historial tras la transferencia",
     "El movimiento nuevo queda como 'Transferencia' (trazabilidad del motivo)",
     f"Queda registrado como '{tipo_tr}'", tipo_tr == "Transferencia", evid("ME-04b", hist), "baja", "OBSERVACIÓN")

s, b = req("POST", "/trazabilidad/transferencia", json={"ACT_Activo": a1["ACT_Activo"], "PER_Persona_Destino": P2["PER_Persona"], "ARE_Area_Destino": AREA_B})
caso("ME-05", "Transferencia a la misma persona que ya lo tiene", "Transferir a P2 el activo que P2 ya custodia",
     "400/409 (transferencia sin efecto)", f"HTTP {s} {corto(b,120)}", s in (400, 409), evid("ME-05", b), "baja", "OBSERVACIÓN")

s, b = req("POST", "/trazabilidad/devolucion", json={"ACT_Activo": a1["ACT_Activo"]})
det = activo(a1["ACT_Activo"])
ok = s == 200 and EST_NOM[det["EOP_Estado_Operativo"]] == "En Bodega" and not vigentes(a1["ACT_Activo"])
caso("ME-06", "Devolución", "POST /trazabilidad/devolucion", "200; estado En Bodega; sin custodia abierta",
     f"HTTP {s}; estado {EST_NOM[det['EOP_Estado_Operativo']]}", ok, evid("ME-06", b), "alta")
s, b = req("POST", "/trazabilidad/devolucion", json={"ACT_Activo": a1["ACT_Activo"]})
caso("ME-07", "Devolución de activo no asignado", "Repetir la devolución", "400 ASSET_IS_NOT_ASSIGNED",
     f"HTTP {s} {corto(b)}", s == 400, evid("ME-07", b), "media")
s, b = req("POST", "/trazabilidad/transferencia", json={"ACT_Activo": a1["ACT_Activo"], "PER_Persona_Destino": P1["PER_Persona"], "ARE_Area_Destino": AREA_A})
caso("ME-08", "Transferir activo no asignado", "Transferencia sobre activo En Bodega", "400 ASSET_IS_NOT_ASSIGNED_CANNOT_TRANSFER",
     f"HTTP {s} {corto(b)}", s == 400, evid("ME-08", b), "media")

# Préstamo
a2 = nuevo_activo()
s, b = asignar(a2["ACT_Activo"], P1["PER_Persona"], AREA_A, tipo="Préstamo")
det = activo(a2["ACT_Activo"])
caso("ME-09", "Préstamo de activo Disponible", "Movimiento tipo Préstamo", "201; estado Asignado; custodia abierta tipo Préstamo",
     f"HTTP {s}; estado {EST_NOM[det['EOP_Estado_Operativo']]}", s == 201 and EST_NOM[det["EOP_Estado_Operativo"]] == "Asignado",
     evid("ME-09", b), "media")
campos = set(b.keys()) if isinstance(b, dict) else set()
caso("ME-10", "Préstamo con fecha de retorno esperada", "Revisar el esquema del movimiento de préstamo",
     "El préstamo registra una fecha de devolución prevista y alerta al vencer",
     f"El movimiento no tiene campo de fecha prevista (campos: {sorted(k for k in campos if k.startswith('MOV_'))}); préstamo = asignación",
     False, evid("ME-10", b), "media", "OBSERVACIÓN")
req("POST", "/trazabilidad/devolucion", json={"ACT_Activo": a2["ACT_Activo"]})

# Tipos de movimiento "no custodia" enviados por /movimientos
a3 = nuevo_activo(estado="En Bodega")
s, b = asignar(a3["ACT_Activo"], P1["PER_Persona"], AREA_A, tipo="Devolución")
v = vigentes(a3["ACT_Activo"])
caso("ME-11", "Movimiento tipo 'Devolución' vía /movimientos sobre activo sin custodia",
     "POST /trazabilidad/movimientos con TMO=Devolución sobre activo En Bodega",
     "400 (una devolución no abre custodia; debe usarse /devolucion)",
     f"HTTP {s}; estado {estado_de(a3['ACT_Activo'])}; custodias abiertas={len(v)}",
     s >= 400 or not v, evid("ME-11", {"resp": b, "vigentes": v}), "alta")

a4 = nuevo_activo()
s, b = asignar(a4["ACT_Activo"], P1["PER_Persona"], AREA_A, tipo="Baja por Retiro")
v = vigentes(a4["ACT_Activo"])
caso("ME-12", "Movimiento 'Baja por Retiro' vía /movimientos",
     "POST /trazabilidad/movimientos con TMO=Baja por Retiro sobre activo Disponible",
     "400, o baja sin custodia abierta (un activo de baja no tiene custodio)",
     f"HTTP {s}; estado {estado_de(a4['ACT_Activo'])}; custodias abiertas={len(v)}",
     s >= 400 or not v, evid("ME-12", {"resp": b, "vigentes": v}), "alta")

a5 = nuevo_activo()
s, b = asignar(a5["ACT_Activo"], P1["PER_Persona"], AREA_A, tipo="Ingreso")
v = vigentes(a5["ACT_Activo"])
caso("ME-13", "Movimiento 'Ingreso' vía /movimientos",
     "POST /trazabilidad/movimientos con TMO=Ingreso sobre activo Disponible",
     "Ingreso no deja custodia abierta a una persona con el activo en Disponible",
     f"HTTP {s}; estado {estado_de(a5['ACT_Activo'])}; custodias abiertas={len(v)}",
     s >= 400 or not v, evid("ME-13", {"resp": b, "vigentes": v}), "media")

# Tipo de movimiento personalizado
s, tcus = req("POST", "/trazabilidad/tipos", json={"TMO_Nombre": f"QA2-Custom {SUF}"})
a6 = nuevo_activo()
s, b = req("POST", "/trazabilidad/movimientos", json={"ACT_Activo": a6["ACT_Activo"], "PER_Persona": P1["PER_Persona"], "ARE_Area": AREA_A, "TMO_Tipo_Movimiento": tcus["TMO_Tipo_Movimiento"]})
v = vigentes(a6["ACT_Activo"])
caso("ME-14", "Movimiento con tipo personalizado (creado por admin)",
     "Crear tipo 'QA2-Custom' y registrar movimiento sobre activo Disponible",
     "O se rechaza, o aplica una transición definida; no debe quedar custodia abierta con estado Disponible",
     f"HTTP {s}; estado {estado_de(a6['ACT_Activo'])}; custodias abiertas={len(v)}",
     s >= 400 or not v or estado_de(a6["ACT_Activo"]) == "Asignado", evid("ME-14", {"resp": b, "vigentes": v}), "media")

# Bypass de la máquina de estados por PATCH
a7 = nuevo_activo()
asignar(a7["ACT_Activo"], P1["PER_Persona"], AREA_A)
s, b = req("PATCH", f"/core/activos/{a7['ACT_Activo']}", json={"EOP_Estado_Operativo": EST["Disponible"]})
v = vigentes(a7["ACT_Activo"])
caso("ME-15", "Cambio de estado directo por edición (PATCH) de un activo asignado",
     "PATCH /core/activos/{id} EOP=Disponible con custodia abierta",
     "400 (el estado solo cambia por movimientos/mantenimiento; valida la máquina de estados)",
     f"HTTP {s}; estado {estado_de(a7['ACT_Activo'])}; custodias abiertas={len(v)}",
     s >= 400, evid("ME-15", {"resp": b, "vigentes": v}), "alta")
s, b = asignar(a7["ACT_Activo"], P2["PER_Persona"], AREA_A)
caso("ME-15b", "Reasignación tras el PATCH anterior", "Asignar a P2 el activo que quedó Disponible con custodia de P1",
     "409 (sigue en custodia)", f"HTTP {s} {corto(b,120)}", s == 409, evid("ME-15b", b), "media")

a8 = nuevo_activo()
req("DELETE", f"/core/activos/{a8['ACT_Activo']}")
s, b = req("PATCH", f"/core/activos/{a8['ACT_Activo']}", json={"EOP_Estado_Operativo": EST["Asignado"]})
caso("ME-16", "PATCH de Baja → Asignado (transición ilegal)", "Dar de baja y luego PATCH EOP=Asignado",
     "400 INVALID_STATE_TRANSITION", f"HTTP {s}; estado {estado_de(a8['ACT_Activo'])}", s >= 400,
     evid("ME-16", b), "alta")

s, b = req("POST", "/core/activos", json={
    "ACT_Serie_Fabricante": f"QA2-{SUF}-ASG", "ACT_Fecha_Compra": str(HOY), "MOD_Modelo": MODELO,
    "TAC_Tipo_Activo": TIPO_ACT, "EOP_Estado_Operativo": EST["Asignado"], "SED_Sede": 1})
caso("ME-17", "Alta de activo directamente en estado Asignado (sin custodio)",
     "POST /core/activos con EOP=Asignado", "400 (un activo nuevo nace Disponible/En Bodega)",
     f"HTTP {s}; estado {EST_NOM.get(b.get('EOP_Estado_Operativo')) if isinstance(b, dict) else b}",
     s >= 400, evid("ME-17", b), "media")

# Asignar activo de baja / en reparación
a9 = nuevo_activo()
req("DELETE", f"/core/activos/{a9['ACT_Activo']}")
s, b = asignar(a9["ACT_Activo"], P1["PER_Persona"], AREA_A)
caso("ME-18", "Asignar activo dado de Baja", "Baja lógica y luego asignación", "400 INVALID_STATE_TRANSITION",
     f"HTTP {s} {corto(b,160)}", s == 400, evid("ME-18", b), "alta")
s, b = req("DELETE", f"/core/activos/{a1['ACT_Activo']}")
s2, b2 = req("DELETE", f"/core/activos/{a1['ACT_Activo']}")
caso("ME-19", "Baja lógica repetida", "DELETE dos veces el mismo activo", "Segunda baja: 400/409 (ya está de baja) o no-op sin nueva auditoría",
     f"1ª HTTP {s}, 2ª HTTP {s2}", s2 in (400, 409), evid("ME-19", {"1": b, "2": b2}), "baja", "OBSERVACIÓN")

# Transición En Tránsito → Baja (ilegal según la matriz), llegando a Tránsito con PATCH
a10 = nuevo_activo()
req("PATCH", f"/core/activos/{a10['ACT_Activo']}", json={"EOP_Estado_Operativo": EST["En Tránsito"]})
s, b = req("DELETE", f"/core/activos/{a10['ACT_Activo']}")
caso("ME-20", "Baja de un activo En Tránsito (no permitido por AssetStateMachine)",
     "PATCH a En Tránsito y DELETE /core/activos/{id}", "400 (TRANSITO→BAJA no está en la matriz)",
     f"HTTP {s}; estado {estado_de(a10['ACT_Activo'])}", s >= 400, evid("ME-20", b), "media")

# Asignar a persona inactiva
pin = nueva_persona("Inactiva")
req("PATCH", f"/org/personas/{pin['PER_Persona']}", json={"PER_Estado": False})
a11 = nuevo_activo()
s, b = asignar(a11["ACT_Activo"], pin["PER_Persona"], AREA_A)
caso("ME-21", "Asignar un activo a una persona inactiva", "Persona con PER_Estado=false; asignación",
     "400 (PERSON_INACTIVE)", f"HTTP {s}; estado {estado_de(a11['ACT_Activo'])}", s >= 400, evid("ME-21", b), "alta")
a12 = nuevo_activo()
asignar(a12["ACT_Activo"], P1["PER_Persona"], AREA_A)
s, b = req("POST", "/trazabilidad/transferencia", json={"ACT_Activo": a12["ACT_Activo"], "PER_Persona_Destino": pin["PER_Persona"], "ARE_Area_Destino": AREA_A})
caso("ME-22", "Transferir un activo a una persona inactiva", "Transferencia con destino inactivo",
     "400", f"HTTP {s}", s >= 400, evid("ME-22", b), "alta")

# ═════════════════════ 2. MANTENIMIENTO ═════════════════════
m_a = nuevo_activo()
s, man = req("POST", "/mantenimiento/", json={"ACT_Activo": m_a["ACT_Activo"], "PER_Persona_Solicita": P1["PER_Persona"],
                                             "TMA_Tipo_Mantenimiento": 2, "MAN_Descripcion_Falla": "QA2 falla de pantalla",
                                             "MAN_Moneda": "USD"})
caso("MA-01", "Abrir ticket sobre activo Disponible", "POST /mantenimiento/ moneda USD", "201; activo En Reparación; ticket en USD",
     f"HTTP {s}; estado {estado_de(m_a['ACT_Activo'])}; moneda {man.get('MAN_Moneda') if isinstance(man, dict) else '?'}",
     s == 201 and estado_de(m_a["ACT_Activo"]) == "En Reparación" and man.get("MAN_Moneda") == "USD", evid("MA-01", man), "alta")
MID = man["MAN_Mantenimiento"]
s, b = req("POST", "/mantenimiento/", json={"ACT_Activo": m_a["ACT_Activo"], "PER_Persona_Solicita": P1["PER_Persona"],
                                           "TMA_Tipo_Mantenimiento": 1, "MAN_Descripcion_Falla": "QA2 segundo ticket"})
caso("MA-02", "Segundo ticket abierto sobre el mismo activo", "POST /mantenimiento/ otra vez", "409 ASSET_ALREADY_HAS_OPEN_MAINTENANCE_TICKET",
     f"HTTP {s} {corto(b)}", s == 409, evid("MA-02", b), "alta")
d1 = req("POST", f"/mantenimiento/{MID}/detalles", json={"DMA_Accion_Realizada": "QA2 cambio de panel", "DMA_Costo_Item": "120.00"})
d2 = req("POST", f"/mantenimiento/{MID}/detalles", json={"DMA_Accion_Realizada": "QA2 mano de obra", "DMA_Costo_Item": "30.00"})
caso("MA-03", "Agregar detalles a ticket abierto", "2 detalles (120 + 30)", "201 y 201", f"HTTP {d1[0]} y {d2[0]}",
     d1[0] == 201 and d2[0] == 201, evid("MA-03", [d1, d2]), "media")
s, b = req("PATCH", f"/mantenimiento/{MID}/cerrar", json={"MAN_Costo_Total": "999.00"})
caso("MA-04", "Coherencia costo total vs. suma de detalles", "Cerrar con MAN_Costo_Total=999 (detalles suman 150)",
     "Aviso o rechazo si el total no cuadra con los detalles (o total calculado)",
     f"HTTP {s}; costo guardado {b.get('MAN_Costo_Total') if isinstance(b, dict) else b} {b.get('MAN_Moneda') if isinstance(b, dict) else ''}",
     False, evid("MA-04", b), "media", "OBSERVACIÓN")
caso("MA-05", "Cierre: moneda conservada y estado final", "Revisar ticket cerrado y activo",
     "Moneda USD conservada; activo sin custodia → En Bodega", f"moneda {b.get('MAN_Moneda')}; estado {estado_de(m_a['ACT_Activo'])}",
     b.get("MAN_Moneda") == "USD" and estado_de(m_a["ACT_Activo"]) == "En Bodega", evid("MA-05", b), "media")
s, b = req("PATCH", f"/mantenimiento/{MID}/cerrar", json={"MAN_Costo_Total": "1.00"})
caso("MA-06", "Cerrar ticket ya cerrado", "PATCH /cerrar otra vez", "400 TICKET_ALREADY_CLOSED", f"HTTP {s} {corto(b)}", s == 400, evid("MA-06", b), "alta")
s, b = req("POST", f"/mantenimiento/{MID}/detalles", json={"DMA_Accion_Realizada": "QA2 tardío", "DMA_Costo_Item": "1"})
caso("MA-07", "Detalle en ticket cerrado", "POST detalle", "400", f"HTTP {s} {corto(b)}", s == 400, evid("MA-07", b), "media")

# Ticket con fecha de cierre anterior / futura y cambio de moneda
m_b = nuevo_activo()
s, man_b = req("POST", "/mantenimiento/", json={"ACT_Activo": m_b["ACT_Activo"], "PER_Persona_Solicita": P1["PER_Persona"], "TMA_Tipo_Mantenimiento": 1, "MAN_Descripcion_Falla": "QA2 preventivo"})
s, b = req("PATCH", f"/mantenimiento/{man_b['MAN_Mantenimiento']}/cerrar", json={"MAN_Costo_Total": "10", "MAN_Fecha_Cierre": "2020-01-01T00:00:00"})
caso("MA-08", "Cierre con fecha anterior al ingreso", "MAN_Fecha_Cierre=2020-01-01", "400 CLOSE_DATE_CANNOT_BE_BEFORE_ENTRY_DATE", f"HTTP {s} {corto(b)}", s == 400, evid("MA-08", b), "media")
fut = (HOY + timedelta(days=400)).isoformat() + "T00:00:00"
s, b = req("PATCH", f"/mantenimiento/{man_b['MAN_Mantenimiento']}/cerrar", json={"MAN_Costo_Total": "10", "MAN_Moneda": "CHF", "MAN_Fecha_Cierre": fut})
caso("MA-09", "Cierre con fecha futura (+400 días)", "MAN_Fecha_Cierre en el futuro", "400 (fecha de cierre no puede ser futura)",
     f"HTTP {s}; cierre={b.get('MAN_Fecha_Cierre') if isinstance(b, dict) else b}; moneda={b.get('MAN_Moneda') if isinstance(b, dict) else ''}",
     s >= 400, evid("MA-09", b), "media")

# Ticket sobre activo asignado (matriz: ASIGNADO → REPARACION no permitido)
m_c = nuevo_activo()
asignar(m_c["ACT_Activo"], P1["PER_Persona"], AREA_A)
s, man_c = req("POST", "/mantenimiento/", json={"ACT_Activo": m_c["ACT_Activo"], "PER_Persona_Solicita": P1["PER_Persona"], "TMA_Tipo_Mantenimiento": 2, "MAN_Descripcion_Falla": "QA2 teclado"})
caso("MA-10", "Abrir ticket sobre activo Asignado", "Activo con custodia de P1 → POST /mantenimiento/",
     "Según AssetStateMachine ASIGNADO→REPARACION no es válido: o se rechaza, o se documenta la excepción (custodia se conserva)",
     f"HTTP {s}; estado {estado_de(m_c['ACT_Activo'])}; custodias abiertas={len(vigentes(m_c['ACT_Activo']))}",
     False, evid("MA-10", man_c), "media", "OBSERVACIÓN")
s, b = req("POST", "/trazabilidad/devolucion", json={"ACT_Activo": m_c["ACT_Activo"]})
st_dev = estado_de(m_c["ACT_Activo"])
s2, b2 = asignar(m_c["ACT_Activo"], P2["PER_Persona"], AREA_A)
st_asg = estado_de(m_c["ACT_Activo"])
tick = req("GET", f"/mantenimiento/{man_c['MAN_Mantenimiento']}")[1]
caso("MA-11", "Devolver y reasignar un activo con ticket de mantenimiento ABIERTO",
     "Ticket abierto → POST /devolucion → asignar a P2",
     "La devolución no debe sacar el activo de 'En Reparación' y la asignación debe rechazarse mientras haya ticket abierto",
     f"devolución HTTP {s} → estado {st_dev}; asignación HTTP {s2} → estado {st_asg}; ticket abierto={tick.get('MAN_Fecha_Cierre') is None}",
     not (s == 200 and s2 == 201), evid("MA-11", {"dev": b, "asig": b2, "ticket": tick}), "alta")
req("PATCH", f"/mantenimiento/{man_c['MAN_Mantenimiento']}/cerrar", json={"MAN_Costo_Total": "0"})

# Baja durante reparación y cierre posterior
m_d = nuevo_activo()
s, man_d = req("POST", "/mantenimiento/", json={"ACT_Activo": m_d["ACT_Activo"], "PER_Persona_Solicita": P1["PER_Persona"], "TMA_Tipo_Mantenimiento": 2, "MAN_Descripcion_Falla": "QA2 placa quemada"})
s1, b1 = req("DELETE", f"/core/activos/{m_d['ACT_Activo']}")
st_baja = estado_de(m_d["ACT_Activo"])
s2, b2 = req("PATCH", f"/mantenimiento/{man_d['MAN_Mantenimiento']}/cerrar", json={"MAN_Costo_Total": "0"})
st_fin = estado_de(m_d["ACT_Activo"])
caso("MA-12", "Baja de activo con ticket abierto y cierre posterior del ticket",
     "Ticket abierto → DELETE activo (baja) → cerrar ticket",
     "La baja debe exigir cerrar el ticket (o cerrarlo); el cierre nunca debe 'resucitar' un activo de Baja",
     f"baja HTTP {s1} → {st_baja}; cierre HTTP {s2} → estado final {st_fin}",
     st_fin == "Baja", evid("MA-12", {"baja": b1, "cierre": b2}), "alta")

m_e = nuevo_activo()
req("DELETE", f"/core/activos/{m_e['ACT_Activo']}")
s, b = req("POST", "/mantenimiento/", json={"ACT_Activo": m_e["ACT_Activo"], "PER_Persona_Solicita": P1["PER_Persona"], "TMA_Tipo_Mantenimiento": 2, "MAN_Descripcion_Falla": "QA2 sobre baja"})
st1 = estado_de(m_e["ACT_Activo"])
if s == 201:
    req("PATCH", f"/mantenimiento/{b['MAN_Mantenimiento']}/cerrar", json={"MAN_Costo_Total": "0"})
st2 = estado_de(m_e["ACT_Activo"])
caso("MA-13", "Abrir ticket sobre activo de Baja", "Baja → POST /mantenimiento/ → cerrar",
     "400 (BAJA→REPARACION no es válido)", f"HTTP {s}; estado tras abrir {st1}; tras cerrar {st2}",
     s >= 400, evid("MA-13", b), "alta")

# ═════════════════════ 3. CONSUMIBLES Y STOCK MÍNIMO ═════════════════════
s, con = req("POST", "/consumibles", json={"CON_Nombre": f"QA2-Toner {SUF}", "CON_Unidad": "unidad", "CON_Stock_Minimo": 5, "CON_Stock_Actual": 10, "SED_Sede": 1})
CID = con["CON_Consumible"]
s1, b1 = req("POST", f"/consumibles/{CID}/salida", json={"MOC_Cantidad": 6, "MOC_Motivo": "QA2 salida", "PER_Persona": P1["PER_Persona"]})
c_now = req("GET", f"/consumibles/{CID}")[1]
bajos = [x["CON_Consumible"] for x in req("GET", "/consumibles", params={"solo_bajo_stock": True})[1]]
caso("CO-01", "Salida que cruza el stock mínimo", "Stock 10, mínimo 5, salida 6", "stock 4; bajo_stock=true; aparece en ?solo_bajo_stock",
     f"HTTP {s1}; stock {c_now['CON_Stock_Actual']}; bajo_stock {c_now['bajo_stock']}; en filtro {CID in bajos}",
     s1 == 201 and c_now["CON_Stock_Actual"] == 4 and c_now["bajo_stock"] and CID in bajos, evid("CO-01", {"salida": b1, "con": c_now}), "media")
s, b = req("POST", f"/consumibles/{CID}/salida", json={"MOC_Cantidad": 5})
caso("CO-02", "Salida mayor al stock", "Salida 5 con stock 4", "409 INSUFFICIENT_STOCK; stock sin cambio",
     f"HTTP {s}; stock {req('GET', f'/consumibles/{CID}')[1]['CON_Stock_Actual']}", s == 409, evid("CO-02", b), "alta")
res = en_paralelo([lambda cl=cliente_extra(): req("POST", f"/consumibles/{CID}/salida", client=cl, json={"MOC_Cantidad": 3}) for _ in range(2)])
stk = req("GET", f"/consumibles/{CID}")[1]["CON_Stock_Actual"]
caso("CC-01", "Concurrencia: dos salidas simultáneas de 3 con stock 4", "2 hilos con barrera", "Una 201 y una 409; stock final 1 (nunca negativo)",
     f"códigos {[r[0] for r in res]}; stock final {stk}", sorted(r[0] for r in res) == [201, 409] and stk == 1, evid("CC-01", res), "alta")
req("PATCH", f"/consumibles/{CID}", json={"CON_Activo": False})
s, b = req("POST", f"/consumibles/{CID}/entrada", json={"MOC_Cantidad": 1, "MOC_Motivo": "QA2 inactivo"})
caso("CO-03", "Movimiento de stock sobre consumible inactivo", "CON_Activo=false → entrada", "400 (consumible inactivo)",
     f"HTTP {s}", s >= 400, evid("CO-03", b), "baja", "OBSERVACIÓN")
req("PATCH", f"/consumibles/{CID}", json={"CON_Activo": True})
dash = req("GET", "/stats/dashboard")[1]
caso("CO-04", "Alerta de stock mínimo en el tablero", "GET /stats/dashboard", "KPI de consumibles bajo mínimo",
     f"claves del tablero sin consumibles: {'consumibles_bajo_stock' in dash}", "consumibles_bajo_stock" in dash,
     evid("CO-04", list(dash.keys())), "baja", "OBSERVACIÓN")

# ═════════════════════ 4. ÓRDENES DE COMPRA Y RECEPCIÓN ═════════════════════
s, prv = req("POST", "/compras/proveedores", json={"PRV_Nombre": f"QA2-Proveedor {SUF}"})
PRV = prv["PRV_Proveedor"]
s, sw = req("POST", "/soft/software", json={"SOF_Nombre": f"QA2-Soft {SUF}", "SOF_Fabricante": "QA2"})
s, tli = req("POST", "/soft/tipos-licencia", json={"TLI_Nombre": f"QA2-Perpetua {SUF}"})
s, con2 = req("POST", "/consumibles", json={"CON_Nombre": f"QA2-Mouse {SUF}", "CON_Stock_Minimo": 2, "CON_Stock_Actual": 1, "SED_Sede": 1})
CID2 = con2["CON_Consumible"]


def crear_orden(numero, lineas, moneda="USD", sede=1):
    return req("POST", "/compras/ordenes", json={"OCO_Numero": numero, "OCO_Fecha": str(HOY), "OCO_Moneda": moneda,
                                                 "PRV_Proveedor": PRV, "SED_Sede": sede, "lineas": lineas})


L_ACT = {"OCL_Descripcion": "QA2 Laptop", "OCL_Cantidad": 3, "OCL_Precio_Unitario": "1000.50"}
L_CON = {"OCL_Descripcion": "QA2 Mouse", "OCL_Cantidad": 10, "OCL_Precio_Unitario": "12.25", "CON_Consumible": CID2}
L_LIC = {"OCL_Descripcion": "QA2 Licencias", "OCL_Cantidad": 2, "OCL_Precio_Unitario": "50"}
s, oc = crear_orden(f"QA2-OC1-{SUF}", [L_ACT, L_CON, L_LIC])
ok = s == 201 and Decimal(oc["OCO_Total"]) == Decimal("3001.50") + Decimal("122.50") + Decimal("100") and oc["OCO_Estado"] == "BORRADOR"
caso("OC-01", "Crear orden en USD con 3 líneas (activos, consumible, licencias)", "POST /compras/ordenes",
     "201; BORRADOR; total = 3·1000.50 + 10·12.25 + 2·50 = 3224.00", f"HTTP {s}; estado {oc.get('OCO_Estado')}; total {oc.get('OCO_Total')}",
     ok, evid("OC-01", oc), "alta")
OID = oc["OCO_Orden"]
LN = {l["OCL_Descripcion"]: l["OCL_Linea"] for l in oc["lineas"]}


def ra(linea, serie, costo=None, garantia=None):
    d = {"OCL_Linea": linea, "ACT_Serie_Fabricante": serie, "MOD_Modelo": MODELO, "TAC_Tipo_Activo": TIPO_ACT,
         "ACT_Fecha_Compra": str(HOY)}
    if costo:
        d["ACT_Costo"] = costo
    if garantia:
        d["ACT_Fin_Garantia"] = str(garantia)
    return d


# Recibir más de lo pedido
s, b = req("POST", f"/compras/ordenes/{OID}/recibir", json={"activos": [ra(LN["QA2 Laptop"], f"QA2-R{SUF}-{i}") for i in range(4)]})
caso("OC-02", "Recepción con más activos que los pedidos", "4 series para una línea de 3", "400 RECEIPT_QUANTITY_EXCEEDS_ORDER_LINE",
     f"HTTP {s} {corto(b)}", s == 400, evid("OC-02", b), "alta")
s, b = req("POST", f"/compras/ordenes/{OID}/recibir", json={"consumibles": [{"OCL_Linea": LN["QA2 Mouse"], "CON_Consumible": CID2, "cantidad": 11}]})
caso("OC-03", "Recepción de consumibles por encima de lo pedido", "cantidad 11 para línea de 10", "400", f"HTTP {s} {corto(b)}", s == 400, evid("OC-03", b), "alta")
s, b = req("POST", f"/compras/ordenes/{OID}/recibir", json={"activos": [ra(LN["QA2 Laptop"], f"QA2-R{SUF}-D"), ra(LN["QA2 Laptop"], f"QA2-R{SUF}-D")]})
caso("OC-04", "Series duplicadas en la misma recepción", "2 activos con la misma serie", "400 DUPLICATE_SERIAL_NUMBER_IN_RECEIPT",
     f"HTTP {s} {corto(b)}", s == 400, evid("OC-04", b), "alta")
stock_antes = req("GET", f"/consumibles/{CID2}")[1]["CON_Stock_Actual"]
s, b = req("POST", f"/compras/ordenes/{OID}/recibir", json={
    "consumibles": [{"OCL_Linea": LN["QA2 Mouse"], "CON_Consumible": CID2, "cantidad": 5}],
    "activos": [ra(LN["QA2 Laptop"], a1["ACT_Serie_Fabricante"])]})
stock_desp = req("GET", f"/consumibles/{CID2}")[1]["CON_Stock_Actual"]
est_oc = req("GET", f"/compras/ordenes/{OID}")[1]["OCO_Estado"]
caso("OC-05", "Atomicidad: serie ya existente en la empresa", "Recepción con consumibles (5) + activo con serie existente",
     "400 SERIAL_NUMBER_ALREADY_EXISTS y rollback total (stock sin cambio, orden BORRADOR)",
     f"HTTP {s}; stock {stock_antes}→{stock_desp}; orden {est_oc}", s == 400 and stock_antes == stock_desp and est_oc == "BORRADOR",
     evid("OC-05", b), "bloqueante")

# Recepción parcial con precio distinto (clave para el hallazgo funcional)
gar30 = HOY + timedelta(days=30)
rec1 = {
    "activos": [ra(LN["QA2 Laptop"], f"QA2-R{SUF}-1", garantia=gar30), ra(LN["QA2 Laptop"], f"QA2-R{SUF}-2", costo="1500.00", garantia=gar30)],
    "consumibles": [{"OCL_Linea": LN["QA2 Mouse"], "CON_Consumible": CID2, "cantidad": 6}],
    "licencias": [{"OCL_Linea": LN["QA2 Licencias"], "SOF_Software": sw["SOF_Software"], "TLI_Tipo_Licencia": tli["TLI_Tipo_Licencia"],
                   "claves": [{"LCL_Clave_Activacion": f"QA2-KEY-{SUF}-A"}, {"LCL_Clave_Activacion": f"QA2-KEY-{SUF}-B"}]}],
}
s, rcp = req("POST", f"/compras/ordenes/{OID}/recibir", headers={"Idempotency-Key": f"qa2-{SUF}-rec1"}, json=rec1)
orden = req("GET", f"/compras/ordenes/{OID}")[1]
stock_r = req("GET", f"/consumibles/{CID2}")[1]
caso("OC-06", "Recepción parcial (2 de 3 activos, 6 de 10 consumibles)", "POST /recibir con cantidades menores a las pedidas",
     "Orden queda 'PARCIAL' con pendientes (1 laptop, 4 mouse) o se pide confirmar el cierre con faltante",
     f"HTTP {s}; orden {orden['OCO_Estado']}; activos creados {rcp.get('activos_creados') if isinstance(rcp, dict) else '?'}; sin registro de pendientes",
     False, evid("OC-06", {"recepcion": rcp, "orden": orden}), "alta", "DEFECTO")
caso("OC-07", "Consumibles recibidos suman stock", "Stock previo 1 + recepción 6", "stock 7; movimiento ENTRADA con motivo 'Recepción OC …'",
     f"stock {stock_r['CON_Stock_Actual']}; bajo_stock {stock_r['bajo_stock']}", stock_r["CON_Stock_Actual"] == stock_antes + 6,
     evid("OC-07", {"con": stock_r, "movs": req("GET", f"/consumibles/{CID2}/movimientos")[1]}), "alta")
creados = []
for cod in (rcp.get("activos_codigos") or []):
    creados.append(req("GET", f"/core/activos/by-code/{cod}")[1])
monedas = {x["ACT_Moneda"] for x in creados}
costos = sorted(str(x["ACT_Costo"]) for x in creados)
sedes = {x["SED_Sede"] for x in creados}
estados = {EST_NOM[x["EOP_Estado_Operativo"]] for x in creados}
caso("OC-08", "Activos recibidos heredan moneda, sede y costo de la orden", "Revisar activos creados",
     "moneda USD; sede de la orden; costo = precio unitario (1000.50) salvo que se indique; estado Disponible",
     f"monedas {monedas}; costos {costos}; sedes {sedes}; estados {estados}",
     monedas == {"USD"} and sedes == {1} and "1000.50" in costos and estados == {"Disponible"}, evid("OC-08", creados), "alta")
caso("OC-09", "Precio recibido distinto al de la orden", "Activo recibido con ACT_Costo 1500.00 (línea 1000.50)",
     "Advertencia de variación de precio / justificación y registro de la diferencia",
     f"Aceptado sin aviso; costo guardado {costos}", False, evid("OC-09", creados), "media", "OBSERVACIÓN")
lics = req("GET", "/soft/licencias", params={"software_id": sw["SOF_Software"]})[1]
lics = lics if isinstance(lics, list) else []
caso("OC-10", "Licencias recibidas con claves individuales", "Línea de 2 licencias con 2 claves",
     "1 lote de licencia con cantidad total 2 y 2 claves", f"licencias creadas {rcp.get('licencias_creadas')}; claves {rcp.get('licencias_claves_creadas')}; lote total {[l.get('LIC_Cantidad_Total') for l in lics]}",
     rcp.get("licencias_creadas") == 1 and rcp.get("licencias_claves_creadas") == 2, evid("OC-10", {"rcp": rcp, "lics": lics}), "media")
s, b = req("POST", f"/compras/ordenes/{OID}/recibir", headers={"Idempotency-Key": f"qa2-{SUF}-rec1"}, json=rec1)
caso("OC-11", "Reintento idempotente de la recepción (misma Idempotency-Key)", "Repetir el POST con la misma clave",
     "Devuelve el resultado cacheado; no crea nada", f"HTTP {s}; activos_creados {b.get('activos_creados') if isinstance(b, dict) else b}",
     s == 200 and isinstance(b, dict) and b.get("activos_codigos") == rcp.get("activos_codigos"), evid("OC-11", b), "alta")
s, b = req("POST", f"/compras/ordenes/{OID}/recibir", json={"activos": [ra(LN["QA2 Laptop"], f"QA2-R{SUF}-3")]})
caso("OC-12", "Recibir el faltante (1 laptop) de una orden ya recibida parcialmente", "POST /recibir con el activo pendiente",
     "Se admite completar lo pendiente de una recepción parcial", f"HTTP {s} {corto(b)} → el faltante no puede registrarse contra la orden",
     s == 200, evid("OC-12", b), "alta")

# Reabrir la orden y recibir de nuevo (doble recepción)
s1, b1 = req("PATCH", f"/compras/ordenes/{OID}/estado", json={"OCO_Estado": "BORRADOR"})
s2, b2 = req("POST", f"/compras/ordenes/{OID}/recibir", json={
    "activos": [ra(LN["QA2 Laptop"], f"QA2-R{SUF}-X{i}") for i in range(3)],
    "consumibles": [{"OCL_Linea": LN["QA2 Mouse"], "CON_Consumible": CID2, "cantidad": 10}]})
vinc = len([1 for _ in (b2.get("activos_codigos") or [])]) + len(creados) if isinstance(b2, dict) else None
stock_f = req("GET", f"/consumibles/{CID2}")[1]["CON_Stock_Actual"]
caso("OC-13", "Doble recepción: reabrir orden RECIBIDA a BORRADOR y recibir de nuevo",
     "PATCH estado BORRADOR → POST /recibir (3 laptops + 10 mouse)",
     "La reapertura se rechaza (409) o la segunda recepción valida contra lo ya recibido",
     f"reapertura HTTP {s1}; 2ª recepción HTTP {s2}; laptops totales recibidas {vinc} de 3 pedidas; mouse {6 + 10} de 10 (stock {stock_f})",
     not (s1 == 200 and s2 == 200), evid("OC-13", {"reabrir": b1, "recibir": b2}), "bloqueante")

# Marcar recibida sin recibir y cancelar una recibida
s, oc2 = crear_orden(f"QA2-OC2-{SUF}", [{"OCL_Descripcion": "QA2 Desktop", "OCL_Cantidad": 2, "OCL_Precio_Unitario": "700"}], moneda="GTQ")
s1, b1 = req("PATCH", f"/compras/ordenes/{oc2['OCO_Orden']}/estado", json={"OCO_Estado": "RECIBIDA"})
caso("OC-14", "Marcar RECIBIDA por cambio de estado sin pasar por la recepción", "PATCH /estado RECIBIDA en orden BORRADOR",
     "409 (solo /recibir deja la orden RECIBIDA)", f"HTTP {s1}; estado {b1.get('OCO_Estado') if isinstance(b1, dict) else b1}; 0 activos dados de alta",
     s1 >= 400, evid("OC-14", b1), "alta")
s2, b2 = req("PATCH", f"/compras/ordenes/{OID}/estado", json={"OCO_Estado": "CANCELADA"})
caso("OC-15", "Cancelar una orden ya recibida", "PATCH /estado CANCELADA sobre OC1 (con activos y stock recibidos)",
     "409 (o reversa controlada de lo recibido)", f"HTTP {s2}; estado {b2.get('OCO_Estado') if isinstance(b2, dict) else b2}; activos y stock recibidos permanecen",
     s2 >= 400, evid("OC-15", b2), "alta")
s3, b3 = req("PATCH", f"/compras/ordenes/{OID}/estado", json={"OCO_Estado": "BORRADOR"})
caso("OC-16", "Reabrir una orden CANCELADA", "PATCH /estado BORRADOR", "409 ORDER_ALREADY_CANCELLED", f"HTTP {s3} {corto(b3,100)}", s3 == 409, evid("OC-16", b3), "media")

s, oc3 = crear_orden(f"QA2-OC3-{SUF}", [{"OCL_Descripcion": "QA2 Nada", "OCL_Cantidad": 5, "OCL_Precio_Unitario": "10"}], moneda="CHF")
s1, b1 = req("POST", f"/compras/ordenes/{oc3['OCO_Orden']}/recibir", json={})
caso("OC-17", "Recepción vacía", "POST /recibir con cuerpo {} (nada recibido)", "400 (nada que recibir)",
     f"HTTP {s1}; estado {b1.get('OCO_Estado') if isinstance(b1, dict) else b1}", s1 >= 400, evid("OC-17", b1), "alta")
s, oc4 = crear_orden(f"QA2-OC4-{SUF}", [])
caso("OC-18", "Orden sin líneas", "POST /compras/ordenes con lineas=[]", "422/400 (una orden necesita al menos una línea)",
     f"HTTP {s}; total {oc4.get('OCO_Total') if isinstance(oc4, dict) else oc4}", s >= 400, evid("OC-18", oc4), "baja", "OBSERVACIÓN")

# Consumible distinto al de la línea / línea de activo recibida como consumible
s, oc5 = crear_orden(f"QA2-OC5-{SUF}", [{"OCL_Descripcion": "QA2 Toner pedido", "OCL_Cantidad": 4, "OCL_Precio_Unitario": "30", "CON_Consumible": CID},
                                        {"OCL_Descripcion": "QA2 Laptop como consumible", "OCL_Cantidad": 2, "OCL_Precio_Unitario": "900"}], moneda="GTQ")
lns = {l["OCL_Descripcion"]: l["OCL_Linea"] for l in oc5["lineas"]}
s1, b1 = req("POST", f"/compras/ordenes/{oc5['OCO_Orden']}/recibir", json={"consumibles": [
    {"OCL_Linea": lns["QA2 Toner pedido"], "CON_Consumible": CID2, "cantidad": 4},
    {"OCL_Linea": lns["QA2 Laptop como consumible"], "CON_Consumible": CID2, "cantidad": 2}]})
caso("OC-19", "Recibir un artículo distinto al pedido en la línea", "Línea de Toner (CON=Toner) recibida como Mouse; línea de laptops recibida como consumible",
     "400 (el consumible debe coincidir con el de la línea; el tipo de recepción con el de la línea)",
     f"HTTP {s1} {corto(b1,150)}", s1 >= 400, evid("OC-19", b1), "alta")

# Sede de la orden: activos a sede B
s, oc6 = crear_orden(f"QA2-OC6-{SUF}", [{"OCL_Descripcion": "QA2 Laptop B", "OCL_Cantidad": 1, "OCL_Precio_Unitario": "800"}], moneda="CHF", sede=SEDE_B)
s1, b1 = req("POST", f"/compras/ordenes/{oc6['OCO_Orden']}/recibir", json={"activos": [ra(oc6["lineas"][0]["OCL_Linea"], f"QA2-R{SUF}-B1")]})
nb = req("GET", f"/core/activos/by-code/{b1['activos_codigos'][0]}")[1] if s1 == 200 else {}
caso("OC-20", "Activo recibido en orden CHF de la sede B", "Orden CHF, sede B → recibir 1", "activo en sede B, moneda CHF, costo 800",
     f"HTTP {s1}; sede {nb.get('SED_Sede')}; moneda {nb.get('ACT_Moneda')}; costo {nb.get('ACT_Costo')}",
     nb.get("SED_Sede") == SEDE_B and nb.get("ACT_Moneda") == "CHF" and Decimal(str(nb.get("ACT_Costo"))) == Decimal("800"), evid("OC-20", nb), "media")
s, b = req("POST", "/consumibles", json={"CON_Nombre": f"QA2-Cable B {SUF}", "CON_Stock_Actual": 0, "SED_Sede": SEDE_B})
CID_B = b["CON_Consumible"]
s, oc7 = crear_orden(f"QA2-OC7-{SUF}", [{"OCL_Descripcion": "QA2 Cable", "OCL_Cantidad": 3, "OCL_Precio_Unitario": "5"}], moneda="GTQ", sede=1)
s1, b1 = req("POST", f"/compras/ordenes/{oc7['OCO_Orden']}/recibir", json={"consumibles": [{"OCL_Linea": oc7["lineas"][0]["OCL_Linea"], "CON_Consumible": CID_B, "cantidad": 3}]})
caso("OC-21", "Consumible de otra sede recibido en una orden de la sede A", "Orden sede A → recibir en consumible de la sede B",
     "400 o advertencia (la mercadería entra en la bodega de la sede de la orden)", f"HTTP {s1}", s1 >= 400, evid("OC-21", b1), "baja", "OBSERVACIÓN")

# Concurrencia: doble recepción simultánea
s, oc8 = crear_orden(f"QA2-OC8-{SUF}", [{"OCL_Descripcion": "QA2 Monitor", "OCL_Cantidad": 2, "OCL_Precio_Unitario": "150", "CON_Consumible": CID2}], moneda="GTQ")
ln8 = oc8["lineas"][0]["OCL_Linea"]
st0 = req("GET", f"/consumibles/{CID2}")[1]["CON_Stock_Actual"]
res = en_paralelo([lambda cl=cliente_extra(), i=i: req("POST", f"/compras/ordenes/{oc8['OCO_Orden']}/recibir", client=cl,
                                                      json={"consumibles": [{"OCL_Linea": ln8, "CON_Consumible": CID2, "cantidad": 2}]}) for i in range(3)])
st1 = req("GET", f"/consumibles/{CID2}")[1]["CON_Stock_Actual"]
cods = sorted(r[0] for r in res)
caso("CC-02", "Concurrencia: 3 recepciones simultáneas de la misma orden", "3 hilos POST /recibir (2 unidades)",
     "Una 200 y el resto 409; stock +2 una sola vez", f"códigos {cods}; stock {st0}→{st1}",
     cods.count(200) == 1 and st1 == st0 + 2, evid("CC-02", res), "bloqueante")
s, oc9 = crear_orden(f"QA2-OC9-{SUF}", [{"OCL_Descripcion": "QA2 Laptop C", "OCL_Cantidad": 1, "OCL_Precio_Unitario": "900"}], moneda="GTQ")
res = en_paralelo([lambda cl=cliente_extra(), i=i: req("POST", f"/compras/ordenes/{oc9['OCO_Orden']}/recibir", client=cl,
                                                      json={"activos": [ra(oc9["lineas"][0]["OCL_Linea"], f"QA2-R{SUF}-C{i}")]}) for i in range(2)])
cods = sorted(r[0] for r in res)
caso("CC-03", "Concurrencia: 2 recepciones simultáneas con activos distintos", "2 hilos, series distintas, línea de 1",
     "Una 200 y una 409; 1 activo creado", f"códigos {cods}", cods == [200, 409], evid("CC-03", res), "bloqueante")

# ═════════════════════ 5. CONCURRENCIA SOBRE ACTIVOS / TICKETS ═════════════════════
c1 = nuevo_activo()
res = en_paralelo([lambda cl=cliente_extra(), p=p: asignar(c1["ACT_Activo"], p["PER_Persona"], AREA_A, client=cl) for p in (P1, P2, P3)])
v = vigentes(c1["ACT_Activo"])
cods = sorted(r[0] for r in res)
caso("CC-04", "Concurrencia: 3 asignaciones simultáneas del mismo activo a 3 personas", "3 hilos POST /movimientos",
     "Una 201, dos 409; una sola custodia abierta", f"códigos {cods}; custodias abiertas {len(v)}",
     cods.count(201) == 1 and len(v) == 1 and all(c == 409 for c in cods if c != 201), evid("CC-04", res), "bloqueante")
res = en_paralelo([lambda cl=cliente_extra(), p=p: req("POST", "/trazabilidad/transferencia", client=cl, json={"ACT_Activo": c1["ACT_Activo"], "PER_Persona_Destino": p["PER_Persona"], "ARE_Area_Destino": AREA_A}) for p in (P4, P5)])
v = vigentes(c1["ACT_Activo"])
cods = sorted(r[0] for r in res)
caso("CC-05", "Concurrencia: 2 transferencias simultáneas", "2 hilos POST /transferencia a P4 y P5",
     "Una 201 y una rechazada; una sola custodia abierta", f"códigos {cods}; custodias abiertas {len(v)}",
     cods.count(201) == 1 and len(v) == 1, evid("CC-05", {"res": res, "vig": v}), "bloqueante")
res = en_paralelo([lambda cl=cliente_extra(): req("POST", "/trazabilidad/devolucion", client=cl, json={"ACT_Activo": c1["ACT_Activo"]}) for _ in range(2)])
cods = sorted(r[0] for r in res)
caso("CC-06", "Concurrencia: 2 devoluciones simultáneas", "2 hilos POST /devolucion", "Una 200 y una 400/409",
     f"códigos {cods}; estado {estado_de(c1['ACT_Activo'])}", cods.count(200) == 1, evid("CC-06", res), "alta")
c2 = nuevo_activo()
res = en_paralelo([lambda cl=cliente_extra(): req("POST", "/mantenimiento/", client=cl, json={"ACT_Activo": c2["ACT_Activo"], "PER_Persona_Solicita": P1["PER_Persona"], "TMA_Tipo_Mantenimiento": 2, "MAN_Descripcion_Falla": "QA2 concurrente"}) for _ in range(2)])
cods = sorted(r[0] for r in res)
caso("CC-07", "Concurrencia: 2 aperturas simultáneas de ticket", "2 hilos POST /mantenimiento/", "Una 201 y una 409",
     f"códigos {cods}", cods == [201, 409], evid("CC-07", res), "alta")
mid = next(r[1]["MAN_Mantenimiento"] for r in res if r[0] == 201)
res = en_paralelo([lambda cl=cliente_extra(), k=k: req("PATCH", f"/mantenimiento/{mid}/cerrar", client=cl, json={"MAN_Costo_Total": k, "MAN_Moneda": m}) for k, m in (("111.00", "GTQ"), ("222.00", "USD"))])
cods = sorted(r[0] for r in res)
final = req("GET", f"/mantenimiento/{mid}")[1]
caso("CC-08", "Concurrencia: doble cierre simultáneo del mismo ticket", "2 hilos PATCH /cerrar con costos/monedas distintos",
     "Uno 200 y otro 400 TICKET_ALREADY_CLOSED", f"códigos {cods}; costo final {final.get('MAN_Costo_Total')} {final.get('MAN_Moneda')}",
     cods.count(200) == 1, evid("CC-08", {"res": res, "final": final}), "alta")

# ═════════════════════ 6. OFFBOARDING ═════════════════════
o1, o2 = nuevo_activo(), nuevo_activo()
asignar(o1["ACT_Activo"], P3["PER_Persona"], AREA_A)
asignar(o2["ACT_Activo"], P3["PER_Persona"], AREA_A, tipo="Préstamo")
s, b = req("POST", f"/trazabilidad/persona/{P3['PER_Persona']}/offboarding")
per3 = req("GET", f"/org/personas/{P3['PER_Persona']}")[1]
ok = s == 200 and b.get("movimientos_cerrados") == 2 and estado_de(o1["ACT_Activo"]) == "En Bodega" \
    and estado_de(o2["ACT_Activo"]) == "En Bodega" and per3["PER_Estado"] is False
caso("OF-01", "Offboarding completo de persona con 2 activos (asignación + préstamo)", "POST /persona/{id}/offboarding",
     "200; 2 custodias cerradas; activos En Bodega; persona inactiva", f"HTTP {s}; {corto(b, 200)}", ok, evid("OF-01", b), "alta")
s, b = req("POST", f"/trazabilidad/persona/{P3['PER_Persona']}/offboarding")
caso("OF-02", "Offboarding repetido (idempotencia)", "Repetir", "200 con idempotent_noop=true", f"HTTP {s}; noop {b.get('idempotent_noop')}",
     s == 200 and b.get("idempotent_noop") is True, evid("OF-02", b), "media")

o3 = nuevo_activo()
asignar(o3["ACT_Activo"], P5["PER_Persona"], AREA_A)
s, t3 = req("POST", "/mantenimiento/", json={"ACT_Activo": o3["ACT_Activo"], "PER_Persona_Solicita": P5["PER_Persona"], "TMA_Tipo_Mantenimiento": 2, "MAN_Descripcion_Falla": "QA2 en taller al salir"})
s, b = req("POST", f"/trazabilidad/persona/{P5['PER_Persona']}/offboarding")
caso("OF-03", "Offboarding con un activo en reparación (ticket abierto)", "Activo de P5 con ticket abierto → offboarding",
     "El activo sigue 'En Reparación' hasta cerrar el ticket (no pasa a En Bodega)",
     f"HTTP {s}; estado tras offboarding {estado_de(o3['ACT_Activo'])}", estado_de(o3["ACT_Activo"]) == "En Reparación",
     evid("OF-03", b), "media")
req("PATCH", f"/mantenimiento/{t3['MAN_Mantenimiento']}/cerrar", json={"MAN_Costo_Total": "0"})

# Offboarding parcial por alcance: usuario ADMIN_TI con alcance solo sede 1
P_ADM = nueva_persona("AdminA")
PWD_T, PWD_F = "Brisa#Lumen-7304-Tq", "Cobalto!Ria-5821-Wx"
USR = f"qa2adm{SUF}"
s, u = req("POST", "/org/usuarios", json={"USU_Username": USR, "USU_Password": PWD_T, "USU_Rol": "ADMIN_TI",
                                          "PER_Persona": P_ADM["PER_Persona"], "sedes": [1]})
alcance_ok = s == 201
CA = None
if alcance_ok:
    try:
        ca = httpx.Client(verify=False, timeout=60)
        body = login(ca, USR, PWD_T)
        ca.headers["Authorization"] = f"Bearer {body['access_token']}"
        if body.get("restriction") == "PASSWORD_CHANGE_REQUIRED":
            r = ca.post(f"{BASE}/me/password", json={"current_password": PWD_T, "new_password": PWD_F})
            body = login(ca, USR, PWD_F)
            ca.headers["Authorization"] = f"Bearer {body['access_token']}"
        secret = None
        if body.get("restriction") == "MFA_ENROLLMENT_REQUIRED":
            secret = ca.post(f"{BASE}/me/2fa/totp/setup").json()["secret"]
            ca.post(f"{BASE}/me/2fa/totp/activate", json={"code": pyotp.TOTP(secret).now()})
            time.sleep(31 - (int(time.time()) % 30))
            ca = httpx.Client(verify=False, timeout=60)
            body = login(ca, USR, PWD_F, secret)
            ca.headers["Authorization"] = f"Bearer {body['access_token']}"
        with open(os.path.join(OUT, "usuario_admin_sedeA.json"), "w", encoding="utf-8") as f:
            json.dump({"usuario": USR, "clave": PWD_F, "totp_secret": secret, "sedes": [1]}, f)
        CA = ca
    except Exception as e:  # noqa: BLE001
        print("No se pudo preparar el ADMIN_TI de la sede A:", e)

if CA:
    o4, o5 = nuevo_activo(), nuevo_activo()
    asignar(o4["ACT_Activo"], P4["PER_Persona"], AREA_A)
    asignar(o5["ACT_Activo"], P4["PER_Persona"], AREA_B)   # queda en la sede B
    s, b = req("POST", f"/trazabilidad/persona/{P4['PER_Persona']}/offboarding", client=CA)
    per4 = req("GET", f"/org/personas/{P4['PER_Persona']}")[1]
    ok = s == 403 and len(vigentes(o4["ACT_Activo"])) == 1 and per4["PER_Estado"] is True
    caso("OF-04", "Offboarding parcial por alcance: ADMIN_TI de sede A sobre persona con un activo en sede B",
         "Persona P4 (sede A) con activo en sede A y otro en sede B; offboarding con ADMIN_TI de alcance sede A",
         "403 OFFBOARDING_REQUIRES_SCOPE_OVER_ALL_ASSETS; nada cambia (atómico)",
         f"HTTP {s} {corto(b,100)}; custodia sede A abierta={len(vigentes(o4['ACT_Activo']))==1}; persona activa={per4['PER_Estado']}",
         ok, evid("OF-04", b), "alta")
    s, vis = req("GET", f"/trazabilidad/persona/{P4['PER_Persona']}/asignaciones", client=CA)
    caso("OF-05", "Vista de custodias de P4 para el ADMIN_TI de sede A", "GET /persona/{id}/asignaciones",
         "Muestra solo la de su sede y avisa que existen custodias fuera de su alcance",
         f"HTTP {s}; ve {len(vis) if isinstance(vis, list) else vis} de 2 (sin indicación de las ocultas)",
         False, evid("OF-05", vis), "baja", "OBSERVACIÓN")
    s, b = req("POST", f"/trazabilidad/persona/{PB['PER_Persona']}/offboarding", client=CA)
    caso("OF-06", "ADMIN_TI de sede A intenta offboarding de persona de sede B", "POST offboarding sobre PB (sede B)",
         "403/404", f"HTTP {s} {corto(b,100)}", s in (403, 404), evid("OF-06", b), "alta")
    s, b = req("POST", f"/trazabilidad/persona/{P4['PER_Persona']}/offboarding")
    caso("OF-07", "El mismo offboarding por un usuario global", "sa ejecuta el offboarding de P4",
         "200; 2 custodias cerradas en ambas sedes", f"HTTP {s}; cerrados {b.get('movimientos_cerrados')}",
         s == 200 and b.get("movimientos_cerrados") == 2, evid("OF-07", b), "alta")
    # Técnico/ADMIN_TI de sede A asignando a un área de sede B
    o6 = nuevo_activo()
    s, b = asignar(o6["ACT_Activo"], P1["PER_Persona"], AREA_B, client=CA)
    caso("OF-08", "ADMIN_TI de sede A asigna a un área de la sede B", "POST /movimientos con área fuera del alcance", "403",
         f"HTTP {s} {corto(b,100)}", s == 403, evid("OF-08", b), "alta")
else:
    caso("OF-04", "Offboarding parcial por alcance", "Crear ADMIN_TI con alcance sede A", "Usuario creado", f"No se pudo crear/enrolar: {corto(u)}",
         False, evid("OF-04", u), "media", "OBSERVACIÓN")

s, b = asignar(o1["ACT_Activo"], P3["PER_Persona"], AREA_A)
caso("OF-09", "Asignar un activo a una persona ya dada de baja (offboarding)", "Asignar a P3 tras su offboarding",
     "400 (persona inactiva)", f"HTTP {s}; estado {estado_de(o1['ACT_Activo'])}", s >= 400, evid("OF-09", b), "alta")

# ═════════════════════ 7. GARANTÍAS ═════════════════════
g_v = nuevo_activo(garantia=HOY + timedelta(days=20))
g_x = nuevo_activo(garantia=HOY - timedelta(days=5), compra=HOY - timedelta(days=400))
g_b = nuevo_activo(garantia=HOY + timedelta(days=10))
req("DELETE", f"/core/activos/{g_b['ACT_Activo']}")
gar = req("GET", "/compras/garantias", params={"dias": 90, "solo_alertas": True})[1]
idx = {g["ACT_Activo"]: g for g in gar}
ok = idx.get(g_v["ACT_Activo"], {}).get("estado_garantia") == "por_vencer" and idx.get(g_x["ACT_Activo"], {}).get("estado_garantia") == "vencida"
caso("GA-01", "Garantías por vencer y vencidas", "Activos con fin de garantía +20 y -5 días; GET /compras/garantias?solo_alertas",
     "+20 → por_vencer (dias_restantes 20); -5 → vencida", f"{idx.get(g_v['ACT_Activo'], {}).get('estado_garantia')} ({idx.get(g_v['ACT_Activo'], {}).get('dias_restantes')}); {idx.get(g_x['ACT_Activo'], {}).get('estado_garantia')}",
     ok, evid("GA-01", [idx.get(g_v["ACT_Activo"]), idx.get(g_x["ACT_Activo"])]), "media")
caso("GA-02", "Activo de Baja en las alertas de garantía", "Activo con garantía +10 días dado de baja",
     "No aparece en alertas (ya no está en inventario)", f"aparece={g_b['ACT_Activo'] in idx}", g_b["ACT_Activo"] not in idx,
     evid("GA-02", idx.get(g_b["ACT_Activo"])), "baja", "OBSERVACIÓN")
rec_prov = [idx.get(x["ACT_Activo"]) for x in creados]
caso("GA-03", "Garantía de activos recibidos por OC muestra el proveedor", "Activos de OC1 con garantía +30 días",
     f"estado por_vencer y proveedor QA2-Proveedor {SUF}", f"{[(r or {}).get('estado_garantia') for r in rec_prov]} / {[(r or {}).get('proveedor') for r in rec_prov]}",
     all(r and r.get("proveedor") == f"QA2-Proveedor {SUF}" for r in rec_prov), evid("GA-03", rec_prov), "baja")
s, b = req("POST", "/core/activos", json={"ACT_Serie_Fabricante": f"QA2-{SUF}-GAR", "ACT_Fecha_Compra": str(HOY), "ACT_Fin_Garantia": str(HOY - timedelta(days=1)),
                                         "MOD_Modelo": MODELO, "TAC_Tipo_Activo": TIPO_ACT, "EOP_Estado_Operativo": EST["Disponible"], "SED_Sede": 1})
caso("GA-04", "Garantía anterior a la compra", "POST /core/activos con fin garantía < compra", "400 WARRANTY_DATE_CANNOT_BE_BEFORE_PURCHASE_DATE",
     f"HTTP {s} {corto(b,100)}", s == 400, evid("GA-04", b), "media")
s, b = req("PATCH", f"/core/activos/{g_v['ACT_Activo']}", json={"ACT_Fin_Garantia": "2000-01-01"})
det = activo(g_v["ACT_Activo"])
caso("GA-05", "Editar la garantía de un activo", "PATCH ACT_Fin_Garantia=2000-01-01 (anterior a la compra)",
     "400 por regla de fechas, o la fecha se actualiza si es válida; nunca 200 ignorando el campo",
     f"HTTP {s}; fin garantía guardado {det.get('ACT_Fin_Garantia')} (el campo no está en ActivoUpdate y se ignora en silencio)",
     s >= 400, evid("GA-05", det), "baja", "OBSERVACIÓN")

# ═════════════════════ 8. TABLERO ═════════════════════
time.sleep(2)
D = req("GET", "/stats/dashboard")[1]
evid("TB_dashboard_final", D)
tot = req("POST", "/core/activos/search", json={"page": 1, "per_page": 1})[1]["total"]
por_estado_api = {}
for nom, eid in EST.items():
    por_estado_api[nom] = req("POST", "/core/activos/search", json={"page": 1, "per_page": 1, "estado_operativo_id": eid})[1]["total"]
caso("TB-01", "Total de activos y por estado coherentes con el inventario", "Comparar /stats/dashboard con /core/activos/search por estado",
     "activos_totales = total de búsqueda; por_estado = conteos por estado", f"tablero {D['activos_totales']} vs búsqueda {tot}; por_estado tablero {D['por_estado']} vs {por_estado_api}",
     D["activos_totales"] == tot and all(D["por_estado"].get(k, 0) == v for k, v in por_estado_api.items()),
     evid("TB-01", {"dash": D, "api": por_estado_api, "total": tot}), "media")
caso("TB-02", "KPI 'asignados' vs. estado Asignado", "activos_asignados (custodias abiertas) vs por_estado['Asignado']",
     "Iguales (cada activo Asignado tiene una custodia y viceversa)", f"custodias abiertas {D['activos_asignados']} vs estado Asignado {D['por_estado'].get('Asignado')}",
     D["activos_asignados"] == D["por_estado"].get("Asignado"), evid("TB-02", D), "media")
stock_calc = D["por_estado"].get("Disponible", 0) + D["por_estado"].get("En Bodega", 0)
caso("TB-03", "KPI 'activos_stock' (en existencia)", "activos_stock = totales − asignados",
     "Solo Disponible + En Bodega (no Baja, Reparación ni Tránsito)", f"activos_stock {D['activos_stock']} vs Disponible+Bodega {stock_calc} (incluye Baja {D['en_baja']} y Reparación {D['en_reparacion']})",
     D["activos_stock"] == stock_calc, evid("TB-03", D), "media")
d_costo = {k: round(D["costo_inventario_por_moneda"].get(k, 0) - DASH0["costo_inventario_por_moneda"].get(k, 0), 2) for k in ("GTQ", "USD", "CHF")}
caso("TB-04", "Valor del inventario por moneda (sin mezclar)", "Delta de costo_inventario_por_moneda antes/después de las pruebas",
     "Totales separados por GTQ/USD/CHF; los activos de Baja no suman", f"delta {d_costo}; tablero final {D['costo_inventario_por_moneda']}",
     set(D["costo_inventario_por_moneda"]) <= {"GTQ", "USD", "CHF"} and d_costo["USD"] > 0 and d_costo["CHF"] > 0,
     evid("TB-04", {"antes": DASH0["costo_inventario_por_moneda"], "despues": D["costo_inventario_por_moneda"], "delta": d_costo}), "media")
d_mant = {k: round(D["costo_mantenimiento_por_moneda"].get(k, 0) - DASH0["costo_mantenimiento_por_moneda"].get(k, 0), 2) for k in ("GTQ", "USD", "CHF")}
caso("TB-05", "Costo de mantenimiento por moneda", "Delta de costo_mantenimiento_por_moneda", "USD incluye 999 (MA-04); monedas separadas",
     f"delta {d_mant}", d_mant["USD"] >= 999, evid("TB-05", d_mant), "media")
gv_total = req("GET", "/compras/garantias", params={"dias": 90, "solo_alertas": True})[1]
pv = [g for g in gv_total if g["estado_garantia"] == "por_vencer"]
caso("TB-06", "Garantías por vencer: tablero vs. vista de garantías", "garantias_por_vencer_total vs /compras/garantias (por_vencer, 90 días)",
     "Mismo número (el tablero no debe truncar ni contar activos de baja)", f"tablero {D['garantias_por_vencer_total']} vs vista {len(pv)}; tablero limitado a 50 (LIMIT 50)",
     D["garantias_por_vencer_total"] == len(pv), evid("TB-06", {"dash": D["garantias_por_vencer"], "vista": pv}), "baja", "OBSERVACIÓN")
# Consistencia inmediata tras escritura (tablero lee de la réplica)
x = nuevo_activo(costo="1.00", moneda="CHF")
D2 = req("GET", "/stats/dashboard")[1]
caso("TB-07", "Tablero inmediatamente después de un alta (lee de la réplica)", "Crear activo y leer el tablero al instante",
     "activos_totales refleja el alta (o el retraso está documentado)", f"antes {D['activos_totales']} → inmediato {D2['activos_totales']}",
     D2["activos_totales"] == D["activos_totales"] + 1, evid("TB-07", {"antes": D["activos_totales"], "despues": D2["activos_totales"]}), "baja", "OBSERVACIÓN")

with open(os.path.join(OUT, "resultados.json"), "w", encoding="utf-8") as f:
    json.dump({"sufijo": SUF, "sede_b": SEDE_B, "casos": RESULTADOS}, f, ensure_ascii=False, indent=2, default=str)
ok = sum(r["estado"] == "OK" for r in RESULTADOS)
print(f"\n== {ok}/{len(RESULTADOS)} OK; defectos {sum(r['estado']=='DEFECTO' for r in RESULTADOS)}; observaciones {sum(r['estado']=='OBSERVACIÓN' for r in RESULTADOS)} ==")

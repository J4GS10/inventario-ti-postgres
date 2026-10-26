"""
Carga inicial del inventario de Guatemala desde el JSON que genera
`scripts/excel_inventario_gt.py` a partir de «Inventario GT.xlsx».

Pensado para una base recién creada (solo bootstrap y catálogo mínimo): crea
ubicaciones, departamentos, personas, tipos, marcas, modelos y activos, y
reproduce en orden cronológico las asignaciones de la hoja MOVIMIENTO. Cada
alta y cada asignación deja su evento en el kardex con la fecha del Excel.

El kardex es de solo inserción: por eso el script se niega a correr si ya hay
activos, y conviene probar antes con --dry-run (todo se revierte).

    docker cp inventario_gt.json lombardi-backend-1:/tmp/inventario_gt.json
    docker exec lombardi-backend-1 python -m app.importar_inventario_gt /tmp/inventario_gt.json --dry-run
    docker exec lombardi-backend-1 python -m app.importar_inventario_gt /tmp/inventario_gt.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from collections import Counter
from datetime import date, datetime

from sqlalchemy import func, select

from app.db.session import SessionLocal
from app.models.catalogs import EstadoOperativo, Marca, Modelo, TipoActivo, TipoEspecificacion
from app.models.core import Activo, Especificacion
from app.models.enums import EstadoOperativoEnum, TipoMovimientoEnum
from app.models.governance import AuditoriaSistema, Secuencia
from app.models.location import Area, Edificio, Estado, Municipio, Nivel, Pais, Sede
from app.models.organization import Cargo, Departamento, Persona, Usuario
from app.models.traceability import Movimiento, TipoMovimiento
from app.services.kardex import KardexService

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("importar_inventario_gt")

AGENTE = "app.importar_inventario_gt"

SEDES = {
    "GT": {"pais": ("Guatemala", "GT"), "estado": "Guatemala", "municipio": "Guatemala",
           "sede": "Lombardi Guatemala", "edificio": "Oficinas Lombardi Guatemala"},
    "PE": {"pais": ("Perú", "PE"), "estado": "Lima", "municipio": "Lima",
           "sede": "Lombardi Perú", "edificio": "Oficina Lombardi Perú"},
}
DESCRIPCION_DEP = {
    "Por clasificar": "Personas importadas del Excel sin área definida; asignar su departamento real.",
}
CARGOS_JEFATURA = {"Jefe de dibujantes"}


def _dt(iso: str) -> datetime:
    return datetime.combine(date.fromisoformat(iso), datetime.min.time())


async def _crear_ubicaciones(db, ubicaciones: list[dict]) -> tuple[dict[str, Area], dict[str, int]]:
    sedes: dict[str, int] = {}
    niveles: dict[str, int] = {}
    for clave in sorted({u["sede"] for u in ubicaciones}):
        cfg = SEDES[clave]
        pais = Pais(PAI_Nombre=cfg["pais"][0], PAI_Codigo_ISO=cfg["pais"][1])
        db.add(pais)
        await db.flush()
        estado = Estado(EST_Nombre=cfg["estado"], PAI_Pais=pais.PAI_Pais)
        db.add(estado)
        await db.flush()
        mun = Municipio(MUN_Nombre=cfg["municipio"], EST_Estado=estado.EST_Estado)
        db.add(mun)
        await db.flush()
        sede = Sede(SED_Nombre=cfg["sede"], MUN_Municipio=mun.MUN_Municipio)
        db.add(sede)
        await db.flush()
        edificio = Edificio(EDI_Nombre=cfg["edificio"], SED_Sede=sede.SED_Sede)
        db.add(edificio)
        await db.flush()
        nivel = Nivel(NIV_Numero_Piso="General", EDI_Edificio=edificio.EDI_Edificio)
        db.add(nivel)
        await db.flush()
        sedes[clave] = sede.SED_Sede
        niveles[clave] = nivel.NIV_Nivel

    areas: dict[str, Area] = {}
    for u in ubicaciones:
        area = Area(ARE_Nombre=u["area"], ARE_Tipo_Acceso=u["acceso"], NIV_Nivel=niveles[u["sede"]])
        db.add(area)
        await db.flush()
        area.sede_id = sedes[u["sede"]]  # atributo auxiliar, no se persiste
        areas[u["area"]] = area
    return areas, sedes


async def importar(ruta: str, *, dry_run: bool) -> dict:
    with open(ruta, encoding="utf-8") as fh:
        datos = json.load(fh)

    stats: Counter = Counter()
    async with SessionLocal() as db:
        if (await db.execute(select(func.count()).select_from(Activo))).scalar_one():
            raise SystemExit("La base ya tiene activos: esta carga inicial solo corre sobre una base limpia.")

        sa = (await db.execute(
            select(Usuario).where(Usuario.USU_Rol == "SUPER_ADMIN").order_by(Usuario.USU_Creado_En).limit(1)
        )).scalar_one()
        operador = sa.USU_Usuario

        estados = {e.EOP_Nombre: e.EOP_Estado_Operativo for e in (await db.execute(select(EstadoOperativo))).scalars()}
        tmov = {t.TMO_Nombre: t.TMO_Tipo_Movimiento for t in (await db.execute(select(TipoMovimiento))).scalars()}
        disponible = estados[EstadoOperativoEnum.DISPONIBLE.value]
        asignado = estados[EstadoOperativoEnum.ASIGNADO.value]

        # --- Ubicaciones ---
        areas, sedes = await _crear_ubicaciones(db, datos["ubicaciones"])
        stats["areas"] = len(areas)
        stats["sedes"] = len(sedes)

        # --- Organización ---
        deps: dict[str, int] = {}
        for nombre in sorted({p["departamento"] for p in datos["personas"]}):
            d = Departamento(DEP_Nombre=nombre, DEP_Descripcion=DESCRIPCION_DEP.get(nombre))
            db.add(d)
            await db.flush()
            deps[nombre] = d.DEP_Departamento
        cargos: dict[str, int] = {}
        for nombre in sorted({p["cargo"] for p in datos["personas"]}):
            c = Cargo(CAR_Nombre=nombre, CAR_Es_Jefatura=nombre in CARGOS_JEFATURA)
            db.add(c)
            await db.flush()
            cargos[nombre] = c.CAR_Cargo
        personas: dict[str, Persona] = {}
        for p in datos["personas"]:
            per = Persona(
                PER_Primer_Nombre=p["primer_nombre"][:50],
                PER_Segundo_Nombre=(p.get("segundo_nombre") or None),
                PER_Primer_Apellido=p["primer_apellido"][:50],
                PER_Email_Corporativo=p["email"],
                DEP_Departamento=deps[p["departamento"]],
                CAR_Cargo=cargos[p["cargo"]],
                SED_Sede=sedes[p["sede"]],
            )
            db.add(per)
            personas[p["nombre"]] = per
        await db.flush()
        stats.update(departamentos=len(deps), cargos=len(cargos), personas=len(personas))

        # --- Catálogos de activos ---
        tipos: dict[str, TipoActivo] = {}
        for t in datos["tipos"]:
            obj = TipoActivo(TAC_Nombre=t["nombre"], TAC_Prefijo=t["prefijo"] + "-",
                             TAC_Aplica_Depreciacion=t["deprecia"])
            db.add(obj)
            tipos[t["nombre"]] = obj
        marcas: dict[str, Marca] = {}
        for nombre in datos["marcas"]:
            obj = Marca(MAR_Nombre=nombre)
            db.add(obj)
            marcas[nombre] = obj
        await db.flush()
        modelos: dict[tuple, Modelo] = {}
        for m in datos["modelos"]:
            obj = Modelo(MOD_Nombre=m["nombre"], MAR_Marca=marcas[m["marca"]].MAR_Marca,
                         TAC_Tipo_Activo=tipos[m["tipo"]].TAC_Tipo_Activo)
            db.add(obj)
            modelos[tuple(m["clave"])] = obj
        await db.flush()
        stats.update(tipos=len(tipos), marcas=len(marcas), modelos=len(modelos))

        tes: dict[str, int] = {}
        for nombre in ("Factura", "Observaciones", "Nota de serie"):
            existente = (await db.execute(
                select(TipoEspecificacion).where(func.lower(TipoEspecificacion.TES_Nombre) == nombre.lower())
            )).scalar_one_or_none()
            if existente is None:
                existente = TipoEspecificacion(TES_Nombre=nombre)
                db.add(existente)
                await db.flush()
            tes[nombre] = existente.TES_Tipo_Especificacion

        # --- Activos (entran disponibles en su ubicación del Excel) ---
        activos: dict[str, Activo] = {}
        for a in datos["activos"]:
            area = areas[a["ubicacion"]]
            act = Activo(
                ACT_Codigo_Interno=a["codigo"],
                ACT_Serie_Fabricante=a["serie"],
                ACT_Hostname=a["hostname"],
                ACT_Fecha_Compra=date.fromisoformat(a["fecha_entrada"]),
                MOD_Modelo=modelos[tuple(a["modelo_clave"])].MOD_Modelo,
                TAC_Tipo_Activo=tipos[a["tipo"]].TAC_Tipo_Activo,
                EOP_Estado_Operativo=disponible,
                ARE_Area_Ubicacion=area.ARE_Area,
                SED_Sede=area.sede_id,
            )
            db.add(act)
            activos[a["codigo"]] = act
        await db.flush()
        for a in datos["activos"]:
            act = activos[a["codigo"]]
            specs = {"Factura": a["factura"], "Observaciones": a["observaciones"]}
            if a["motivo_serie"]:
                specs["Nota de serie"] = (
                    "Sin serie del fabricante: se usa el código interno." if a["motivo_serie"].startswith("sin")
                    else f"Serie original {a['serie_original']}, {a['motivo_serie']}."
                )
            for nombre, valor in specs.items():
                if valor:
                    db.add(Especificacion(ACT_Activo=act.ACT_Activo, TES_Tipo_Especificacion=tes[nombre],
                                          ESP_Valor=str(valor)[:255]))
                    stats["especificaciones"] += 1
        stats["activos"] = len(activos)

        # Secuencias de código: el próximo alta continúa la numeración del Excel.
        ultimos: dict[str, int] = {}
        for codigo in activos:
            prefijo, numero = codigo.rsplit("-", 1)
            ultimos[prefijo] = max(ultimos.get(prefijo, 0), int(numero))
        for t in datos["tipos"]:
            db.add(Secuencia(SEC_Contexto=f"ASSET_{t['prefijo']}-",
                             SEC_Ultimo_Numero=ultimos.get(t["prefijo"], 0), SEC_Relleno=5))
        await db.flush()

        # --- Línea de tiempo: altas y asignaciones en orden cronológico ---
        kardex = KardexService(db)
        info = {a["codigo"]: a for a in datos["activos"]}
        eventos = [(a["fecha_entrada"], 0, i, "ALTA", a) for i, a in enumerate(datos["activos"])]
        eventos += [(m["fecha"], 1, i, "MOV", m) for i, m in enumerate(datos["movimientos"]) if not m.get("omitir")]
        eventos.sort(key=lambda e: e[:3])
        abiertos: dict[str, Movimiento] = {}
        for fecha_iso, _, _, clase, ev in eventos:
            cuando = _dt(fecha_iso)
            if clase == "ALTA":
                act = activos[ev["codigo"]]
                await kardex.registrar(
                    "ALTA", activo=act, usuario_id=operador, fecha=cuando,
                    destino={"area": act.ARE_Area_Ubicacion, "sede": act.SED_Sede},
                    documento=("FACTURA", ev["factura"]) if ev["factura"] else None,
                    motivo="Carga inicial desde el Excel Inventario GT",
                    detalle={"fila_excel": ev["fila"], "responsable": ev["responsable"],
                             "estado_excel": ev["estado_excel"], "observaciones": ev["observaciones"]},
                )
                stats["kardex_altas"] += 1
                await db.flush()
                continue

            act = activos[ev["codigo"]]
            per = personas[ev["persona"]]
            area = areas[ev["area"]]
            notas = [f"Estación {ev['estacion']}" if ev["estacion"] else None,
                     f"Ticket {ev['ticket']}" if ev["ticket"] else None,
                     f"Documento {ev['documento']}" if ev["documento"] else None,
                     f"Registrado por {ev['responsable']}" if ev["responsable"] else None,
                     f"Excel MOVIMIENTO fila {ev['fila']}"]
            previo = abiertos.get(ev["codigo"])
            tipo = TipoMovimientoEnum.TRANSFERENCIA if previo else TipoMovimientoEnum.ASIGNACION
            if previo:
                previo.MOV_Fecha_Devolucion = cuando
            mov = Movimiento(
                ACT_Activo=act.ACT_Activo, PER_Persona=per.PER_Persona, ARE_Area=area.ARE_Area,
                TMO_Tipo_Movimiento=tmov[tipo.value], MOV_Fecha_Asignacion=cuando,
                MOV_Motivo=(ev["motivo"] or None) and ev["motivo"][:255],
                MOV_Observacion=" · ".join(n for n in notas if n),
                MOV_Usuario_Registra=operador,
            )
            db.add(mov)
            await db.flush()
            origen = None
            if previo:
                origen = {"persona": previo.PER_Persona, "area": previo.ARE_Area, "sede": act.SED_Sede}
            act.EOP_Estado_Operativo = asignado
            act.ARE_Area_Ubicacion = area.ARE_Area
            act.SED_Sede = area.sede_id
            await kardex.registrar(
                tipo.name if tipo == TipoMovimientoEnum.TRANSFERENCIA else "ASIGNACION",
                activo=act, usuario_id=operador, fecha=cuando, origen=origen,
                destino={"persona": per.PER_Persona, "area": area.ARE_Area, "sede": area.sede_id},
                documento=("TICKET", ev["ticket"]) if ev["ticket"] else ("MOVIMIENTO", str(mov.MOV_Movimiento)),
                motivo=ev["motivo"] or "Asignación",
                detalle={"fila_excel": ev["fila"], "estacion": ev["estacion"], "registrado_por": ev["responsable"]},
            )
            await db.flush()
            abiertos[ev["codigo"]] = mov
            stats["transferencias" if previo else "asignaciones"] += 1
        stats["custodias_abiertas"] = len(abiertos)
        stats["disponibles"] = len(activos) - len(abiertos)

        resumen = {
            "fuente": datos["fuente"],
            "json_generado": datos["generado"],
            "dry_run": dry_run,
            "totales": dict(stats),
            "incidencias": datos["incidencias"],
        }
        db.add(AuditoriaSistema(
            AUD_Accion="EXCEL_IMPORT_DRY_RUN" if dry_run else "EXCEL_IMPORT",
            AUD_Entidad_Afectada="INVENTARIO_GT",
            AUD_Snapshot_JSON=resumen, USU_Usuario=operador,
            AUD_IP_Origen="127.0.0.1", AUD_User_Agent=AGENTE,
        ))
        if dry_run:
            await db.rollback()
        else:
            await db.commit()
    return resumen


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Carga inicial del inventario GT desde el JSON del Excel.")
    ap.add_argument("json", help="Ruta del JSON generado por scripts/excel_inventario_gt.py")
    ap.add_argument("--dry-run", action="store_true", help="Valida todo y revierte los cambios.")
    args = ap.parse_args()
    res = asyncio.run(importar(args.json, dry_run=args.dry_run))
    log.info("Importación %s: %s", "simulada" if args.dry_run else "completada", res["totales"])

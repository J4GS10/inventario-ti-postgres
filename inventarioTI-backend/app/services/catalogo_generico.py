"""
Servicio genérico de catálogos (plan de licencias y catálogos §4.2).

Un solo servicio administra todos los catálogos estándar: listar (con uso e
inactivos opcionales), crear, editar, desactivar/reactivar, reordenar y
fusionar duplicados. Cada catálogo se declara una vez en CATALOGOS; las
tablas que lo referencian se descubren de las llaves foráneas del modelo, así
un catálogo nuevo no necesita código propio.

Fusionar «Kingstom» en «Kingston» reasigna en una transacción todo lo que
apunta al origen, deja el origen inactivo con la referencia al destino
(<P>_Fusionado_En) y registra CATALOG_MERGE en la bitácora.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import Column, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.data_scope import current_scope
from app.core.transactional import commit_or_409
import app.db.unicidad  # noqa: F401  (registra todas las tablas: las referencias salen de sus FKs)
from app.db.base import Base
from app.db.catalogos_semilla import CODIGOS_SISTEMA
from app.models import catalogo_estandar as ce
from app.models.catalogs import Marca, Modelo, TipoConexion, TipoEspecificacion
from app.models.traceability import TipoEvidencia, TipoMantenimiento
from app.repositories.governance import GovernanceRepository

GRUPOS = ("ACTIVOS", "MOVIMIENTOS", "LICENCIAS", "CONSUMIBLES", "MANTENIMIENTO", "UBICACIONES", "DOCUMENTOS")


@dataclass(frozen=True)
class Extra:
    """Columna propia de un catálogo que se puede editar."""
    columna: str
    tipo: str  # bool | texto | aplica_a | catalogo
    largo: int = 0
    catalogo: str | None = None  # para tipo «catalogo»: clave del catálogo referido
    # Tipo «catalogo»: columna de texto propia que se llena con el registro
    # elegido (columna de texto, columna del catálogo referido; si está vacía, su nombre).
    texto: tuple[str, str] | None = None


@dataclass(frozen=True)
class CatalogoDef:
    clave: str
    modelo: Any
    prefijo: str
    grupo: str
    extras: tuple[Extra, ...] = ()
    # Los registros creados por el usuario reciben un código automático
    # (la lógica guarda el código, p. ej. el motivo de una baja en el kardex).
    codigo_automatico: bool = False
    # Columna de texto heredada que se sincroniza con el destino al fusionar:
    # {(tabla, columna FK): (columna de texto, columna del catálogo que la llena)}.
    # Si la columna del catálogo está vacía se usa el nombre.
    textos: dict[tuple[str, str], tuple[str, str]] = field(default_factory=dict)
    # Columnas que guardan el CÓDIGO (sin FK) y también se trasladan al fusionar.
    # El kardex nunca: es de solo inserción y conserva el código histórico.
    codigos: tuple[tuple[str, str], ...] = ()
    # Largo máximo del código automático (lo limita la columna que lo guarda).
    largo_codigo: int = 40
    # Catálogos cuya lógica depende solo de los registros de sistema (p. ej. la
    # forma de conteo): el usuario los ordena y describe, pero no agrega nuevos.
    permite_crear: bool = True

    @property
    def pk(self) -> Column:
        return next(iter(self.modelo.__table__.primary_key.columns))

    def col(self, sufijo: str) -> Column | None:
        return self.modelo.__table__.columns.get(f"{self.prefijo}_{sufijo}")

    @property
    def tabla(self) -> str:
        return self.modelo.__tablename__

    @property
    def largo_nombre(self) -> int:
        return self.col("Nombre").type.length or 100

    @property
    def sistema(self) -> frozenset[str]:
        return CODIGOS_SISTEMA.get(self.tabla, frozenset())


def _pide_detalle(prefijo: str) -> Extra:
    return Extra(f"{prefijo}_Pide_Detalle", "bool")


CATALOGOS: dict[str, CatalogoDef] = {d.clave: d for d in (
    # Activos
    CatalogoDef("marcas", Marca, "MAR", "ACTIVOS",
                textos={("INV_SOFTWARE", "MAR_Marca"): ("SOF_Fabricante", "MAR_Nombre")}),
    CatalogoDef("tipos-conexion", TipoConexion, "TCN", "ACTIVOS"),
    CatalogoDef("tipos-especificacion", TipoEspecificacion, "TES", "ACTIVOS",
                extras=(Extra("UME_Unidad", "catalogo", catalogo="unidades-medida",
                              texto=("TES_Unidad_Medida", "UME_Simbolo")),)),
    CatalogoDef("unidades-medida", ce.UnidadMedida, "UME", "ACTIVOS",
                extras=(Extra("UME_Simbolo", "texto", 20),),
                textos={("INV_CONSUMIBLE", "UME_Unidad"): ("CON_Unidad", "UME_Nombre"),
                        ("INV_TIPO_ESPECIFICACION", "UME_Unidad"): ("TES_Unidad_Medida", "UME_Simbolo")}),
    # Movimientos y bajas
    CatalogoDef("motivos-movimiento", ce.MotivoMovimiento, "MMO", "MOVIMIENTOS",
                extras=(Extra("MMO_Aplica_A", "aplica_a"), _pide_detalle("MMO"))),
    CatalogoDef("motivos-baja", ce.MotivoBaja, "MBA", "MOVIMIENTOS",
                extras=(_pide_detalle("MBA"),), codigo_automatico=True, largo_codigo=30),  # ACT_Baja_Motivo
    CatalogoDef("destinos-baja", ce.DestinoBaja, "DBA", "MOVIMIENTOS",
                extras=(_pide_detalle("DBA"),), codigo_automatico=True, largo_codigo=30),  # ACT_Baja_Destino
    # Consumibles
    CatalogoDef("categorias-consumible", ce.CategoriaConsumible, "CCO", "CONSUMIBLES",
                textos={("INV_CONSUMIBLE", "CCO_Categoria"): ("CON_Categoria", "CCO_Nombre")}),
    # Mantenimiento
    CatalogoDef("tipos-mantenimiento", TipoMantenimiento, "TMA", "MANTENIMIENTO"),
    CatalogoDef("tipos-falla", ce.TipoFalla, "TFA", "MANTENIMIENTO", extras=(_pide_detalle("TFA"),)),
    CatalogoDef("acciones-mantenimiento", ce.AccionMantenimiento, "AMA", "MANTENIMIENTO",
                extras=(_pide_detalle("AMA"),)),
    # Ubicaciones
    CatalogoDef("tipos-area", ce.TipoArea, "TAR", "UBICACIONES"),
    # Documentos
    CatalogoDef("tipos-documento", ce.TipoDocumento, "TDO", "DOCUMENTOS", codigo_automatico=True,
                largo_codigo=30),  # KDX_Documento_Tipo
    # Licencias
    CatalogoDef("modelos-comerciales", ce.ModeloComercial, "MCO", "LICENCIAS"),
    CatalogoDef("metodos-activacion", ce.MetodoActivacion, "MAC", "LICENCIAS", codigo_automatico=True,
                extras=tuple(Extra(f"MAC_Pide_{c.capitalize()}", "bool") for c in ce.CAMPOS_CREDENCIAL)
                + tuple(Extra(f"MAC_Etiqueta_{c.capitalize()}", "texto", 60) for c in ce.CAMPOS_CREDENCIAL)),
    CatalogoDef("formas-conteo", ce.FormaConteo, "FCO", "LICENCIAS", permite_crear=False),
    CatalogoDef("tipos-evidencia", TipoEvidencia, "TEV", "DOCUMENTOS", codigo_automatico=True, largo_codigo=20,
                codigos=(("INV_ADJUNTO", "ADJ_Categoria"),)),
)}


def registrar(definicion: CatalogoDef) -> None:
    """Otros módulos (p. ej. licencias) agregan sus catálogos al registro."""
    CATALOGOS[definicion.clave] = definicion


def definicion(clave: str) -> CatalogoDef:
    d = CATALOGOS.get(clave)
    if d is None:
        raise HTTPException(404, "CATALOG_NOT_FOUND")
    return d


def referencias(d: CatalogoDef) -> list[Column]:
    """Columnas de otras tablas con llave foránea hacia el catálogo (sin la propia de fusión)."""
    pk = d.pk
    cols = []
    for tabla in Base.metadata.sorted_tables:
        for fk in tabla.foreign_keys:
            if fk.column is pk and not (tabla is d.modelo.__table__):
                cols.append(fk.parent)
    return cols


def codigo_desde_nombre(nombre: str, largo: int = 30) -> str:
    """«Código de suscripción + PIN» → CODIGO_DE_SUSCRIPCION_PIN."""
    base = unicodedata.normalize("NFKD", nombre).encode("ascii", "ignore").decode()
    base = re.sub(r"[^A-Za-z0-9]+", "_", base).strip("_").upper()
    return (base or "ITEM")[:largo]


def _ahora() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class CatalogoGenericoService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.gov_repo = GovernanceRepository(db)

    # ── Lectura ──────────────────────────────────────────────────────────────
    @staticmethod
    def describir(d: CatalogoDef) -> dict:
        return {
            "clave": d.clave,
            "grupo": d.grupo,
            "tiene_codigo": d.col("Codigo") is not None,
            "tiene_descripcion": d.col("Descripcion") is not None,
            "largo_nombre": d.largo_nombre,
            "permite_crear": d.permite_crear,
            "extras": [{"columna": e.columna, "tipo": e.tipo, "largo": e.largo, "catalogo": e.catalogo}
                       for e in d.extras],
        }

    def serializar(self, d: CatalogoDef, obj, uso: int = 0) -> dict:
        codigo_col, desc_col = d.col("Codigo"), d.col("Descripcion")
        codigo = getattr(obj, codigo_col.name) if codigo_col is not None else None
        return {
            "id": getattr(obj, d.pk.name),
            "nombre": getattr(obj, f"{d.prefijo}_Nombre"),
            "codigo": codigo,
            "descripcion": getattr(obj, desc_col.name) if desc_col is not None else None,
            "orden": getattr(obj, f"{d.prefijo}_Orden"),
            "activo": getattr(obj, f"{d.prefijo}_Activo"),
            "fusionado_en": getattr(obj, f"{d.prefijo}_Fusionado_En"),
            "sistema": codigo is not None and codigo in d.sistema,
            "uso": uso,
            "extras": {e.columna: getattr(obj, e.columna) for e in d.extras},
        }

    async def _uso(self, d: CatalogoDef) -> dict[int, int]:
        totales: dict[int, int] = {}
        for col in referencias(d):
            filas = await self.db.execute(
                select(col, func.count()).where(col.isnot(None)).group_by(col)
            )
            for valor, n in filas.all():
                totales[valor] = totales.get(valor, 0) + n
        if d.codigos:
            ids = dict((await self.db.execute(select(d.col("Codigo"), d.pk))).all())
            for tabla, columna in d.codigos:
                col = Base.metadata.tables[tabla].c[columna]
                for codigo, n in (await self.db.execute(select(col, func.count()).group_by(col))).all():
                    if codigo in ids:
                        totales[ids[codigo]] = totales.get(ids[codigo], 0) + n
        return totales

    async def listar(self, clave: str, *, inactivos: bool = False) -> list[dict]:
        d = definicion(clave)
        stmt = select(d.modelo).order_by(d.col("Orden"), func.lower(d.col("Nombre")))
        if not inactivos:
            stmt = stmt.where(d.col("Activo").is_(True))
        items = (await self.db.execute(stmt)).scalars().all()
        uso = await self._uso(d)
        return [self.serializar(d, o, uso.get(getattr(o, d.pk.name), 0)) for o in items]

    async def _obtener(self, d: CatalogoDef, id_: int):
        obj = await self.db.get(d.modelo, id_)
        if obj is None:
            raise HTTPException(404, "CATALOG_ITEM_NOT_FOUND")
        return obj

    async def obtener_por_codigo(self, clave: str, codigo: str, *, solo_activos: bool = True):
        """Registro por su código interno (la lógica nunca depende del nombre)."""
        d = definicion(clave)
        stmt = select(d.modelo).where(d.col("Codigo") == codigo)
        if solo_activos:
            stmt = stmt.where(d.col("Activo").is_(True))
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def nombre_vigente(self, clave: str, id_: int, fuente: str | None = None) -> str:
        """
        Texto legible de un registro activo (su nombre, o la columna `fuente`
        si tiene valor), para los campos de texto que se conservan junto a la
        llave del catálogo. Rechaza registros inexistentes o inactivos.
        """
        d = definicion(clave)
        obj = await self.db.get(d.modelo, id_)
        if obj is None or not getattr(obj, f"{d.prefijo}_Activo"):
            raise HTTPException(422, f"CATALOG_ITEM_NOT_FOUND:{clave}")
        nombre = getattr(obj, f"{d.prefijo}_Nombre")
        return (getattr(obj, fuente) or nombre) if fuente else nombre

    # ── Escritura ────────────────────────────────────────────────────────────
    async def _nombre_libre(self, d: CatalogoDef, nombre: str, excepto: int | None = None) -> None:
        stmt = select(d.pk).where(func.lower(func.trim(d.col("Nombre"))) == nombre.strip().lower())
        if excepto is not None:
            stmt = stmt.where(d.pk != excepto)
        if (await self.db.execute(stmt)).first():
            raise HTTPException(409, "CATALOG_ITEM_ALREADY_EXISTS")

    async def _codigo_libre(self, d: CatalogoDef, nombre: str) -> str:
        base = codigo_desde_nombre(nombre, d.largo_codigo - 3)
        codigo, n = base, 1
        while (await self.db.execute(select(d.pk).where(d.col("Codigo") == codigo))).first():
            n += 1
            codigo = f"{base}_{n}"
        return codigo

    async def _validar_extras(self, d: CatalogoDef, extras: dict[str, Any]) -> dict[str, Any]:
        permitidos = {e.columna: e for e in d.extras}
        limpios: dict[str, Any] = {}
        for columna, valor in (extras or {}).items():
            e = permitidos.get(columna)
            if e is None:
                raise HTTPException(422, f"CATALOG_FIELD_NOT_ALLOWED:{columna}")
            if e.tipo == "bool":
                if not isinstance(valor, bool):
                    raise HTTPException(422, f"CATALOG_FIELD_INVALID:{columna}")
            elif e.tipo == "texto":
                if valor is not None and (not isinstance(valor, str) or len(valor) > e.largo):
                    raise HTTPException(422, f"CATALOG_FIELD_INVALID:{columna}")
                valor = (valor or "").strip() or None
            elif e.tipo == "aplica_a":
                if valor is not None and (not isinstance(valor, list) or any(v not in ce.APLICA_A for v in valor)):
                    raise HTTPException(422, f"CATALOG_FIELD_INVALID:{columna}")
                valor = sorted(set(valor)) if valor else None
            elif e.tipo == "catalogo":
                obj = None
                if valor is not None:
                    ref = definicion(e.catalogo)
                    obj = await self.db.get(ref.modelo, valor) if isinstance(valor, int) else None
                    if obj is None or not getattr(obj, f"{ref.prefijo}_Activo"):
                        raise HTTPException(422, f"CATALOG_FIELD_INVALID:{columna}")
                if e.texto:
                    columna_texto, fuente = e.texto
                    largo = d.modelo.__table__.c[columna_texto].type.length
                    texto = (getattr(obj, fuente) or getattr(obj, f"{ref.prefijo}_Nombre")) if obj is not None else None
                    limpios[columna_texto] = texto[:largo] if texto else None
            limpios[columna] = valor
        return limpios

    async def crear(self, clave: str, datos: dict, *, usuario_id=None, ip=None) -> dict:
        d = definicion(clave)
        if not d.permite_crear:
            raise HTTPException(409, "CATALOG_CLOSED")
        nombre = datos["nombre"].strip()
        if len(nombre) > d.largo_nombre:
            raise HTTPException(422, "CATALOG_NAME_TOO_LONG")
        await self._nombre_libre(d, nombre)
        valores: dict[str, Any] = {f"{d.prefijo}_Nombre": nombre}
        if d.col("Descripcion") is not None:
            valores[d.col("Descripcion").name] = (datos.get("descripcion") or "").strip() or None
        if d.codigo_automatico:
            valores[d.col("Codigo").name] = await self._codigo_libre(d, nombre)
        orden = datos.get("orden")
        if orden is None:
            maximo = (await self.db.execute(select(func.max(d.col("Orden"))))).scalar() or 0
            orden = maximo + 10
        valores[f"{d.prefijo}_Orden"] = orden
        valores.update(await self._validar_extras(d, datos.get("extras") or {}))
        obj = d.modelo(**valores)
        self.db.add(obj)
        await self.db.flush()
        await self.gov_repo.create_audit_log(
            "CREATE", d.tabla, {"id": getattr(obj, d.pk.name), "nombre": nombre},
            usuario_id=usuario_id, ip_origen=ip,
        )
        await commit_or_409(self.db, where="CatalogoGenericoService.crear")
        return self.serializar(d, obj)

    async def editar(self, clave: str, id_: int, datos: dict, *, usuario_id=None, ip=None) -> dict:
        d = definicion(clave)
        obj = await self._obtener(d, id_)
        codigo_col = d.col("Codigo")
        es_sistema = codigo_col is not None and getattr(obj, codigo_col.name) in d.sistema
        cambios: dict[str, Any] = {}
        if "nombre" in datos and datos["nombre"] is not None:
            nombre = datos["nombre"].strip()
            if nombre != getattr(obj, f"{d.prefijo}_Nombre"):
                # La UI traduce los registros de sistema por su código: renombrarlos no tendría efecto visible.
                if es_sistema:
                    raise HTTPException(409, "CATALOG_SYSTEM_ITEM")
                if len(nombre) > d.largo_nombre:
                    raise HTTPException(422, "CATALOG_NAME_TOO_LONG")
                await self._nombre_libre(d, nombre, excepto=id_)
                cambios[f"{d.prefijo}_Nombre"] = nombre
        if "descripcion" in datos and d.col("Descripcion") is not None:
            cambios[d.col("Descripcion").name] = (datos["descripcion"] or "").strip() or None
        if datos.get("orden") is not None:
            cambios[f"{d.prefijo}_Orden"] = datos["orden"]
        if datos.get("activo") is not None and datos["activo"] != getattr(obj, f"{d.prefijo}_Activo"):
            if es_sistema and not datos["activo"]:
                raise HTTPException(409, "CATALOG_SYSTEM_ITEM")
            if datos["activo"] and getattr(obj, f"{d.prefijo}_Fusionado_En") is not None:
                raise HTTPException(409, "CATALOG_ITEM_MERGED")
            cambios[f"{d.prefijo}_Activo"] = datos["activo"]
        if datos.get("extras"):
            cambios.update(await self._validar_extras(d, datos["extras"]))
        for columna, valor in cambios.items():
            setattr(obj, columna, valor)
        if cambios:
            await self.gov_repo.create_audit_log(
                "UPDATE", d.tabla, {"id": id_, "cambios": cambios}, usuario_id=usuario_id, ip_origen=ip,
            )
        await commit_or_409(self.db, where="CatalogoGenericoService.editar")
        return self.serializar(d, obj)

    async def reordenar(self, clave: str, ids: list[int], *, usuario_id=None, ip=None) -> None:
        d = definicion(clave)
        for posicion, id_ in enumerate(ids, start=1):
            await self.db.execute(update(d.modelo).where(d.pk == id_).values({d.col("Orden"): posicion * 10}))
        await self.gov_repo.create_audit_log(
            "CATALOG_REORDER", d.tabla, {"ids": ids}, usuario_id=usuario_id, ip_origen=ip,
        )
        await commit_or_409(self.db, where="CatalogoGenericoService.reordenar")

    # ── Fusión ───────────────────────────────────────────────────────────────
    async def _validar_fusion(self, d: CatalogoDef, origen_id: int, destino_id: int):
        if origen_id == destino_id:
            raise HTTPException(422, "CATALOG_MERGE_SAME_ITEM")
        origen = await self._obtener(d, origen_id)
        destino = await self._obtener(d, destino_id)
        codigo_col = d.col("Codigo")
        if codigo_col is not None and getattr(origen, codigo_col.name) in d.sistema:
            raise HTTPException(409, "CATALOG_SYSTEM_ITEM")
        if getattr(origen, f"{d.prefijo}_Fusionado_En") is not None:
            raise HTTPException(409, "CATALOG_ITEM_MERGED")
        if not getattr(destino, f"{d.prefijo}_Activo"):
            raise HTTPException(409, "CATALOG_MERGE_TARGET_INACTIVE")
        return origen, destino

    async def _conteos(self, d: CatalogoDef, origen) -> dict[str, int]:
        conteos: dict[str, int] = {}
        origen_id = getattr(origen, d.pk.name)
        for col in referencias(d):
            n = (await self.db.execute(select(func.count()).where(col == origen_id))).scalar_one()
            if n:
                conteos[f"{col.table.name}.{col.name}"] = n
        codigo = getattr(origen, d.col("Codigo").name) if d.col("Codigo") is not None else None
        for tabla, columna in d.codigos if codigo else ():
            col = Base.metadata.tables[tabla].c[columna]
            n = (await self.db.execute(select(func.count()).where(col == codigo))).scalar_one()
            if n:
                conteos[f"{tabla}.{columna}"] = n
        return conteos

    async def previa_fusion(self, clave: str, origen_id: int, destino_id: int) -> dict:
        d = definicion(clave)
        origen, _destino = await self._validar_fusion(d, origen_id, destino_id)
        conteos = await self._conteos(d, origen)
        if d.clave == "marcas":
            # Los activos de los modelos de la marca también cambian de marca.
            from app.models.core import Activo
            conteos["INV_ACTIVO.MAR_Marca"] = (await self.db.execute(
                select(func.count()).select_from(Activo).join(Modelo, Modelo.MOD_Modelo == Activo.MOD_Modelo)
                .where(Modelo.MAR_Marca == origen_id)
            )).scalar_one()
        return {"conteos": conteos, "total": sum(conteos.values())}

    async def _fusionar_modelos_de_marca(self, origen_id: int, destino_id: int) -> int:
        """
        Modelos de la marca absorbida que ya existen en el destino (mismo nombre):
        sus activos y referencias pasan al modelo del destino y el duplicado se
        elimina (quedó vacío). Devuelve cuántos modelos se unieron.
        """
        destino = {
            (n or "").strip().lower(): m for m, n in (await self.db.execute(
                select(Modelo.MOD_Modelo, Modelo.MOD_Nombre).where(Modelo.MAR_Marca == destino_id)
            )).all()
        }
        unidos = 0
        pk_modelo = Modelo.__table__.c.MOD_Modelo
        refs = [fk.parent for t in Base.metadata.sorted_tables for fk in t.foreign_keys if fk.column is pk_modelo]
        for mod_id, nombre in (await self.db.execute(
            select(Modelo.MOD_Modelo, Modelo.MOD_Nombre).where(Modelo.MAR_Marca == origen_id)
        )).all():
            gemelo = destino.get((nombre or "").strip().lower())
            if gemelo is None:
                continue
            for col in refs:
                await self.db.execute(update(col.table).where(col == mod_id).values({col.name: gemelo}))
            await self.db.execute(Modelo.__table__.delete().where(pk_modelo == mod_id))
            unidos += 1
        return unidos

    async def fusionar(self, clave: str, origen_id: int, destino_id: int, *, usuario_id=None, ip=None) -> dict:
        d = definicion(clave)
        # Los catálogos son globales: fusionar mueve registros de todas las
        # sedes, así que solo lo hace una cuenta con alcance global.
        scope = current_scope()
        if scope is not None and not scope.is_global:
            raise HTTPException(403, "CATALOG_MERGE_REQUIRES_GLOBAL_SCOPE")
        origen, destino = await self._validar_fusion(d, origen_id, destino_id)
        previa = await self.previa_fusion(clave, origen_id, destino_id)
        nombre_destino = getattr(destino, f"{d.prefijo}_Nombre")
        modelos_unidos = 0
        try:
            async with self.db.begin_nested():
                if d.clave == "marcas":
                    modelos_unidos = await self._fusionar_modelos_de_marca(origen_id, destino_id)
                for col in referencias(d):
                    valores = {col.name: destino_id}
                    texto = d.textos.get((col.table.name, col.name))
                    if texto:
                        columna_texto, fuente = texto
                        largo = col.table.c[columna_texto].type.length
                        valores[columna_texto] = (getattr(destino, fuente) or nombre_destino)[:largo]
                    await self.db.execute(update(col.table).where(col == origen_id).values(valores))
                codigo_col = d.col("Codigo")
                codigo_origen = getattr(origen, codigo_col.name) if codigo_col is not None else None
                codigo_destino = getattr(destino, codigo_col.name) if codigo_col is not None else None
                for tabla, columna in d.codigos if codigo_origen and codigo_destino else ():
                    col = Base.metadata.tables[tabla].c[columna]
                    await self.db.execute(update(col.table).where(col == codigo_origen).values({columna: codigo_destino}))
        except IntegrityError:
            raise HTTPException(409, "CATALOG_MERGE_CONFLICT")
        setattr(origen, f"{d.prefijo}_Activo", False)
        setattr(origen, f"{d.prefijo}_Fusionado_En", destino_id)
        setattr(origen, f"{d.prefijo}_Fusionado_Fecha", _ahora())
        await self.gov_repo.create_audit_log(
            "CATALOG_MERGE", d.tabla,
            {"origen": origen_id, "origen_nombre": getattr(origen, f"{d.prefijo}_Nombre"),
             "destino": destino_id, "destino_nombre": nombre_destino,
             "conteos": previa["conteos"], "modelos_unidos": modelos_unidos},
            usuario_id=usuario_id, ip_origen=ip,
        )
        await commit_or_409(self.db, where="CatalogoGenericoService.fusionar")
        return {**previa, "modelos_unidos": modelos_unidos}

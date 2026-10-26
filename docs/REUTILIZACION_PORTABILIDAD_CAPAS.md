# Reutilización, capas y portabilidad

## Diagnóstico

- **Reutilización estimada: 75–85%** para nuevas features de negocio. Ya existe
  una separación consistente de endpoints, servicios, repositorios, modelos,
  esquemas, API frontend, hooks y features.
- **Motor de base de datos:** este repositorio es solo PostgreSQL.
- **Portabilidad actual: media hacia MySQL**. SQLAlchemy y el módulo de
  dialectos están preparados, pero RLS, índices parciales, `JSONB`, arrays,
  `RETURNING` y algunas migraciones PostgreSQL requieren sustitutos.

## Capas objetivo

1. Presentación: FastAPI/React, sin reglas persistentes.
2. Aplicación: servicios/casos de uso, transacciones y permisos.
3. Dominio: estados, invariantes, fechas y políticas puras.
4. Puertos: repositorios, almacenamiento, correo y reloj.
5. Adaptadores: SQLAlchemy por dialecto, PostgreSQL RLS, MySQL y S3.

La regla es que una capa interna nunca importe una capa externa. El backend
existente ya cumple gran parte de este esquema; `app/prototypes` ofrece una
zona independiente para completar el dominio sin acoplarlo a SQL.

## Reglas de no rotura

- El frontend puede ocultar acciones, pero el backend siempre valida permiso,
  estado, concurrencia e idempotencia.
- Toda fecha se transporta como ISO-8601 y se valida como rango; nunca se
  interpreta con `new Date('dd/mm/yyyy')`.
- Una asignación, devolución, transferencia o cambio de estado debe ser una
  transacción única y producir auditoría.
- Los cambios de motor deben pasar compilación de modelos, migraciones y
  pruebas de contrato por dialecto; RLS se implementa con un adaptador y
  controles equivalentes en aplicación cuando el motor no lo soporte.

## Riesgos pendientes para producción

1. Ejecutar CI con PostgreSQL real y añadir la matriz MySQL 8.0.
2. Separar por completo las migraciones específicas de PostgreSQL de las
   migraciones portables.
3. Añadir pruebas de concurrencia para doble asignación y doble devolución.
4. Confirmar reglas de negocio con usuarios antes de promover prototipos a
   pantallas productivas.

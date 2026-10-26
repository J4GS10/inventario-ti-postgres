"""
Roles del sistema y reglas de gobierno de identidades (fuente única).

Separación de funciones:
- SUPER_ADMIN      configuración del sistema y cualquier identidad.
- ADMIN_SEGURIDAD  identidades y accesos: crear usuarios, asignar roles
                   operativos, restablecer contraseñas y MFA, desbloquear,
                   cerrar sesiones, leer la auditoría. Sin acceso al inventario.
- JEFE_TI          lo mismo que ADMIN_TI y, además, aprueba las bajas y los
                   sobrantes de compras que solicita otra persona.
- ADMIN_TI / TECNICO / CONSULTA  operación del inventario (sin gestión de usuarios).
- AUDITOR          solo lectura del inventario, más la bitácora de auditoría y
                   su exportación. Sin ninguna operación de escritura.
- COMPRAS          Administración: proveedores, órdenes de compra y facturas.
                   No recibe en bodega (lo confirma TI) ni ve personas,
                   asignaciones ni licencias.
- CONTABILIDAD     Administración: lectura de valores (activos, consumibles,
                   órdenes, licencias y bajas) y exportaciones. Sin datos personales.

Cada módulo se protege en el servidor con estos grupos (app/api/v1/api.py y
cada endpoint): lo que un rol no debe ver tampoco se puede pedir a la API.
TECNICO y CONSULTA no reciben costos ni montos (middleware ocultar_costos).

Alcance de datos (ver app/core/data_scope.py): SUPER_ADMIN y ADMIN_SEGURIDAD
son siempre globales; el resto ve las sedes que se le asignen, o todas si un
SUPER_ADMIN le otorga alcance global.
"""
from __future__ import annotations

SUPER_ADMIN = "SUPER_ADMIN"
ADMIN_SEGURIDAD = "ADMIN_SEGURIDAD"
JEFE_TI = "JEFE_TI"
ADMIN_TI = "ADMIN_TI"
TECNICO = "TECNICO"
AUDITOR = "AUDITOR"
CONSULTA = "CONSULTA"
COMPRAS = "COMPRAS"
CONTABILIDAD = "CONTABILIDAD"

ALL_ROLES: tuple[str, ...] = (SUPER_ADMIN, ADMIN_SEGURIDAD, JEFE_TI, ADMIN_TI, TECNICO, AUDITOR, CONSULTA, COMPRAS,
                              CONTABILIDAD)
ROLE_PATTERN = "^(" + "|".join(ALL_ROLES) + ")$"

# Quién administra identidades.
IAM_ROLES: tuple[str, ...] = (SUPER_ADMIN, ADMIN_SEGURIDAD)
# Quién lee la bitácora de auditoría (el auditor, filtrada por su alcance).
AUDIT_READER_ROLES: tuple[str, ...] = (SUPER_ADMIN, ADMIN_SEGURIDAD, AUDITOR)
# Roles con alcance global implícito (no se limitan por sede).
ALWAYS_GLOBAL_ROLES: tuple[str, ...] = (SUPER_ADMIN, ADMIN_SEGURIDAD)
# Roles del inventario a los que se asigna alcance (global o por sedes).
SCOPED_ROLES: tuple[str, ...] = (JEFE_TI, ADMIN_TI, TECNICO, AUDITOR, CONSULTA, COMPRAS, CONTABILIDAD)
# Quién accede al inventario (ADMIN_SEGURIDAD no: separación de funciones).
BUSINESS_ROLES: tuple[str, ...] = (SUPER_ADMIN, JEFE_TI, ADMIN_TI, TECNICO, AUDITOR, CONSULTA)
# ── Grupos por módulo (fuente única para la API y la interfaz) ──
# Cualquier rol de la aplicación salvo el de identidades (catálogos, sedes, búsqueda).
APP_ROLES: tuple[str, ...] = BUSINESS_ROLES + (COMPRAS, CONTABILIDAD)
# Operación del inventario (activos, movimientos, trazabilidad, mantenimiento, tomas).
INVENTORY_READ_ROLES: tuple[str, ...] = BUSINESS_ROLES
# Activos y consumibles en lectura: el inventario y, además, Contabilidad (valores).
STOCK_READ_ROLES: tuple[str, ...] = BUSINESS_ROLES + (CONTABILIDAD,)
# Personas y su información (asignaciones, fichas): nunca Consulta ni Administración.
PEOPLE_READ_ROLES: tuple[str, ...] = (SUPER_ADMIN, JEFE_TI, ADMIN_TI, TECNICO, AUDITOR)
# Software y licencias (sin credenciales): los técnicos instalan; Contabilidad valora.
SOFTWARE_READ_ROLES: tuple[str, ...] = (SUPER_ADMIN, JEFE_TI, ADMIN_TI, TECNICO, AUDITOR, CONTABILIDAD)
# Compras: quién crea proveedores y órdenes, y quién solo las consulta.
PURCHASE_WRITE_ROLES: tuple[str, ...] = (SUPER_ADMIN, JEFE_TI, ADMIN_TI, COMPRAS)
PURCHASE_READ_ROLES: tuple[str, ...] = PURCHASE_WRITE_ROLES + (AUDITOR, CONTABILIDAD)
# Lectura de control (aprobaciones, calidad de datos).
CONTROL_READ_ROLES: tuple[str, ...] = (SUPER_ADMIN, JEFE_TI, ADMIN_TI, AUDITOR)
# Exportaciones de valores (activos, consumibles, licencias).
FINANCE_EXPORT_ROLES: tuple[str, ...] = (SUPER_ADMIN, JEFE_TI, ADMIN_TI, AUDITOR, CONTABILIDAD)
# No reciben costos ni montos (activos, órdenes, consumibles, licencias, kardex, tablero).
HIDE_COST_ROLES: tuple[str, ...] = (TECNICO, CONSULTA)

# Quién aprueba bajas y sobrantes (nunca la misma persona que lo solicita).
APPROVER_ROLES: tuple[str, ...] = (SUPER_ADMIN, JEFE_TI)
# Roles que solo un SUPER_ADMIN puede otorgar o administrar (evita que un
# administrador de seguridad se perpetúe o eleve a otros a su mismo nivel).
PROTECTED_ROLES: tuple[str, ...] = (SUPER_ADMIN, ADMIN_SEGURIDAD)


def can_manage(requester_role: str, target_role: str) -> bool:
    """¿Puede `requester_role` administrar una cuenta con `target_role`?"""
    if requester_role == SUPER_ADMIN:
        return True
    if requester_role == ADMIN_SEGURIDAD:
        return target_role not in PROTECTED_ROLES
    return False


def can_grant(requester_role: str, role: str) -> bool:
    """¿Puede `requester_role` asignar `role` a una cuenta?"""
    return can_manage(requester_role, role)


def can_grant_global_scope(requester_role: str) -> bool:
    """Solo un SUPER_ADMIN otorga alcance global (ver datos de todas las sedes)."""
    return requester_role == SUPER_ADMIN

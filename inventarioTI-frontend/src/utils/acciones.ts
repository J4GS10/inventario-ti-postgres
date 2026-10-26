import { catalogSlug } from './catalogLabels';
import type { UsoActivo } from './clasesActivo';

/** Acciones posibles sobre un activo (el menú «⋯» de las tablas y la ficha). */
export type AccionActivo =
  | 'ver' | 'editar' | 'asignar' | 'devolver' | 'transferir' | 'mantenimiento' | 'ver_ticket'
  | 'etiqueta' | 'hoja_vida' | 'baja' | 'instalar' | 'reubicar' | 'retirar';

export interface PermisosAcciones {
  canOperate: boolean;
  canAdminInventory: boolean;
}

/**
 * Acciones válidas según el estado del activo y el rol: asignar si está libre,
 * devolver o transferir si está asignado, ir al ticket si está en reparación;
 * ver, etiqueta y hoja de vida siempre. Fuente única para todas las pantallas.
 */
export function accionesDisponibles(estadoNombre: string | null | undefined, permisos: PermisosAcciones,
  uso: UsoActivo = 'PERSONAL'): AccionActivo[] {
  const estado = catalogSlug(estadoNombre ?? '');
  const libre = estado === 'disponible' || estado === 'en_bodega';
  const asignado = estado === 'asignado';
  const instalado = estado === 'instalado';
  const reparacion = estado === 'en_reparacion';
  const baja = estado === 'baja';
  // Una impresora o un switch se instalan en un área; un insumo no se mueve (se reclasifica).
  const compartido = uso === 'COMPARTIDO';
  const acciones: AccionActivo[] = ['ver'];
  if (permisos.canOperate && !baja) acciones.push('editar');
  if (permisos.canOperate && libre && compartido) acciones.push('instalar');
  else if (permisos.canOperate && libre && uso !== 'INSUMO') acciones.push('asignar');
  if (permisos.canOperate && asignado) acciones.push('devolver', 'transferir');
  if (permisos.canOperate && instalado) acciones.push('reubicar', 'retirar');
  if (reparacion) acciones.push('ver_ticket');
  else if (permisos.canOperate && (libre || asignado || instalado)) acciones.push('mantenimiento');
  acciones.push('hoja_vida');
  if (permisos.canOperate && !baja) acciones.push('etiqueta');
  if (permisos.canAdminInventory && libre) acciones.push('baja');
  return acciones;
}

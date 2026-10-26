import { Chip, Tooltip } from '@mui/material';
import type { ChipProps } from '@mui/material';

export type StatusVariant =
  | 'disponible'
  | 'asignado'
  | 'bodega'
  | 'reparacion'
  | 'baja'
  | 'activo'
  | 'inactivo'
  | 'abierto'
  | 'cerrado'
  | 'borrador'
  | 'recibido'
  | 'cancelado'
  | 'vigente'
  | 'por_vencer'
  | 'vencido'
  | 'default';

const VARIANT_COLOR: Record<StatusVariant, ChipProps['color']> = {
  disponible: 'success',
  asignado: 'info',
  bodega: 'warning',
  reparacion: 'secondary',
  baja: 'error',
  activo: 'success',
  inactivo: 'default',
  abierto: 'warning',
  cerrado: 'success',
  borrador: 'default',
  recibido: 'success',
  cancelado: 'error',
  vigente: 'success',
  por_vencer: 'warning',
  vencido: 'error',
  default: 'default',
};

interface StatusChipProps {
  label: string;
  variant?: StatusVariant;
  tooltip?: string;
  size?: ChipProps['size'];
}

/**
 * Chip de estado semántico.
 * Usa `variant` para aplicar el color correcto sin depender de strings hardcodeados.
 * Si no se pasa variant, intenta resolver desde el label en minúsculas.
 */
export const StatusChip = ({
  label,
  variant,
  tooltip,
  size = 'small',
}: StatusChipProps) => {
  const resolvedVariant =
    variant ?? (label.toLowerCase().replace(/ /g, '_') as StatusVariant);
  const color = VARIANT_COLOR[resolvedVariant] ?? 'default';

  const chip = (
    <Chip
      label={label}
      color={color}
      size={size}
      sx={{ fontWeight: 600, letterSpacing: 0 }}
    />
  );

  return tooltip ? (
    <Tooltip title={tooltip} arrow>
      <span>{chip}</span>
    </Tooltip>
  ) : (
    chip
  );
};

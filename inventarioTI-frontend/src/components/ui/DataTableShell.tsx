import {
  Box,
  CircularProgress,
  Paper,
  Skeleton,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TablePagination,
  TableRow,
} from '@mui/material';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { EmptyState } from './EmptyState';
import type { SvgIconComponent } from '@mui/icons-material';
import InboxIcon from '@mui/icons-material/Inbox';

interface Column {
  key: string;
  /** Texto o nodo (p. ej. un Checkbox de "seleccionar todo"). */
  label: ReactNode;
  width?: number | string;
  align?: 'left' | 'center' | 'right';
  padding?: 'normal' | 'checkbox' | 'none';
}

interface DataTableShellProps<T> {
  columns: Column[];
  rows: T[];
  loading?: boolean;
  total?: number;
  page?: number;
  rowsPerPage?: number;
  onPageChange?: (page: number) => void;
  onRowsPerPageChange?: (rows: number) => void;
  renderRow: (row: T, index: number) => ReactNode;
  emptyTitle?: string;
  emptyDescription?: string;
  emptyIcon?: SvgIconComponent;
  emptyAction?: { label: string; onClick: () => void };
  toolbar?: ReactNode;
  stickyHeader?: boolean;
  rowsPerPageOptions?: number[];
  /** Columnas ocultas por el usuario (claves); las celdas se ocultan por posición. */
  hiddenColumns?: ReadonlySet<string>;
  /** Densidad de las filas (preferencia del usuario). */
  density?: 'compact' | 'comfortable';
}

const SKELETON_ROWS = 6;

/**
 * Shell de tabla de datos estándar.
 * Integra: loading skeleton, estado vacío, paginación server-side y toolbar colapsable.
 * Los datos y renderizado de filas son responsabilidad del componente padre.
 */
export const DataTableShell = <T,>({
  columns,
  rows,
  loading = false,
  total,
  page = 0,
  rowsPerPage = 25,
  onPageChange,
  onRowsPerPageChange,
  renderRow,
  emptyTitle,
  emptyDescription,
  emptyIcon = InboxIcon,
  emptyAction,
  toolbar,
  stickyHeader = false,
  rowsPerPageOptions = [10, 25, 50, 100],
  hiddenColumns,
  density = 'compact',
}: DataTableShellProps<T>) => {
  const { t } = useTranslation();
  const showPagination = total !== undefined && onPageChange;
  const isEmpty = !loading && rows.length === 0;

  return (
    <Paper variant="outlined" sx={{ borderRadius: 2, overflow: 'hidden' }}>
      {toolbar && (
        <Box sx={{ p: 2, borderBottom: '1px solid', borderColor: 'divider' }}>
          {toolbar}
        </Box>
      )}

      {/* Sin borde propio (ya lo da el Paper). En pantallas angostas las celdas
          no hacen wrap: filas compactas y scroll horizontal dentro de la tabla. */}
      <TableContainer sx={{ border: 0, borderRadius: 0 }}>
        <Table
          size="small"
          stickyHeader={stickyHeader}
          sx={{
            '& .MuiTableCell-root': { whiteSpace: { xs: 'nowrap', lg: 'normal' } },
            ...(density === 'comfortable' ? { '& .MuiTableCell-root': { py: 1.5, whiteSpace: { xs: 'nowrap', lg: 'normal' } } } : {}),
            ...Object.fromEntries(columns.flatMap((col, i) => (hiddenColumns?.has(col.key)
              ? [[`& .MuiTableRow-root > .MuiTableCell-root:nth-of-type(${i + 1})`, { display: 'none' }]] : []))),
            // Acciones (columna alineada a la derecha): iconos siempre en una fila.
            '& .MuiTableCell-root.MuiTableCell-alignRight': { whiteSpace: 'nowrap', width: '1%' },
          }}
        >
          <TableHead>
            <TableRow>
              {columns.map((col) => (
                <TableCell
                  key={col.key}
                  align={col.align ?? 'left'}
                  padding={col.padding}
                  sx={{ width: col.width, whiteSpace: 'nowrap' }}
                >
                  {col.label}
                </TableCell>
              ))}
            </TableRow>
          </TableHead>

          <TableBody>
            {loading ? (
              Array.from({ length: SKELETON_ROWS }).map((_, i) => (
                <TableRow key={i}>
                  {columns.map((col) => (
                    <TableCell key={col.key} padding={col.padding}>
                      <Skeleton variant="text" width="80%" />
                    </TableCell>
                  ))}
                </TableRow>
              ))
            ) : isEmpty ? null : (
              rows.map((row, index) => renderRow(row, index))
            )}
          </TableBody>
        </Table>
      </TableContainer>

      {/* El estado vacío va fuera de la tabla: centrado en el ancho visible
          (dentro de un colSpan quedaba fuera de pantalla en móvil). */}
      {isEmpty && (
        <EmptyState
          title={emptyTitle ?? t('common.no_records')}
          description={emptyDescription}
          icon={emptyIcon}
          action={emptyAction}
          compact
        />
      )}

      {showPagination && (
        <Stack
          direction="row"
          alignItems="center"
          justifyContent="flex-end"
          sx={{ borderTop: '1px solid', borderColor: 'divider', px: 1 }}
        >
          {loading && (
            <Box sx={{ mr: 'auto', pl: 1 }}>
              <CircularProgress size={16} thickness={4} />
            </Box>
          )}
          <TablePagination
            component="div"
            count={total}
            page={page}
            rowsPerPage={rowsPerPage}
            onPageChange={(_, p) => onPageChange(p)}
            onRowsPerPageChange={(e) =>
              onRowsPerPageChange?.(parseInt(e.target.value, 10))
            }
            rowsPerPageOptions={rowsPerPageOptions}
            sx={{ '& .MuiTablePagination-selectLabel, & .MuiTablePagination-input': { display: { xs: 'none', sm: 'flex' } } }}
            labelRowsPerPage={t('common.table.rows_per_page')}
            labelDisplayedRows={({ from, to, count }) =>
              count !== -1
                ? t('common.table.displayed_rows', { from, to, count })
                : t('common.table.displayed_rows_more', { from, to })
            }
          />
        </Stack>
      )}
    </Paper>
  );
};

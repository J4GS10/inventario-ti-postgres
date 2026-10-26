import { useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Box, TableCell, TableRow, Button, Chip, IconButton, Tooltip, } from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import { PageHeader, DataTableShell } from '../../components/ui';
import VisibilityIcon from '@mui/icons-material/Visibility';
import DeleteIcon from '@mui/icons-material/DeleteOutline';
import { useTranslation } from 'react-i18next';
import { useSnackbar } from 'notistack';
import { getApiErrorMessage } from '../../api/errors';
import { maintenanceService } from './maintenanceService';
import { Mantenimiento } from '../../types/maintenance';
import { MaintenanceDialog } from './MaintenanceDialog';
import { MaintenanceDetailDialog } from './MaintenanceDetailDialog';
import { useConfirm } from '../../components/confirm-context';
import { usePermissions } from '../../hooks/usePermissions';
import { formatMoney } from '../../utils/currency';
export const MaintenancePage = () => {
    const { t, i18n } = useTranslation();
    const { canOperate, canAdminInventory } = usePermissions();
    const confirm = useConfirm();
    const { enqueueSnackbar } = useSnackbar();
    const [tickets, setTickets] = useState<Mantenimiento[]>([]);
    const [loading, setLoading] = useState(true);
    const [searchParams, setSearchParams] = useSearchParams();
    // ?nuevo=<activo>: llega desde la ficha del activo o una devolución con daño.
    const activoNuevo = searchParams.get('nuevo');
    const [createOpen, setCreateOpen] = useState(!!activoNuevo);
    const [detailOpen, setDetailOpen] = useState(false);
    const [selected, setSelected] = useState<Mantenimiento | null>(null);
    const loadData = useCallback(() => {
        setLoading(true);
        maintenanceService.getAll()
            .then(setTickets)
            .catch(() => enqueueSnackbar(t('common.error_loading'), { variant: 'error' }))
            .finally(() => setLoading(false));
    }, [enqueueSnackbar, t]);
    useEffect(() => { void Promise.resolve().then(loadData); }, [loadData]);
    const handleView = (row: Mantenimiento) => { setSelected(row); setDetailOpen(true); };
    const handleDelete = async (row: Mantenimiento) => {
        if (!row.MAN_Fecha_Cierre) {
            enqueueSnackbar(t('maintenance.cant_delete_open'), {
                variant: 'warning',
            });
            return;
        }
        if (!(await confirm({ message: t('common.confirm_delete'), destructive: true, confirmText: t('common.delete') })))
            return;
        try {
            await maintenanceService.remove(row.MAN_Mantenimiento);
            enqueueSnackbar(t('common.success_update'), { variant: 'success' });
            loadData();
        }
        catch (err: unknown) {
            enqueueSnackbar(getApiErrorMessage(err, t('common.error_save')), { variant: 'error' });
        }
    };
    return (<Box>
      <PageHeader help="maintenance"
        title={t('maintenance.title')}
        badge={tickets.length}
        actions={canOperate ? <Button variant="contained" startIcon={<AddIcon />} onClick={() => setCreateOpen(true)}>{t('maintenance.new_ticket')}</Button> : undefined}
      />

      <MaintenanceDialog open={createOpen} activoInicialId={activoNuevo} onClose={(refresh) => { setCreateOpen(false); if (activoNuevo) setSearchParams({}); if (refresh)
        loadData(); }}/>
      <MaintenanceDetailDialog open={detailOpen} ticket={selected} onClose={() => { setDetailOpen(false); setSelected(null); }} onChanged={loadData}/>

      <DataTableShell
        columns={[
          { key: 'date', label: t('maintenance.col_date') },
          { key: 'asset', label: t('maintenance.col_asset') },
          { key: 'type', label: t('maintenance.col_type') },
          { key: 'failure', label: t('maintenance.col_failure') },
          { key: 'cost', label: t('maintenance.col_cost') },
          { key: 'status', label: t('common.active') },
          { key: 'actions', label: t('common.actions'), align: 'right' },
        ]}
        rows={tickets}
        loading={loading}
        emptyTitle={t('common.no_records')}
        renderRow={(row) => {
            const cerrado = !!row.MAN_Fecha_Cierre;
            return (<TableRow key={row.MAN_Mantenimiento} hover>
                <TableCell>{new Date(row.MAN_Fecha_Ingreso).toLocaleDateString()}</TableCell>
                <TableCell sx={{ fontWeight: 'bold' }}>{row.activo?.ACT_Codigo_Interno ?? '—'}</TableCell>
                <TableCell>{row.tipo_mantenimiento?.TMA_Nombre ?? '—'}</TableCell>
                <TableCell sx={{ maxWidth: 280, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  {row.MAN_Descripcion_Falla}
                </TableCell>
                <TableCell sx={{ whiteSpace: 'nowrap' }}>{formatMoney(row.MAN_Costo_Total, row.MAN_Moneda, i18n.language)}</TableCell>
                <TableCell>
                  <Chip label={cerrado ? t('common.closed') : t('maintenance.open')} color={cerrado ? 'success' : 'warning'} size="small"/>
                </TableCell>
                <TableCell align="right">
                  <Tooltip title={t('common.view') as string}>
                    <IconButton size="small" onClick={() => handleView(row)}>
                      <VisibilityIcon fontSize="small"/>
                    </IconButton>
                  </Tooltip>
                  {cerrado && canAdminInventory && (<Tooltip title={t('common.delete') as string}>
                      <IconButton size="small" color="error" onClick={() => handleDelete(row)}>
                        <DeleteIcon fontSize="small"/>
                      </IconButton>
                    </Tooltip>)}
                </TableCell>
              </TableRow>);
        }}
      />
    </Box>);
};

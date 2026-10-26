import { useEffect, useState } from 'react';
import {
  Alert, Button, Checkbox, CircularProgress, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel,
  Stack, TextField,
} from '@mui/material';
import { useTranslation } from 'react-i18next';
import { useSnackbar } from 'notistack';
import { inventarioService } from '../../api/inventarioService';
import { getApiErrorCode, getApiErrorMessage, getApiErrorParams } from '../../api/errors';
import type { CatalogoItem } from '../../api/catalogoService';
import { CatalogField } from '../../components/CatalogField';
import { pideDetalle } from '../../hooks/useCatalogo';
import { hoyLocal } from '../../utils/dates';

type ActivoRef = { ACT_Activo: string; ACT_Codigo_Interno: string };

interface Props {
  open: boolean;
  activo: ActivoRef | null;
  /** Solicitud de baja para varios equipos con los mismos datos (cada uno queda pendiente de aprobación). */
  activos?: ActivoRef[];
  onClose: (changed: boolean) => void;
}

/**
 * Baja formal: motivo, destino, fecha y documento de respaldo. Es una SALIDA
 * del inventario que queda en el kardex; libera las licencias del equipo y,
 * si tiene componentes instalados, pide confirmación para retirarlos.
 */
export const BajaDialog = ({ open, activo, activos, onClose }: Props) => {
  const lote = activos && activos.length > 0 ? activos : null;
  const { t } = useTranslation();
  const { enqueueSnackbar } = useSnackbar();
  const [motivoItem, setMotivoItem] = useState<CatalogoItem | null>(null);
  const [destinoItem, setDestinoItem] = useState<CatalogoItem | null>(null);
  const motivo = motivoItem?.codigo ?? '';
  const destino = destinoItem?.codigo ?? '';
  const [fecha, setFecha] = useState(hoyLocal());
  const [documento, setDocumento] = useState('');
  const [observacion, setObservacion] = useState('');
  const [componentes, setComponentes] = useState<number | null>(null);
  const [retirar, setRetirar] = useState(false);
  const [enviando, setEnviando] = useState(false);

  useEffect(() => {
    if (!open) return;
    setMotivoItem(null);
    setDestinoItem(null);
    setFecha(hoyLocal());
    setDocumento('');
    setObservacion('');
    setComponentes(null);
    setRetirar(false);
  }, [open]);

  const requiereDetalle = pideDetalle(motivoItem) || pideDetalle(destinoItem);
  const valido = !!motivo && !!destino && (!requiereDetalle || !!observacion.trim()) && (componentes == null || retirar);

  const confirmarLote = async (lista: ActivoRef[]) => {
    if (!motivo || !destino) return;
    setEnviando(true);
    const fallidos: string[] = [];
    for (const a of lista) {
      try {
        await inventarioService.darDeBaja(a.ACT_Activo, {
          motivo, destino, fecha, documento: documento.trim() || undefined,
          observacion: observacion.trim() || undefined, retirar_componentes: retirar,
        });
      } catch {
        fallidos.push(a.ACT_Codigo_Interno);
      }
    }
    setEnviando(false);
    const hechos = lista.length - fallidos.length;
    if (hechos) enqueueSnackbar(t('decommission.requested_batch', { count: hechos }), { variant: 'success' });
    if (fallidos.length) {
      enqueueSnackbar(t('decommission.failed_batch', { list: fallidos.join(', ') }), { variant: 'warning', autoHideDuration: 10000 });
    }
    onClose(hechos > 0);
  };

  const confirmar = async () => {
    if (lote) { await confirmarLote(lote); return; }
    if (!activo || !motivo || !destino) return;
    setEnviando(true);
    try {
      await inventarioService.darDeBaja(activo.ACT_Activo, {
        motivo, destino, fecha, documento: documento.trim() || undefined,
        observacion: observacion.trim() || undefined, retirar_componentes: retirar,
      });
      // Separación de funciones: la baja la ejecuta el Jefe de TI al aprobarla.
      enqueueSnackbar(t('decommission.requested', { code: activo.ACT_Codigo_Interno }), { variant: 'success' });
      onClose(true);
    } catch (err) {
      if (getApiErrorCode(err) === 'ASSET_HAS_COMPONENTS') {
        // Diálogo: se explica por qué no se pudo y se ofrece la solución.
        setComponentes(Number(getApiErrorParams(err)[0]) || 1);
      } else {
        enqueueSnackbar(getApiErrorMessage(err, t('common.error_save')), { variant: 'error' });
      }
    } finally {
      setEnviando(false);
    }
  };

  return (
    <Dialog open={open} onClose={() => onClose(false)} maxWidth="sm" fullWidth>
      <DialogTitle>{lote ? t('decommission.title_batch', { count: lote.length }) : t('decommission.title', { code: activo?.ACT_Codigo_Interno ?? '' })}</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 1 }}>
          <Alert severity="warning">{t('decommission.warning')}</Alert>
          <Alert severity="info">{t('decommission.approval_note')}</Alert>
          <CatalogField clave="motivos-baja" valueBy="codigo" required label={t('decommission.reason')}
            value={motivo || null} onChange={(_v, item) => setMotivoItem(item)} />
          <CatalogField clave="destinos-baja" valueBy="codigo" required label={t('decommission.destination')}
            value={destino || null} onChange={(_v, item) => setDestinoItem(item)} />
          <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
            <TextField type="date" label={t('decommission.date')} value={fecha} onChange={(e) => setFecha(e.target.value)}
              InputLabelProps={{ shrink: true }} inputProps={{ max: hoyLocal() }} fullWidth />
            <TextField label={t('decommission.document')} value={documento} onChange={(e) => setDocumento(e.target.value)}
              helperText={t('decommission.document_help')} inputProps={{ maxLength: 120 }} fullWidth />
          </Stack>
          <TextField label={t('decommission.note')} value={observacion} onChange={(e) => setObservacion(e.target.value)}
            multiline minRows={2} inputProps={{ maxLength: 500 }} required={requiereDetalle}
            error={requiereDetalle && !observacion.trim()}
            helperText={requiereDetalle ? t('decommission.note_required') : undefined} />
          {lote && (
            <FormControlLabel
              control={<Checkbox checked={retirar} onChange={(e) => setRetirar(e.target.checked)} />}
              label={t('decommission.remove_components_batch')} />
          )}
          {componentes != null && (
            <Alert severity="info">
              {t('decommission.has_components', { count: componentes })}
              <FormControlLabel sx={{ display: 'block', mt: 1 }}
                control={<Checkbox checked={retirar} onChange={(e) => setRetirar(e.target.checked)} />}
                label={t('decommission.remove_components')} />
            </Alert>
          )}
        </Stack>
      </DialogContent>
      <DialogActions sx={{ p: 3 }}>
        <Button color="inherit" onClick={() => onClose(false)}>{t('common.cancel')}</Button>
        <Button variant="contained" color="error" onClick={confirmar} disabled={!valido || enviando}>
          {enviando ? <CircularProgress size={22} /> : t('decommission.request')}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

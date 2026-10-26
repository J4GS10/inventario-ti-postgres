import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Box, Button, Chip, FormControlLabel, IconButton, MenuItem, Stack, Switch, TableCell, TableRow, TextField, Tooltip,
  Typography,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import DragIndicatorIcon from '@mui/icons-material/DragIndicator';
import CallMergeIcon from '@mui/icons-material/CallMerge';
import LockIcon from '@mui/icons-material/LockOutlined';
import { useTranslation } from 'react-i18next';
import { useSnackbar } from 'notistack';
import {
  catalogoService, type CatalogoDef, type CatalogoExtraDef, type CatalogoItem, type MovimientoAplica,
} from '../../../api/catalogoService';
import { getApiErrorMessage } from '../../../api/errors';
import { DataTableShell } from '../../../components/ui';
import { SearchInput } from '../../../components/SearchInput';
import { CatalogField } from '../../../components/CatalogField';
import { usePermissions } from '../../../hooks/usePermissions';
import { catalogItemLabel } from '../../../utils/catalogLabels';
import { MergeDialog } from './MergeDialog';

const APLICA: MovimientoAplica[] = ['ASIGNACION', 'PRESTAMO', 'TRANSFERENCIA', 'DEVOLUCION', 'INSTALACION', 'RETIRO'];

interface Props {
  def: CatalogoDef;
}

/**
 * Texto que se guarda al salir del campo (edición en línea). Quien lo usa le
 * pasa `key={value}`: si el valor guardado cambia, el campo se reinicia.
 */
const CampoEnLinea = ({ value, onSave, disabled, max, placeholder }: {
  value: string; onSave: (v: string) => void; disabled?: boolean; max: number; placeholder?: string;
}) => {
  const [texto, setTexto] = useState(value);
  return (
    <TextField
      variant="standard" fullWidth value={texto} disabled={disabled} placeholder={placeholder}
      inputProps={{ maxLength: max }}
      onChange={(e) => setTexto(e.target.value)}
      onBlur={() => { if (texto.trim() !== value) onSave(texto.trim()); }}
      onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); }}
    />
  );
};

/**
 * Registros de un catálogo: edición en línea, orden por arrastre, activar o
 * desactivar, campos propios del catálogo y fusión de duplicados.
 */
export const CatalogItemsPanel = ({ def }: Props) => {
  const { t } = useTranslation();
  const { enqueueSnackbar } = useSnackbar();
  const { canAdminInventory } = usePermissions();
  const [items, setItems] = useState<CatalogoItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [busqueda, setBusqueda] = useState('');
  const [inactivos, setInactivos] = useState(false);
  const [nuevo, setNuevo] = useState('');
  const [arrastrando, setArrastrando] = useState<number | null>(null);
  const [fusion, setFusion] = useState<CatalogoItem | null>(null);
  const clave = def.clave;

  const cargar = useCallback(async () => {
    setLoading(true);
    try {
      setItems(await catalogoService.listar(clave, true));
    } catch (err) {
      enqueueSnackbar(getApiErrorMessage(err, t('common.error_loading')), { variant: 'error' });
    } finally {
      setLoading(false);
    }
  }, [clave, enqueueSnackbar, t]);

  useEffect(() => { void cargar(); }, [cargar]);

  const visibles = useMemo(() => {
    const q = busqueda.trim().toLowerCase();
    return items.filter((i) => (inactivos || i.activo)
      && (!q || catalogItemLabel(t, clave, i).toLowerCase().includes(q) || (i.codigo ?? '').toLowerCase().includes(q)));
  }, [items, inactivos, busqueda, t, clave]);

  const guardar = async (item: CatalogoItem, cambios: Parameters<typeof catalogoService.editar>[2]) => {
    try {
      const actualizado = await catalogoService.editar(clave, item.id, cambios);
      setItems((prev) => prev.map((i) => (i.id === item.id ? { ...actualizado, uso: i.uso } : i)));
    } catch (err) {
      enqueueSnackbar(getApiErrorMessage(err, t('common.error_save')), { variant: 'error' });
    }
  };

  const agregar = async () => {
    if (nuevo.trim().length < 1) return;
    try {
      await catalogoService.crear(clave, { nombre: nuevo.trim() });
      setNuevo('');
      enqueueSnackbar(t('catalog_center.created', { name: nuevo.trim() }), { variant: 'success' });
      await cargar();
    } catch (err) {
      enqueueSnackbar(getApiErrorMessage(err, t('common.error_save')), { variant: 'error' });
    }
  };

  const soltar = async (destinoId: number) => {
    if (arrastrando === null || arrastrando === destinoId) return;
    const orden = items.filter((i) => i.activo || inactivos).map((i) => i.id);
    const desde = orden.indexOf(arrastrando);
    const hasta = orden.indexOf(destinoId);
    orden.splice(hasta, 0, ...orden.splice(desde, 1));
    setArrastrando(null);
    setItems((prev) => [...prev].sort((a, b) => orden.indexOf(a.id) - orden.indexOf(b.id)));
    try {
      await catalogoService.reordenar(clave, orden);
    } catch (err) {
      enqueueSnackbar(getApiErrorMessage(err, t('common.error_save')), { variant: 'error' });
      await cargar();
    }
  };

  const extra = (item: CatalogoItem, e: CatalogoExtraDef) => {
    const valor = item.extras[e.columna];
    const bloqueado = !canAdminInventory;
    if (e.tipo === 'bool') {
      return (
        <FormControlLabel
          control={<Switch size="small" checked={valor === true} disabled={bloqueado}
            onChange={(ev) => void guardar(item, { extras: { [e.columna]: ev.target.checked } })} />}
          label={<Typography variant="caption">{t(`catalog_center.extras.${e.columna}`, { defaultValue: t('catalog_center.extras.pide_detalle') })}</Typography>}
        />
      );
    }
    if (e.tipo === 'texto') {
      return (
        <CampoEnLinea key={(valor as string) ?? ''} value={(valor as string) ?? ''} max={e.largo} disabled={bloqueado}
          placeholder={t(`catalog_center.extras.${e.columna}`)}
          onSave={(v) => void guardar(item, { extras: { [e.columna]: v || null } })} />
      );
    }
    if (e.tipo === 'aplica_a') {
      const lista = (valor as MovimientoAplica[] | null) ?? [];
      return (
        <TextField
          select variant="standard" fullWidth disabled={bloqueado}
          SelectProps={{
            multiple: true,
            renderValue: (v) => ((v as string[]).length
              ? (v as string[]).map((x) => t(`catalog_center.applies.${x}`)).join(', ')
              : t('catalog_center.applies.ALL')),
            displayEmpty: true,
          }}
          value={lista}
          onChange={(ev) => void guardar(item, { extras: { [e.columna]: ev.target.value as unknown as string[] } })}
        >
          {APLICA.map((a) => <MenuItem key={a} value={a}>{t(`catalog_center.applies.${a}`)}</MenuItem>)}
        </TextField>
      );
    }
    if (e.tipo === 'catalogo' && e.catalogo) {
      return (
        <CatalogField size="small" clave={e.catalogo} label={t(`catalog_center.extras.${e.columna}`)}
          value={(valor as number) ?? null} disabled={bloqueado} allowCreate={false}
          onChange={(v) => void guardar(item, { extras: { [e.columna]: v } })} />
      );
    }
    return null;
  };

  const columnas = [
    { key: 'drag', label: '', width: 32 },
    { key: 'name', label: t('catalog_center.name') },
    ...(def.tiene_descripcion ? [{ key: 'desc', label: t('catalog_center.description') }] : []),
    ...(def.extras.length ? [{ key: 'extras', label: t('catalog_center.options') }] : []),
    { key: 'uso', label: t('catalog_center.in_use'), align: 'right' as const },
    { key: 'activo', label: t('catalog_center.active'), align: 'center' as const },
    { key: 'actions', label: '', align: 'right' as const },
  ];

  return (
    <Box>
      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1.5} alignItems={{ sm: 'center' }} sx={{ mb: 1.5 }}>
        <Box sx={{ flex: 1, minWidth: 0 }}><SearchInput value={busqueda} onChange={setBusqueda} /></Box>
        <FormControlLabel control={<Switch checked={inactivos} onChange={(e) => setInactivos(e.target.checked)} />}
          label={t('catalog_center.show_inactive')} />
      </Stack>
      {canAdminInventory && (
        <Stack direction="row" spacing={1} sx={{ mb: 1.5 }}>
          <TextField size="small" fullWidth value={nuevo} label={t('catalog_center.new_item')}
            inputProps={{ maxLength: def.largo_nombre }}
            onChange={(e) => setNuevo(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') void agregar(); }} />
          <Button variant="contained" startIcon={<AddIcon />} onClick={() => void agregar()} disabled={!nuevo.trim()}
            sx={{ flexShrink: 0 }}>
            {t('common.add')}
          </Button>
        </Stack>
      )}

      <DataTableShell
        columns={columnas}
        rows={visibles}
        loading={loading}
        emptyTitle={t('common.no_records')}
        renderRow={(item) => (
          <TableRow
            key={item.id}
            draggable={canAdminInventory && !busqueda}
            onDragStart={() => setArrastrando(item.id)}
            onDragOver={(e) => e.preventDefault()}
            onDrop={() => void soltar(item.id)}
            sx={{ opacity: item.activo ? 1 : 0.55, '&:hover': { bgcolor: 'action.hover' } }}
          >
            <TableCell sx={{ cursor: canAdminInventory && !busqueda ? 'grab' : 'default', color: 'text.disabled', px: 1 }}>
              <DragIndicatorIcon fontSize="small" />
            </TableCell>
            <TableCell sx={{ minWidth: 150 }}>
              {item.sistema ? (
                <Stack direction="row" spacing={1} alignItems="center">
                  <Typography variant="body2" fontWeight={600}>{catalogItemLabel(t, clave, item)}</Typography>
                  <Tooltip title={t('catalog_center.system_hint')}>
                    <Chip size="small" icon={<LockIcon />} label={t('catalog_center.system')} variant="outlined" />
                  </Tooltip>
                </Stack>
              ) : (
                <CampoEnLinea key={item.nombre} value={item.nombre} max={def.largo_nombre} disabled={!canAdminInventory || !!item.fusionado_en}
                  onSave={(v) => v && void guardar(item, { nombre: v })} />
              )}
              {item.fusionado_en && (
                <Typography variant="caption" color="text.secondary">
                  {t('catalog_center.merged_into', { name: items.find((i) => i.id === item.fusionado_en)?.nombre ?? '—' })}
                </Typography>
              )}
            </TableCell>
            {def.tiene_descripcion && (
              <TableCell sx={{ minWidth: 110 }}>
                <CampoEnLinea key={item.descripcion ?? ''} value={item.descripcion ?? ''} max={255} disabled={!canAdminInventory}
                  onSave={(v) => void guardar(item, { descripcion: v || null })} />
              </TableCell>
            )}
            {def.extras.length > 0 && (
              <TableCell sx={{ minWidth: 170, maxWidth: 240 }}>
                <Stack spacing={0.5}>{def.extras.map((e) => <Box key={e.columna}>{extra(item, e)}</Box>)}</Stack>
              </TableCell>
            )}
            <TableCell align="right" sx={{ color: 'text.secondary' }}>{item.uso}</TableCell>
            <TableCell align="center">
              <Switch size="small" checked={item.activo}
                disabled={!canAdminInventory || item.sistema || !!item.fusionado_en}
                onChange={(e) => void guardar(item, { activo: e.target.checked })}
                slotProps={{ input: { 'aria-label': t('catalog_center.active') } }} />
            </TableCell>
            <TableCell align="right">
              {canAdminInventory && !item.sistema && item.activo && (
                <Tooltip title={t('catalog_center.merge')}>
                  <IconButton size="small" onClick={() => setFusion(item)} aria-label={t('catalog_center.merge')}>
                    <CallMergeIcon fontSize="small" />
                  </IconButton>
                </Tooltip>
              )}
            </TableCell>
          </TableRow>
        )}
      />
      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
        {t('catalog_center.drag_hint')}
      </Typography>

      <MergeDialog
        open={!!fusion} clave={clave} origen={fusion} items={items.filter((i) => i.activo)}
        onClose={(hecho) => { setFusion(null); if (hecho) void cargar(); }}
      />
    </Box>
  );
};

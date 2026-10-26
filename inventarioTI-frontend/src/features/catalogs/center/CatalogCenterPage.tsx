import { useEffect, useMemo, useState } from 'react';
import {
  Box, List, ListItemButton, ListItemText, ListSubheader, MenuItem, Paper, TextField, useMediaQuery, useTheme,
} from '@mui/material';
import { useTranslation } from 'react-i18next';
import { Link as RouterLink, useSearchParams } from 'react-router-dom';
import { catalogoService, type CatalogoDef, type CatalogoGrupo } from '../../../api/catalogoService';
import { PageHeader, SectionCard } from '../../../components/ui';
import { CatalogItemsPanel } from './CatalogItemsPanel';
import { SpecsByTypePanel } from './SpecsByTypePanel';
import { KitsPanel } from './KitsPanel';

const GRUPOS: CatalogoGrupo[] = ['ACTIVOS', 'MOVIMIENTOS', 'LICENCIAS', 'CONSUMIBLES', 'MANTENIMIENTO', 'UBICACIONES', 'DOCUMENTOS'];

/** Entradas especiales (no son catálogos estándar) y accesos a las páginas que ya existían. */
const ESPECIALES: Record<string, { grupo: CatalogoGrupo }> = {
  'especificaciones-tipo': { grupo: 'ACTIVOS' },
  kits: { grupo: 'MOVIMIENTOS' },
};
const ACCESOS: { clave: string; ruta: string; grupo: CatalogoGrupo }[] = [
  { clave: 'modelos', ruta: '/config/modelos', grupo: 'ACTIVOS' },
  { clave: 'tipos-activo', ruta: '/config/tipos', grupo: 'ACTIVOS' },
  { clave: 'ubicaciones', ruta: '/config/ubicaciones', grupo: 'UBICACIONES' },
];

/**
 * Centro de catálogos: todas las listas que estandarizan los datos (motivos,
 * bajas, unidades, fallas, tipos de documento…) en un solo lugar, con edición
 * en línea, orden, activación y fusión de duplicados.
 */
export const CatalogCenterPage = () => {
  const { t } = useTranslation();
  const theme = useTheme();
  const compacto = useMediaQuery(theme.breakpoints.down('md'));
  const [defs, setDefs] = useState<CatalogoDef[]>([]);
  const [params, setParams] = useSearchParams();
  const actual = params.get('c') ?? 'motivos-movimiento';

  useEffect(() => { catalogoService.definiciones().then(setDefs).catch(() => setDefs([])); }, []);

  const porGrupo = useMemo(() => GRUPOS.map((g) => ({
    grupo: g,
    claves: [
      ...defs.filter((d) => d.grupo === g).map((d) => d.clave as string),
      ...Object.entries(ESPECIALES).filter(([, e]) => e.grupo === g).map(([k]) => k),
    ],
    accesos: ACCESOS.filter((a) => a.grupo === g),
  })).filter((g) => g.claves.length || g.accesos.length), [defs]);

  const elegir = (clave: string) => setParams((p) => { p.set('c', clave); return p; }, { replace: true });
  const def = defs.find((d) => d.clave === actual);
  const titulo = t(`catalog_center.catalogs.${actual}`);

  return (
    <Box>
      <PageHeader help="catalogs" title={t('catalog_center.title')} subtitle={t('catalog_center.subtitle')} />
      <Box sx={{ display: 'flex', gap: 2, flexDirection: compacto ? 'column' : 'row', alignItems: 'flex-start' }}>
        {compacto ? (
          <TextField select fullWidth label={t('catalog_center.catalog')} value={actual} onChange={(e) => elegir(e.target.value)}>
            {porGrupo.flatMap((g) => [
              <ListSubheader key={`h-${g.grupo}`}>{t(`catalog_center.groups.${g.grupo}`)}</ListSubheader>,
              ...g.claves.map((c) => <MenuItem key={c} value={c}>{t(`catalog_center.catalogs.${c}`)}</MenuItem>),
            ])}
          </TextField>
        ) : (
          <Paper variant="outlined" sx={{ width: 260, flexShrink: 0, position: 'sticky', top: 16 }}>
            <List dense disablePadding sx={{ py: 0.5 }}>
              {porGrupo.map((g) => (
                <Box key={g.grupo}>
                  <ListSubheader disableSticky sx={{ lineHeight: '32px', bgcolor: 'transparent' }}>
                    {t(`catalog_center.groups.${g.grupo}`)}
                  </ListSubheader>
                  {g.claves.map((c) => (
                    <ListItemButton key={c} selected={c === actual} onClick={() => elegir(c)} sx={{ pl: 3 }}>
                      <ListItemText primary={t(`catalog_center.catalogs.${c}`)} />
                    </ListItemButton>
                  ))}
                  {g.accesos.map((a) => (
                    <ListItemButton key={a.clave} component={RouterLink} to={a.ruta} sx={{ pl: 3 }}>
                      <ListItemText primary={t(`catalog_center.catalogs.${a.clave}`)}
                        primaryTypographyProps={{ color: 'text.secondary' }} />
                    </ListItemButton>
                  ))}
                </Box>
              ))}
            </List>
          </Paper>
        )}

        <Box sx={{ flex: 1, minWidth: 0, width: '100%' }}>
          <SectionCard title={titulo} subtitle={t(`catalog_center.hints.${actual}`, { defaultValue: '' }) || undefined}>
            {actual === 'especificaciones-tipo' && <SpecsByTypePanel />}
            {actual === 'kits' && <KitsPanel />}
            {def && <CatalogItemsPanel key={def.clave} def={def} />}
          </SectionCard>
        </Box>
      </Box>
    </Box>
  );
};

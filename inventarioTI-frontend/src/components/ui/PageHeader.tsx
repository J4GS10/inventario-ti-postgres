import { Box, Breadcrumbs, Chip, Link, Stack, Typography } from '@mui/material';
import NavigateNextIcon from '@mui/icons-material/NavigateNext';
import type { ReactNode } from 'react';
import { Link as RouterLink, useLocation } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { navItemFor } from '../../router/navigation';
import { PageHelp, type ModuloAyuda } from './PageHelp';

interface PageHeaderProps {
  title: string;
  subtitle?: string;
  badge?: string | number;
  actions?: ReactNode;
  /** Registro abierto (p. ej. el código del activo): se agrega a las migas de pan. */
  crumb?: string;
  /** Módulo con ayuda contextual («¿Cómo se hace?» y recorrido la primera vez). */
  help?: ModuloAyuda;
}

/** Migas de pan: grupo › página › registro. Siempre se sabe dónde se está. */
const Migas = ({ crumb }: { crumb?: string }) => {
  const { t } = useTranslation();
  const { pathname } = useLocation();
  const item = navItemFor(pathname);
  if (!item || item.path === '/') return null;
  const enFicha = !!crumb;
  return (
    <Breadcrumbs separator={<NavigateNextIcon sx={{ fontSize: 14 }} />} aria-label={t('workspace.breadcrumbs')}
      sx={{ mb: 0.5, '& .MuiBreadcrumbs-li': { fontSize: '0.75rem' } }}>
      <Typography variant="caption" color="text.secondary">{t(`menu_groups.${item.group}`)}</Typography>
      {enFicha ? (
        <Link component={RouterLink} to={item.path} underline="hover" variant="caption" color="text.secondary">
          {t(item.title)}
        </Link>
      ) : (
        <Typography variant="caption" color="text.primary" fontWeight={600}>{t(item.title)}</Typography>
      )}
      {enFicha && <Typography variant="caption" color="text.primary" fontWeight={600}>{crumb}</Typography>}
    </Breadcrumbs>
  );
};

/**
 * Encabezado estándar de página: título, subtítulo opcional, badge de conteo y acciones.
 * Las acciones hacen wrap y en pantallas angostas pasan debajo del título, así
 * nunca se salen de la pantalla ni se sobreponen.
 */
export const PageHeader = ({ title, subtitle, badge, actions, crumb, help }: PageHeaderProps) => (
  <Box
    sx={{
      display: 'flex',
      flexWrap: 'wrap',
      alignItems: 'center',
      justifyContent: 'space-between',
      columnGap: 3,
      rowGap: 1.5,
      mb: 3,
    }}
  >
    <Box sx={{ minWidth: 0, flex: '1 1 280px' }}>
      <Migas crumb={crumb} />
      <Stack direction="row" alignItems="center" spacing={1.25}>
        <Typography variant="h5" component="h1" noWrap>
          {title}
        </Typography>
        {badge !== undefined && (
          <Chip label={badge} size="small" sx={{ height: 22, fontSize: '0.75rem', bgcolor: 'action.selected' }} />
        )}
      </Stack>
      {subtitle && (
        <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
          {subtitle}
        </Typography>
      )}
      {help && <Box sx={{ mt: 0.5 }}><PageHelp modulo={help} /></Box>}
    </Box>

    {actions && (
      <Box
        sx={{
          display: 'flex',
          flexWrap: 'wrap',
          alignItems: 'center',
          justifyContent: { xs: 'flex-start', sm: 'flex-end' },
          gap: 1,
          maxWidth: '100%',
        }}
      >
        {actions}
      </Box>
    )}
  </Box>
);

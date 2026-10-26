import { useState } from 'react';
import { Alert, Box, Stack } from '@mui/material';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../../context/auth-context';
import { PageHeader } from '../../components/ui';
import { AdStatusSection } from './AdStatusSection';
import { IntegrationSection } from './IntegrationSection';
import { NotificationRulesSection } from './NotificationRulesSection';

/**
 * Directorio y notificaciones: estado/sincronización con Active Directory y
 * reglas de destinatarios de correo por evento.
 * - ADMIN_TI: puede ver el estado y sincronizar.
 * - SUPER_ADMIN: además configura la cuenta de servicio (correo / AD), prueba la
 *   conexión y edita las reglas de notificación.
 */
export const DirectoryPage = () => {
  const { t } = useTranslation();
  const { user } = useAuth();
  const isSuperAdmin = user?.role === 'SUPER_ADMIN';
  // Se incrementa al guardar la integración para que el estado de AD se recargue.
  const [adRefreshKey, setAdRefreshKey] = useState(0);

  return (
    <Box>
      <PageHeader title={t('directory.title')} subtitle={t('directory.subtitle')} />
      <Stack spacing={3}>
        {isSuperAdmin && <IntegrationSection onSaved={() => setAdRefreshKey((k) => k + 1)} />}
        <AdStatusSection canTestConnection={isSuperAdmin} refreshKey={adRefreshKey} />
        {isSuperAdmin ? (
          <NotificationRulesSection />
        ) : (
          <Alert severity="info">{t('directory.rules.forbidden')}</Alert>
        )}
      </Stack>
    </Box>
  );
};

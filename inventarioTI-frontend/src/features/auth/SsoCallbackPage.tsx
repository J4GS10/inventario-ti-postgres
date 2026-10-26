import { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { Alert, Box, Button, CircularProgress, Stack } from '@mui/material';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../../context/auth-context';
export const SsoCallbackPage = () => {
    const { t } = useTranslation();
    const navigate = useNavigate();
    const { isAuthenticated, isLoading } = useAuth();
    const hasError = !isLoading && !isAuthenticated;
    useEffect(() => {
        if (isLoading)
            return;
        if (isAuthenticated) {
            window.history.replaceState(null, document.title, '/');
            navigate('/', { replace: true });
            return;
        }
    }, [isAuthenticated, isLoading, navigate]);
    if (hasError) {
        return (<Box sx={{ minHeight: '100vh', display: 'grid', placeItems: 'center', p: 3 }}>
        <Stack spacing={2} sx={{ width: '100%', maxWidth: 420 }}>
          <Alert severity="error">{t('auth.sso_error')}</Alert>
          <Button variant="contained" onClick={() => navigate('/login', { replace: true })}>
            {t('auth.back_to_login')}
          </Button>
        </Stack>
      </Box>);
    }
    return (<Box sx={{ minHeight: '100vh', display: 'grid', placeItems: 'center' }}>
      <CircularProgress />
    </Box>);
};

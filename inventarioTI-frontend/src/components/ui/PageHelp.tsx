import { useEffect, useState } from 'react';
import {
  Box, Button, Dialog, DialogActions, DialogContent, DialogTitle, Link, MobileStepper, Typography,
} from '@mui/material';
import HelpOutlineIcon from '@mui/icons-material/HelpOutline';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../../context/auth-context';

export type ModuloAyuda = 'assets' | 'movements' | 'software' | 'catalogs' | 'persons' | 'maintenance';
const PASOS = 4;

const clave = (usuario: string | undefined, modulo: ModuloAyuda) => `ayuda:${usuario ?? 'anon'}:${modulo}`;
const yaVista = (k: string): boolean => {
  try { return localStorage.getItem(k) === '1'; } catch { return true; }
};
const marcarVista = (k: string) => {
  try { localStorage.setItem(k, '1'); } catch { /* sin almacenamiento: no se recuerda */ }
};

/**
 * Ayuda contextual: «¿Cómo se hace?» abre un recorrido breve (4 pasos) del
 * módulo. La primera vez que el usuario entra se muestra solo; se puede omitir
 * y queda recordado por usuario.
 */
export const PageHelp = ({ modulo }: { modulo: ModuloAyuda }) => {
  const { t } = useTranslation();
  const { user } = useAuth();
  const k = clave(user?.sub, modulo);
  const [open, setOpen] = useState(false);
  const [paso, setPaso] = useState(0);

  useEffect(() => {
    if (!yaVista(k)) {
      const id = window.setTimeout(() => setOpen(true), 600);
      return () => window.clearTimeout(id);
    }
    return undefined;
  }, [k]);

  const cerrar = () => { marcarVista(k); setOpen(false); setPaso(0); };

  return (
    <>
      <Link component="button" type="button" variant="body2" underline="hover" onClick={() => setOpen(true)}
        sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5 }}>
        <HelpOutlineIcon sx={{ fontSize: 16 }} />
        {t('help.how_to')}
      </Link>
      <Dialog open={open} onClose={cerrar} maxWidth="xs" fullWidth>
        <DialogTitle>{t(`help.${modulo}.title`)}</DialogTitle>
        <DialogContent>
          <Box sx={{ minHeight: 96 }}>
            <Typography variant="subtitle2" gutterBottom>{t(`help.${modulo}.step${paso + 1}_title`)}</Typography>
            <Typography variant="body2" color="text.secondary">{t(`help.${modulo}.step${paso + 1}`)}</Typography>
          </Box>
          <MobileStepper variant="dots" steps={PASOS} position="static" activeStep={paso} sx={{ bgcolor: 'transparent', px: 0 }}
            backButton={<Button size="small" disabled={paso === 0} onClick={() => setPaso((p) => p - 1)}>{t('help.back')}</Button>}
            nextButton={paso < PASOS - 1
              ? <Button size="small" onClick={() => setPaso((p) => p + 1)}>{t('help.next')}</Button>
              : <Button size="small" variant="contained" onClick={cerrar}>{t('help.done')}</Button>} />
        </DialogContent>
        <DialogActions>
          <Button onClick={cerrar} color="inherit">{t('help.skip')}</Button>
        </DialogActions>
      </Dialog>
    </>
  );
};

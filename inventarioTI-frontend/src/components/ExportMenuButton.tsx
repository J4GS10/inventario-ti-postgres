import { useState } from 'react';
import { Button, CircularProgress, ListItemIcon, ListItemText, ListSubheader, Menu, MenuItem } from '@mui/material';
import FileDownloadIcon from '@mui/icons-material/FileDownload';
import ArrowDropDownIcon from '@mui/icons-material/ArrowDropDown';
import CheckIcon from '@mui/icons-material/Check';
import { useTranslation } from 'react-i18next';
import { normalizeLanguage, type SupportedLanguage } from '../types/language';

const LANGUAGES: { code: SupportedLanguage; label: string }[] = [
  { code: 'es', label: 'Español' },
  { code: 'en', label: 'English' },
  { code: 'it', label: 'Italiano' },
];

interface ExportMenuButtonProps {
  /** Descarga el archivo en el idioma elegido. */
  onExport: (language: SupportedLanguage) => void | Promise<void>;
  label?: string;
  disabled?: boolean;
}

/**
 * Botón estándar de exportación: un solo botón con menú de idioma del archivo
 * (reemplaza el par "selector de idioma + botón" que saturaba los encabezados).
 */
export const ExportMenuButton = ({ onExport, label, disabled }: ExportMenuButtonProps) => {
  const { t, i18n } = useTranslation();
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const [busy, setBusy] = useState(false);
  const current = normalizeLanguage(i18n.resolvedLanguage);

  const run = async (language: SupportedLanguage) => {
    setAnchor(null);
    setBusy(true);
    try {
      await onExport(language);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <Button
        variant="outlined"
        startIcon={busy ? <CircularProgress size={16} /> : <FileDownloadIcon />}
        endIcon={<ArrowDropDownIcon />}
        onClick={(e) => setAnchor(e.currentTarget)}
        disabled={disabled || busy}
        aria-haspopup="menu"
      >
        {label ?? t('common.export')}
      </Button>
      <Menu anchorEl={anchor} open={Boolean(anchor)} onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
        transformOrigin={{ vertical: 'top', horizontal: 'right' }}>
        <ListSubheader sx={{ bgcolor: 'transparent', lineHeight: 2.5, typography: 'overline', fontSize: '0.68rem' }}>
          {t('common.export_language')}
        </ListSubheader>
        {LANGUAGES.map((lang) => (
          <MenuItem key={lang.code} dense onClick={() => void run(lang.code)}>
            <ListItemText>{lang.label}</ListItemText>
            {lang.code === current && (
              <ListItemIcon sx={{ minWidth: 0, ml: 2, color: 'primary.main' }}><CheckIcon fontSize="small" /></ListItemIcon>
            )}
          </MenuItem>
        ))}
      </Menu>
    </>
  );
};

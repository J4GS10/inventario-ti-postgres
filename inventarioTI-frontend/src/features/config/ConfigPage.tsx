import { useCallback, useEffect, useRef, useState } from 'react';
import { useForm } from 'react-hook-form';
import { Box, Typography, TextField, Button, CircularProgress, Card, CardContent, Chip, Stack, Tooltip, InputAdornment, ThemeProvider, alpha } from '@mui/material';
import SaveIcon from '@mui/icons-material/Save';
import BusinessIcon from '@mui/icons-material/Business';
import PaletteIcon from '@mui/icons-material/Palette';
import ImageIcon from '@mui/icons-material/Image';
import DescriptionIcon from '@mui/icons-material/Description';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import StyleIcon from '@mui/icons-material/Style';
import { useTranslation } from 'react-i18next';
import { useConfig } from '../../context/config-context';
import { useSnackbar } from 'notistack';
import { getApiErrorMessage } from '../../api/errors';
import { configService } from './configService';
import type { SystemConfig } from './configService';
import { PageHeader, SectionCard as UiSectionCard } from '../../components/ui';
import { PALETTE_PRESETS, createAppTheme, pickText } from '../../theme/theme';
// Presets de fondo (oscuros, nunca negro puro) para guiar al usuario.
const HEX_RE = /^#([0-9a-fA-F]{3}){1,2}$/;
// Campo de color: input nativo + hex editable + swatch grande.
const ColorField = ({ label, help, value, onChange, error, }: {
    label: string;
    help?: string;
    value: string;
    onChange: (v: string) => void;
    error?: boolean;
}) => (<Box>
        <Typography variant="body2" sx={{ color: 'text.secondary', mb: 0.75 }}>{label}</Typography>
        <Stack direction="row" spacing={1.5} alignItems="center">
            <Box component="input" type="color" value={HEX_RE.test(value || '') ? value : '#000000'} onChange={(e: React.ChangeEvent<HTMLInputElement>) => onChange(e.target.value)} sx={{
        width: 56, height: 44, p: 0, border: '1px solid', borderColor: 'divider',
        borderRadius: 1.5, bgcolor: 'transparent', cursor: 'pointer', flexShrink: 0,
    }}/>
            <TextField size="small" value={value || ''} onChange={(e) => onChange(e.target.value)} error={error} placeholder="#0f172a" InputProps={{
        startAdornment: <InputAdornment position="start" sx={{ color: 'text.secondary' }}>#</InputAdornment>,
    }} sx={{ width: 160 }}/>
        </Stack>
        {help && (<Typography variant="caption" sx={{ color: 'text.secondary', display: 'block', mt: 0.5 }}>{help}</Typography>)}
    </Box>);
// ── Vista previa con el tema real ────────────────────────────────────────────
const ThemePreview = ({ primary, secondary, background, companyName }: {
    primary: string;
    secondary: string;
    background: string;
    companyName: string;
}) => {
    const { t } = useTranslation();
    const valid = (v: string) => HEX_RE.test(v || '');
    const theme = createAppTheme(
        valid(primary) ? primary : undefined,
        valid(secondary) ? secondary : undefined,
        valid(background) ? background : undefined,
    );
    return (<ThemeProvider theme={theme}>
        <Box sx={{ bgcolor: 'background.default', border: '1px solid', borderColor: 'divider', borderRadius: 2, overflow: 'hidden', display: 'flex', minHeight: 210 }}>
            <Box sx={{ width: 150, bgcolor: 'background.paper', borderRight: '1px solid', borderColor: 'divider', p: 1.5, display: { xs: 'none', sm: 'block' } }}>
                <Typography variant="body2" sx={{ fontWeight: 800, color: 'primary.main', mb: 1.5 }} noWrap>
                    {companyName || 'Inventario TI'}
                </Typography>
                {[t('menu.dashboard'), t('menu.assets'), t('menu.movements')].map((label, i) => (<Box key={label} sx={{
                px: 1, py: 0.75, borderRadius: 1, mb: 0.5, typography: 'body2',
                color: i === 1 ? 'primary.main' : 'text.secondary', fontWeight: i === 1 ? 700 : 500,
                bgcolor: i === 1 ? (th) => alpha(th.palette.primary.main, 0.12) : 'transparent',
                whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
            }}>{label}</Box>))}
            </Box>
            <Box sx={{ flex: 1, p: 2, minWidth: 0 }}>
                <Typography variant="subtitle1" sx={{ fontWeight: 700, color: 'text.primary', mb: 1 }}>{t('menu.assets')}</Typography>
                <Card>
                    <CardContent sx={{ p: 1.5, '&:last-child': { pb: 1.5 } }}>
                        <Typography variant="body2" color="text.secondary">{t('config.preview_card')}</Typography>
                        <Stack direction="row" spacing={1} sx={{ mt: 1.5 }} useFlexGap flexWrap="wrap">
                            <Button size="small" variant="contained">{t('common.save')}</Button>
                            <Button size="small" variant="outlined">{t('common.cancel')}</Button>
                            <Chip size="small" color="success" label={t('common.active')}/>
                        </Stack>
                    </CardContent>
                </Card>
            </Box>
        </Box>
    </ThemeProvider>);
};
// ── Selector visual de paletas predefinidas ──────────────────────────────────
type PalettePreset = (typeof PALETTE_PRESETS)[number];
// Los nombres en theme.ts están en español; se traducen por un id estable.
const PRESET_IDS: Record<PalettePreset['name'], string> = {
    'Neutro Cálido': 'neutral_warm',
    'Pizarra': 'slate',
    'Lavanda': 'lavender',
    'Terracota': 'terracotta',
    'Medianoche': 'midnight',
};
const presetId = (p: PalettePreset) => PRESET_IDS[p.name] ?? p.name;
const sameHex = (a?: string, b?: string) => (a || '').trim().toLowerCase() === (b || '').trim().toLowerCase();
// Índice del preset cuyos 3 colores coinciden con los actuales (-1 = personalizado).
const findPresetIndex = (bg?: string, primary?: string, secondary?: string) => PALETTE_PRESETS.findIndex((p) => sameHex(p.bg, bg) && sameHex(p.primary, primary) && sameHex(p.secondary, secondary));
const PaletteSelector = ({ activeIndex, onSelect }: {
    activeIndex: number;
    onSelect: (p: PalettePreset) => void;
}) => {
    const { t } = useTranslation();
    const cardRefs = useRef<(HTMLDivElement | null)[]>([]);
    const selectAt = (i: number) => {
        const n = PALETTE_PRESETS.length;
        const idx = (i + n) % n;
        onSelect(PALETTE_PRESETS[idx]);
        cardRefs.current[idx]?.focus();
    };
    // Patrón WAI-ARIA radiogroup: flechas mueven y seleccionan; Space/Enter seleccionan.
    const onKeyDown = (e: React.KeyboardEvent, i: number) => {
        switch (e.key) {
            case 'ArrowRight':
            case 'ArrowDown': e.preventDefault(); selectAt(i + 1); break;
            case 'ArrowLeft':
            case 'ArrowUp': e.preventDefault(); selectAt(i - 1); break;
            case 'Home': e.preventDefault(); selectAt(0); break;
            case 'End': e.preventDefault(); selectAt(PALETTE_PRESETS.length - 1); break;
            case ' ':
            case 'Enter': e.preventDefault(); selectAt(i); break;
        }
    };
    return (<Box>
            <Box role="radiogroup" aria-label={t('config.palette.group_label')} sx={{
            display: 'grid', gap: 1.5,
            gridTemplateColumns: 'repeat(auto-fill, minmax(170px, 1fr))',
        }}>
                {PALETTE_PRESETS.map((p, i) => {
            const selected = i === activeIndex;
            const tabbable = activeIndex === -1 ? i === 0 : selected;
            const name = t(`config.palette.presets.${presetId(p)}`, { defaultValue: p.name });
            const swatches = [
                { key: 'background', color: p.bg },
                { key: 'primary', color: p.primary },
                { key: 'secondary', color: p.secondary },
            ];
            return (<Box key={p.name} ref={(el: HTMLDivElement | null) => { cardRefs.current[i] = el; }} role="radio" aria-checked={selected} aria-label={name} tabIndex={tabbable ? 0 : -1} onClick={() => selectAt(i)} onKeyDown={(e: React.KeyboardEvent) => onKeyDown(e, i)} sx={{
                    position: 'relative', cursor: 'pointer', borderRadius: 2, p: 1.25,
                    border: '2px solid', borderColor: selected ? 'primary.main' : 'divider',
                    bgcolor: 'background.paper',
                    transition: 'border-color .15s, transform .1s',
                    '&:hover': { borderColor: selected ? 'primary.main' : 'text.secondary' },
                    '&:focus-visible': { outline: '2px solid', outlineColor: 'primary.main', outlineOffset: 2 },
                    '&:active': { transform: 'scale(0.99)' },
                }}>
                            {selected && (<CheckCircleIcon fontSize="small" aria-hidden sx={{
                        position: 'absolute', top: 6, right: 6, zIndex: 1,
                        color: 'primary.main', bgcolor: 'background.paper', borderRadius: '50%',
                    }}/>)}
                            {/* Mini vista previa: barra superior (primario), fondo, botón (secundario) */}
                            <Box aria-hidden sx={{ borderRadius: 1.5, overflow: 'hidden', border: '1px solid', borderColor: 'divider', bgcolor: p.bg }}>
                                <Box sx={{ height: 14, bgcolor: p.primary, display: 'flex', alignItems: 'center', px: 0.75, gap: 0.5 }}>
                                    {[0, 1, 2].map((d) => (<Box key={d} sx={{ width: 4, height: 4, borderRadius: '50%', bgcolor: pickText(p.primary), opacity: 0.7 }}/>))}
                                </Box>
                                <Box sx={{ p: 1, display: 'flex', flexDirection: 'column', gap: 0.75 }}>
                                    <Box sx={{ height: 5, width: '70%', borderRadius: 1, bgcolor: pickText(p.bg), opacity: 0.35 }}/>
                                    <Box sx={{ height: 5, width: '45%', borderRadius: 1, bgcolor: pickText(p.bg), opacity: 0.2 }}/>
                                    <Box sx={{ display: 'flex', gap: 0.5, mt: 0.25 }}>
                                        <Box sx={{ px: 0.75, py: 0.25, borderRadius: 1, fontSize: 10, fontWeight: 700, lineHeight: 1.4, bgcolor: p.secondary, color: pickText(p.secondary) }}>
                                            {t('config.palette.preview_button')}
                                        </Box>
                                        <Box sx={{ px: 0.75, py: 0.25, borderRadius: 1, fontSize: 10, fontWeight: 700, lineHeight: 1.4, border: `1px solid ${p.primary}`, color: pickText(p.bg) }}>
                                            Aa
                                        </Box>
                                    </Box>
                                </Box>
                            </Box>
                            <Typography variant="body2" sx={{ fontWeight: 700, mt: 1, pr: 2.5, color: 'text.primary' }}>
                                {name}
                                {selected && (<Box component="span" sx={{ position: 'absolute', width: 1, height: 1, overflow: 'hidden', clip: 'rect(0 0 0 0)' }}>
                                        {` (${t('config.palette.selected')})`}
                                    </Box>)}
                            </Typography>
                            <Stack direction="row" spacing={0.75} sx={{ mt: 0.75 }}>
                                {swatches.map((sw) => (<Tooltip key={sw.key} title={`${t(`config.palette.${sw.key}`)} ${sw.color}`} arrow>
                                        <Box sx={{ width: 22, height: 22, borderRadius: '50%', bgcolor: sw.color, border: '1px solid', borderColor: 'divider' }}/>
                                    </Tooltip>))}
                            </Stack>
                        </Box>);
        })}
            </Box>
            {activeIndex === -1 && (<Typography variant="caption" sx={{ display: 'block', mt: 1.5, color: 'text.secondary' }}>
                    {t('config.palette.custom')}
                </Typography>)}
        </Box>);
};
export const ConfigPage = () => {
    const { t } = useTranslation();
    const { register, handleSubmit, reset, watch, setValue, formState: { errors } } = useForm<SystemConfig>();
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const { enqueueSnackbar } = useSnackbar();
    const { apply: applyConfig } = useConfig();
    const colorPrimario = watch('SYS_Color_Primario');
    const colorSecundario = watch('SYS_Color_Secundario');
    const colorFondo = watch('SYS_Color_Fondo');
    const logoUrl = watch('SYS_Logo_URL');
    const nombreEmpresa = watch('SYS_Nombre_Empresa');
    const activePresetIndex = findPresetIndex(colorFondo, colorPrimario, colorSecundario);
    // Aplica un preset por la misma vía que los campos de color (setValue → submit → config API → recarga).
    const applyPreset = useCallback((p: PalettePreset) => {
        const opts = { shouldValidate: true, shouldDirty: true };
        setValue('SYS_Color_Fondo', p.bg, opts);
        setValue('SYS_Color_Primario', p.primary, opts);
        setValue('SYS_Color_Secundario', p.secondary, opts);
    }, [setValue]);
    const loadConfig = useCallback(async () => {
        try {
            const data = await configService.getConfig();
            reset({ ...data, SYS_Color_Fondo: data.SYS_Color_Fondo || '#0f172a' });
        }
        catch (error) {
            console.error('Error loading config:', error);
            enqueueSnackbar(t('common.error_loading'), { variant: 'error' });
        }
        finally {
            setLoading(false);
        }
    }, [enqueueSnackbar, reset, t]);
    useEffect(() => { loadConfig(); }, [loadConfig]);
    const onSubmit = async (data: SystemConfig) => {
        setSaving(true);
        try {
            const saved = await configService.updateConfig(data);
            // El tema, la marca y la paleta se aplican al instante (sin recargar).
            applyConfig(saved ?? data);
            enqueueSnackbar(t('config.success_save'), { variant: 'success' });
        }
        catch (error: unknown) {
            console.error('Error saving config:', error);
            enqueueSnackbar(getApiErrorMessage(error, t('common.error_save')), { variant: 'error' });
        }
        finally {
            setSaving(false);
        }
    };
    // Registrar campos de color para validación (los editamos vía setValue).
    register('SYS_Color_Primario', { required: true, pattern: HEX_RE });
    register('SYS_Color_Secundario', { required: true, pattern: HEX_RE });
    register('SYS_Color_Fondo', { required: true, pattern: HEX_RE });
    if (loading) {
        return (<Box display="flex" justifyContent="center" alignItems="center" minHeight="400px">
                <CircularProgress />
            </Box>);
    }
    return (<Box sx={{ maxWidth: 1100, mx: 'auto' }}>
            <PageHeader
                title={t('config.title')}
                subtitle={t('config.subtitle')}
            />

            <form onSubmit={handleSubmit(onSubmit)}>
                <Stack spacing={3}>
                    {/* Identidad: nombre + logo juntos */}
                    <UiSectionCard icon={<BusinessIcon />} title={t('config.identity')}>
                        <Stack direction={{ xs: 'column', md: 'row' }} spacing={3}>
                            <Stack flex={1} spacing={2}>
                                <TextField fullWidth label={t('config.company_name')} placeholder="TechCorp S.A." {...register('SYS_Nombre_Empresa', { required: true, minLength: 2 })} error={!!errors.SYS_Nombre_Empresa} helperText={errors.SYS_Nombre_Empresa ? t('common.required') : ''}/>
                                <TextField fullWidth label={t('config.logo_url')} placeholder={t('config.logo_url_placeholder')} {...register('SYS_Logo_URL')} InputProps={{
            startAdornment: <InputAdornment position="start"><ImageIcon fontSize="small" sx={{ color: 'text.secondary' }}/></InputAdornment>,
        }} helperText={t('config.logo_help')}/>
                            </Stack>
                            <Box sx={{
            flex: 1, minHeight: 120, display: 'flex', alignItems: 'center', justifyContent: 'center',
            p: 2, bgcolor: 'action.hover', borderRadius: 2,
            border: '1px dashed rgba(255,255,255,0.15)',
        }}>
                                {logoUrl ? (<img src={logoUrl} alt={t('config.logo_alt')} style={{ maxHeight: '90px', maxWidth: '100%', objectFit: 'contain' }} onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; }}/>) : (<Typography variant="caption" sx={{ color: 'text.secondary' }}>
                                        {t('config.logo_preview_empty')}
                                    </Typography>)}
                            </Box>
                        </Stack>
                    </UiSectionCard>

                    {/* Paletas predefinidas: escriben los mismos campos de color del formulario */}
                    <UiSectionCard icon={<StyleIcon fontSize="small"/>} title={t('config.palette.title')} subtitle={t('config.palette.subtitle')}>
                        <PaletteSelector activeIndex={activePresetIndex} onSelect={applyPreset}/>
                        <Box aria-live="polite">
                        </Box>
                    </UiSectionCard>

                    {/* Apariencia: fondo + colores + vista previa */}
                    <UiSectionCard icon={<PaletteIcon />} title={t('config.appearance')}>
                        <Stack direction={{ xs: 'column', md: 'row' }} spacing={4}>
                            {/* Controles */}
                            <Stack flex={1} spacing={3}>
                                <Box>
                                    <ColorField label={t('config.background_color')} help={t('config.background_help')} value={colorFondo} onChange={(v) => setValue('SYS_Color_Fondo', v, { shouldValidate: true })} error={!!errors.SYS_Color_Fondo}/>
                                </Box>

                                <ColorField label={t('config.primary_color')} help={t('config.primary_help')} value={colorPrimario} onChange={(v) => setValue('SYS_Color_Primario', v, { shouldValidate: true })} error={!!errors.SYS_Color_Primario}/>

                                <ColorField label={t('config.secondary_color')} help={t('config.secondary_help')} value={colorSecundario} onChange={(v) => setValue('SYS_Color_Secundario', v, { shouldValidate: true })} error={!!errors.SYS_Color_Secundario}/>
                            </Stack>

                            {/* Vista previa real: componentes del sistema con el tema que
                                generan los colores del formulario (lo que se ve es lo que queda). */}
                            <Box flex={1}>
                                <Typography variant="body2" sx={{ color: 'text.secondary', mb: 0.75 }}>
                                    {t('config.preview')}
                                </Typography>
                                <ThemePreview
                                    primary={colorPrimario}
                                    secondary={colorSecundario}
                                    background={colorFondo}
                                    companyName={nombreEmpresa}
                                />
                            </Box>
                        </Stack>
                    </UiSectionCard>

                    {/* Datos de las actas (Word/PDF) */}
                    <UiSectionCard icon={<DescriptionIcon />} title={t('config.acta_header')}>
                        <Stack direction={{ xs: 'column', md: 'row' }} spacing={2}>
                            <TextField fullWidth label={t('config.form_code')} placeholder="F.IT.GUA.04.01" {...register('SYS_Codigo_Formulario')} helperText={t('config.form_code_help')}/>
                            <TextField fullWidth label={t('config.city')} placeholder="Guatemala" {...register('SYS_Ciudad')} helperText={t('config.city_help')}/>
                        </Stack>
                    </UiSectionCard>
                </Stack>

                {/* Botón Guardar */}
                <Box display="flex" justifyContent="flex-end" mt={4}>
                    <Button type="submit" variant="contained" color="primary" size="large" startIcon={saving ? <CircularProgress size={20} color="inherit"/> : <SaveIcon />} disabled={saving} sx={{ fontWeight: 'bold', px: 4 }}>
                        {saving ? t('common.saving') : t('common.save_changes')}
                    </Button>
                </Box>
            </form>
        </Box>);
};

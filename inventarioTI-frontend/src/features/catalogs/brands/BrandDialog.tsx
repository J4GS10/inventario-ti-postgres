import { useEffect, useState } from 'react';
import { useForm } from 'react-hook-form';
import { Dialog, DialogTitle, DialogContent, DialogActions, Button, TextField, CircularProgress } from '@mui/material';
import { useTranslation } from 'react-i18next';
import { SectionCard } from '../../../components/ui';
import { useSnackbar } from 'notistack';
import { getApiErrorDetail } from '../../../api/errors';
import { catalogService } from '../catalogService';
import { Marca, MarcaCreate } from '../../../types/catalogs';
interface Props {
    open: boolean;
    onClose: (refresh?: boolean) => void;
    brandToEdit?: Marca;
}
export const BrandDialog = ({ open, onClose, brandToEdit }: Props) => {
    const { t } = useTranslation();
    const { register, handleSubmit, reset, formState: { errors } } = useForm<MarcaCreate>();
    const [loading, setLoading] = useState(false);
    const { enqueueSnackbar } = useSnackbar();
    // Limpiar formulario al abrir
    useEffect(() => {
        if (open) {
            if (brandToEdit) {
                reset({ MAR_Nombre: brandToEdit.MAR_Nombre });
            }
            else {
                reset({ MAR_Nombre: '' });
            }
        }
    }, [open, brandToEdit, reset]);
    const onSubmit = async (data: MarcaCreate) => {
        setLoading(true);
        try {
            if (brandToEdit) {
                await catalogService.updateMarca(brandToEdit.MAR_Marca, data);
                enqueueSnackbar(t('common.success_update'), { variant: 'success' });
            }
            else {
                await catalogService.createMarca(data);
                enqueueSnackbar(t('catalogs.brands.success_create'), { variant: 'success' });
            }
            onClose(true); // Refrescar tabla
        }
        catch (error: unknown) {
            console.error(error);
            const msg = getApiErrorDetail(error) === 'BRAND_ALREADY_EXISTS'
                ? t('catalogs.brands.error_exists')
                : t('common.error_generic');
            enqueueSnackbar(msg, { variant: 'error' });
        }
        finally {
            setLoading(false);
        }
    };
    return (<Dialog open={open} onClose={() => onClose()} maxWidth="xs" fullWidth>
      <DialogTitle>{brandToEdit ? t('common.edit') : t('catalogs.brands.new_title')}</DialogTitle>
      <form onSubmit={handleSubmit(onSubmit)}>
        <DialogContent>
          <SectionCard title={t('catalogs.sections.general')}>
          <TextField autoFocus fullWidth label={t('catalogs.brands.field_name')} margin="dense" {...register('MAR_Nombre', { required: true, minLength: 2 })} error={!!errors.MAR_Nombre} helperText={errors.MAR_Nombre ? t('common.required') : ''}/>
          </SectionCard>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => onClose()} color="inherit">
            {t('common.cancel')}
          </Button>
          <Button type="submit" variant="contained" disabled={loading}>
            {loading ? <CircularProgress size={24}/> : t('common.save')}
          </Button>
        </DialogActions>
      </form>
    </Dialog>);
};

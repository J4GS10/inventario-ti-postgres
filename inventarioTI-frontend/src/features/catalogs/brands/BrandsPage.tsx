import { useEffect, useState } from 'react';
import { Box, TableCell, TableRow, Button, IconButton, Tooltip } from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import EditIcon from '@mui/icons-material/Edit';
import DeleteIcon from '@mui/icons-material/Delete';
import { useTranslation } from 'react-i18next';
import { useSnackbar } from 'notistack';
import { SearchInput } from '../../../components/SearchInput';
import { PageHeader, DataTableShell } from '../../../components/ui';
import { CollapsibleToolbar } from '../../../components/CollapsibleToolbar';
import { useConfirm } from '../../../components/confirm-context';
import { catalogService } from '../catalogService';
import { Marca } from '../../../types/catalogs';
import { BrandDialog } from './BrandDialog';
export const BrandsPage = () => {
    const { t } = useTranslation();
    const confirm = useConfirm();
    const { enqueueSnackbar } = useSnackbar();
    const [marcas, setMarcas] = useState<Marca[]>([]);
    const [loading, setLoading] = useState(true);
    const [searchTerm, setSearchTerm] = useState('');
    const [isDialogOpen, setIsDialogOpen] = useState(false);
    const [brandToEdit, setBrandToEdit] = useState<Marca | undefined>(undefined);
    const loadData = async () => {
        try {
            const data = await catalogService.getMarcas();
            setMarcas(data);
        }
        catch (error) {
            console.error("Error cargando marcas:", error);
        }
        finally {
            setLoading(false);
        }
    };
    useEffect(() => {
        loadData();
    }, []);
    const handleEdit = (brand: Marca) => {
        setBrandToEdit(brand);
        setIsDialogOpen(true);
    };
    const handleDelete = async (id: number) => {
        if (!(await confirm({ message: t('common.confirm_delete'), destructive: true, confirmText: t('common.delete') })))
            return;
        try {
            await catalogService.deleteMarca(id);
            loadData();
        }
        catch (error) {
            console.error(error);
            enqueueSnackbar(t('common.error_delete_dependencies'), { variant: 'error' });
        }
    };
    const handleCloseDialog = (refresh?: boolean) => {
        setIsDialogOpen(false);
        setBrandToEdit(undefined);
        if (refresh)
            loadData();
    };
    const filteredMarcas = marcas.filter(m => m.MAR_Nombre.toLowerCase().includes(searchTerm.toLowerCase()));
    return (<Box>
      <PageHeader
        title={t('catalogs.brands.title')}
        badge={filteredMarcas.length}
        actions={
          <Button variant="contained" startIcon={<AddIcon />} onClick={() => { setBrandToEdit(undefined); setIsDialogOpen(true); }}>
            {t('catalogs.brands.new_btn')}
          </Button>
        }
      />

      <CollapsibleToolbar storageKey="brands" search={<SearchInput value={searchTerm} onChange={setSearchTerm}/>}/>

      <BrandDialog open={isDialogOpen} onClose={handleCloseDialog} brandToEdit={brandToEdit}/>

      <DataTableShell
        columns={[
          { key: 'id', label: 'ID' },
          { key: 'name', label: t('catalogs.brands.table_name') },
          { key: 'actions', label: t('common.actions'), align: 'right' },
        ]}
        rows={filteredMarcas}
        loading={loading}
        emptyTitle={t('common.no_records')}
        renderRow={(row) => (<TableRow key={row.MAR_Marca} sx={{ '&:hover': { bgcolor: 'action.hover' } }}>
            <TableCell sx={{ color: 'text.secondary' }}>{row.MAR_Marca}</TableCell>
            <TableCell sx={{ color: 'text.primary', fontWeight: 'bold' }}>{row.MAR_Nombre}</TableCell>
            <TableCell align="right">
              <Tooltip title={t('common.edit')}>
                <IconButton size="small" onClick={() => handleEdit(row)} sx={{ color: 'primary.main' }}>
                  <EditIcon fontSize="small"/>
                </IconButton>
              </Tooltip>
              <Tooltip title={t('common.delete')}>
                <IconButton size="small" onClick={() => handleDelete(row.MAR_Marca)} sx={{ color: 'error.main' }}>
                  <DeleteIcon fontSize="small"/>
                </IconButton>
              </Tooltip>
            </TableCell>
          </TableRow>)}
      />
    </Box>);
};

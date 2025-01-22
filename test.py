#!/home/skala/miniconda3/bin/python

import xarray as xr

# Replace 'your_file.nc' with the actual path to your NetCDF file
ds = xr.open_dataset('/data/climate-analytics-lab-shared/ForceSMIP/Training/Amon/tas/CESM2/tas_mon_CESM2_historical_ssp370_r1011.001i1p1f1.188001-202212.nc')
# Print information about the dataset
print(ds)

temperature = ds['tas'] ;
latitude = ds['lat'] ;
longitude = ds['lon']

# Perform operations on the data
# Example: Calculate the mean temperature

print(ds.head(2))

mean_temperature = temperature.mean()
print(mean_temperature)

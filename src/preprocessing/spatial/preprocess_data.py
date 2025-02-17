import os
import sys
import numpy as np
import torch
import xarray as xr
from .utils import calc_spatial_integral
from .constants import GtC_to_GtCO2, Gt_to_Mt, kg_to_Mt, yr_to_s

base_dir = os.path.join(os.getcwd(), '../..')
sys.path.append(base_dir)

from src.fair.ancil import get_gas_params, get_thermal_params
from src.structures import Scenario


def get_fair_params():
    gas_params_df = get_gas_params()
    gas_kwargs = {k: np.asarray(list(v.values()))
                  for k, v in gas_params_df.T.to_dict().items()}
    thermal_params_df = get_thermal_params()
    d = thermal_params_df.T.d.values
    q = thermal_params_df.T.q.values
    base_kwargs = {'d': d,
                   'q': q,
                   **gas_kwargs}
    return base_kwargs


def load_emissions_dataset(filepath):
    inputs = xr.open_dataset(filepath).compute()
    inputs.CO2.data = inputs.CO2.data / GtC_to_GtCO2
    inputs.CO2.attrs['units'] = 'GtC/yr'
    inputs.CH4.data = inputs.CH4.data * Gt_to_Mt
    inputs.CH4.attrs['units'] = 'MtCH4/yr'
    inputs.SO2.data = inputs.SO2.data * kg_to_Mt * yr_to_s
    inputs.SO2.attrs['units'] = 'MtSO2/yr m-2'
    inputs.BC.data = inputs.BC.data * kg_to_Mt * yr_to_s
    inputs.BC.attrs['units'] = 'MtBC/yr m-2'
    return inputs


def load_response_dataset(filepath):
    outputs = xr.open_dataset(filepath).compute()
    return outputs


def make_input_array(xr_input, xr_output, dep_var_name):
    latitude = xr_input.latitude
    longitude = xr_input.longitude
    try:
        xr_input['CO2'] = xr_input.CO2.expand_dims(latitude=latitude, longitude=longitude).transpose('time', 'latitude', 'longitude')
        xr_input['CH4'] = xr_input.CH4.expand_dims(latitude=latitude, longitude=longitude).transpose('time', 'latitude', 'longitude')
        xr_input[dep_var_name] = xr_output[dep_var_name].mean(['member'])
    except ValueError:
        pass
    return xr_input


def extract_arrays(xr_input, dep_var_name):
    # Extract time steps, lat and lon arrays
    time = xr_input.time.values
    lat = xr_input.latitude.values
    lon = xr_input.longitude.values

    # Extract cumulative emissions
    cum_CO2_emissions = xr_input.CO2.values
    # cum_emissions = cum_CO2_emissions[:, :10, :10]
    cum_emissions = cum_CO2_emissions

    # Compute spatial emissions
    CO2_emissions = np.append(np.diff(cum_CO2_emissions, axis=0)[0][None, ...],
                              np.diff(cum_CO2_emissions, axis=0), axis=0)
    CH4_emissions = xr_input.CH4.values
    SO2_emissions = xr_input.SO2.values
    BC_emissions = xr_input.BC.values
    emissions = np.stack([CO2_emissions, CH4_emissions, SO2_emissions, BC_emissions])

    CO2_glob_emissions = CO2_emissions.mean(axis=(1, 2))
    CH4_glob_emissions = CH4_emissions.mean(axis=(1, 2))
    SO2_glob_emissions = calc_spatial_integral(xr_input.SO2).data
    BC_glob_emissions = calc_spatial_integral(xr_input.BC).data
    glob_emissions = np.stack([CO2_glob_emissions, CH4_glob_emissions, SO2_glob_emissions, BC_glob_emissions])

    # Compute spatial temperature anomaly
    #tas = xr_input.tas.data
    dep_var = xr_input[dep_var_name].data

    return time, lat, lon, cum_emissions, emissions, glob_emissions, dep_var


def make_scenario(key, inputs, outputs, hist_scenario=None, dep_var_name=None):
    xr_input = make_input_array(inputs[key], outputs[key], dep_var_name)
    time, lat, lon, _, emission, glob_emissions, dep_var = extract_arrays(xr_input, dep_var_name)
    scenario = Scenario(name=key,
                        timesteps=torch.from_numpy(time).float(),
                        lat=torch.from_numpy(lat).float(),
                        lon=torch.from_numpy(lon).float(),
                        emissions=torch.from_numpy(emission).float().permute(1, 2, 3, 0),
                        dep_var=torch.from_numpy(dep_var).float(),
                        hist_scenario=hist_scenario)
    scenario.glob_emissions = torch.from_numpy(glob_emissions).float().T

    return scenario

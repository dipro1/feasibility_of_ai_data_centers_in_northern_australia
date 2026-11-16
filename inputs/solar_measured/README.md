# Ground-measured solar data (optional)

Drop a file named `<site_id>.csv` here (for example `rockhampton.csv`) to
replace NASA POWER irradiance for that site. Hours not covered keep NASA
values, and the report states which source was used.

Required columns:

| column | meaning |
|---|---|
| `timestamp` | hourly, local standard time (AEST for Queensland, AWST for WA), hour-beginning |
| `ghi` | global horizontal irradiance, W/m2 (hourly mean) |
| `dni` | direct normal irradiance, W/m2 |
| `dhi` | diffuse horizontal irradiance, W/m2 |

Suitable sources: Bureau of Meteorology gridded solar exposure / one-minute
solar station data, Solcast historical time series, or a nearby solar farm's
on-site pyranometer data. NASA POWER is a satellite/reanalysis product and
typically over-states all-sky DNI in tropical climates, which is why
`run_solar_sensitivity.py` reports results at reduced irradiance as well.

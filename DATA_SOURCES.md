# Data sources and assumptions

Values marked **VERIFY** were taken from secondary reporting or are
placeholders. Confirm them against the primary source before quoting.

## Queensland network (Powerlink)

Powerlink Queensland, *2025 Transmission Annual Planning Report, Appendix I:
Indicative short circuit currents*, Table I.2 (Central Queensland), pp. 194-195.
https://www.powerlink.com.au/sites/default/files/2025-10/TAPR%202025%20Appendix%20I%20-%20Indicative%20short%20circuit%20currents.pdf

| Substation | Minimum system-normal 3ph (kA) | Minimum N-1 3ph (kA) | Used for |
|---|---:|---:|---|
| Bouldercombe 275 kV | 10.1 | 8.6 | Rockhampton; Livingstone (nearest 275 kV node, connection point not confirmed) |
| Calliope River 275 kV | 10.4 | 8.7 | Gladstone |
| Larcom Creek 275 kV | 9.2 | 3.3 | Gladstone alternative (Gladstone State Development Area) |

Minimum fault levels are the right (conservative) basis for voltage-step and
voltage-drop screening. They are converted to a Thevenin source using
`Ssc = sqrt(3) V I` with an assumed X/R of 10. Public thermal headroom and
background flows are not available, so the 275 kV results are voltage-only.

Powerlink's Gladstone Project (future Bouldercombe-Gladstone West 275 kV
double circuit, about 2,091 MVA, August 2030 target) is not in the model.
https://engage.powerlink.com.au/pti-gladstone-project

## Western Australia network (Horizon Power, NWIS)

Horizon Power, *Pilbara Network Revised System Description*, 25 October 2024.
https://pilbara.horizonpower.com.au/globalassets/pn-pilbara-network/pn-documents/1-revised-system-description/1-revised-system-description-compressed.pdf

- KRT-PCK 4.3 km, KRT-BUL 4.1 km, BUL-PCK 3.1 km; 132 kV; 157 MVA (p. 7 single-line diagram).
- KRT-BUL-PCK ring N-1 utilisation 45% (Table 1, p. 11).
- Port Hedland 66 kV elements: HDT-SWC, WFD-SWC, WFD-MNM and AST-MDR at 100%; HDT-WFD 75%; HDT T3 105% (Table 1).
- Maximum 3ph fault levels (App. A, p. 14): PCK 22 kV 6.9 kA, BUL 22 kV 6.9 kA, WFD 22 kV 7.2 kA.
- Spare transformer capacity, 2028 forecast (App. B, p. 15): PCK 10-20 MVA, BUL 20-30 MVA, WFD 10-20 MVA.

Horizon publishes *maximum* fault levels only, so the 22 kV voltage screens
are optimistic. Line impedance (R1 = 0.08, X1 = 0.38 ohm/km), capacitance
(9 nF/km), source strength (750-5,000 MVA) and background PF (0.95) are
assumptions, not Horizon Power data. The NWIS is an isolated system, so
frequency and generation-adequacy limits are not represented.

## Weather and solar

NASA POWER hourly point API, community RE, local solar time, 2020-2024,
parameters T2M, T2MWET, RH2M, WS10M, ALLSKY_SFC_SW_DWN/DNI/DIFF.
https://power.larc.nasa.gov/docs/services/api/temporal/hourly/

Site coordinates are in `inputs/candidate_sites.csv`. NASA POWER is a
gridded reanalysis/satellite product (roughly 0.5 degree), so nearby sites
such as Rockhampton and Livingstone are not strongly differentiated.

Solar output uses pvlib 0.13 (Holmgren et al., JOSS 2018): single-axis
tracking with backtracking (GCR 0.35, ±60°), Hay-Davies transposition,
physical IAM, Faiman cell temperature, PVWatts DC model (γ = -0.35%/°C),
DC/AC 1.3, 14% system losses and 98% inverter efficiency, all in
`study_config.json`. With raw NASA POWER irradiance the model gives about 30%
(Queensland) and 34% (Pilbara) AC capacity factor. Validation against three
operating Central Queensland farms (Aldoga, Moura, Lilyvale; AEMO SCADA,
Nov 2025 - Aug 2026, curtailment and outage hours removed) found metered
output averages 0.89 of the model (0.78-1.00), so `solar.irradiance_scale` is
set to 0.9. Calibrated capacity factors are about 27% (Queensland) and 31%
(Pilbara). A bankable study should still use BoM or Solcast data.

## Energy prices and demand

AEMO aggregated price and demand data, QLD1, 5-minute, 2020-2024, aggregated
to hourly means (NEM time = AEST). https://aemo.com.au/energy-systems/electricity/national-electricity-market-nem/data-nem/aggregated-data

The NWIS has no public spot market. The flat **A$120/MWh is a placeholder
(VERIFY)**; replace it with a Horizon Power / NWIS contract estimate.

## Costs (verified against the primary source)

CSIRO GenCost 2025-26 Final Report (July 2026), 2025 values, Current policies
scenario: https://www.csiro.au/-/media/Energy/GenCost-2025-26-Final/GenCost_2025-26_Final_Report_20260715.pdf

- Large-scale solar PV: **A$1,621/kW** (Apx Table B.1; AC basis).
- Battery total cost: A$778, 525, 385, 308 per kWh for 1, 2, 4, 8 h (Apx
  Tables B.4-B.5, usable-capacity basis). Fitted as **A$245/kWh + A$560/kW**,
  within about 3% of all four durations.

These replace the earlier trade-press figures for GenCost 2024-25 (solar
A$1,344/kW). PV O&M A$17/kW/yr, battery O&M 2.5% of capex per year, a 7% real
discount rate and 30/20-year lives remain assumptions.

## Hourly grid emissions and CFE

AEMO Dispatch SCADA archive (5-minute output of every scheduled and
semi-scheduled unit), most recent 365 days:
https://nemweb.com.au/Reports/ARCHIVE/Dispatch_SCADA/
combined with AEMO CDEII per-unit CO2-e factors (NGA / ISP sourced):
https://nemweb.com.au/Reports/Current/CDEII/CO2EII_AVAILABLE_GENERATORS.CSV

Queensland generation-based intensity is averaged into a month x hour x
day-type profile and applied to simulated hours (current grid mix).
Interconnector flows and rooftop PV are not included, and batteries are
excluded from generation. Location-based scope 2 reporting still uses the
annual NGA factor; both are reported.

## Solar validation

Metered output of Aldoga (ALDGASF1), Moura (MOUSF1) and Lilyvale (LILYSF1)
solar farms from the same AEMO SCADA data. Farm coordinates are approximate
and single-axis tracking is assumed (**VERIFY**). NASA POWER for the recent
months comes from its near-real-time products.

## Climate, backup and cost-uncertainty assumptions (VERIFY)

- Warming of +1/+2/+3 C is a uniform delta on 2020-2024 weather; check against
  CSIRO/BoM *Climate Change in Australia* regional projections.
- Backup: 3 MW diesel units, A$1,100/kW, 0.26 L/kWh, 2.71 kg CO2-e/L (NGA
  diesel energy content and factor), 1% fail-to-start, outage statistics swept.
- Cost uncertainty ranges (PV capex x0.8-1.25, BESS x0.7-1.3, discount rate
  5-10%, price level x0.8-1.4, tariff x0.8-1.2) are assumed brackets, not
  fitted distributions.

## Network charges

Powerlink, *Transmission Shared Network Prices 2026/27* (GST inclusive):
https://www.powerlink.com.au/sites/default/files/2026-03/2026-27%20Schedule%20of%20Transmission%20Shared%20Network%20Prices.pdf

| Node (132 kV, used as proxy for 275 kV) | Locational price ($/kW/month) |
|---|---:|
| Bouldercombe (Rockhampton, Livingstone) | 0.8613 |
| Gladstone (Calliope River) | 0.6314 |
| Larcom Creek | 1.0208 |

Non-locational energy-based price 0.5412 c/kWh; common-service energy-based
price 0.9251 c/kWh. The model applies the locational price to each month's
peak import (a simplification of Powerlink's billing basis) and removes GST.
Regulated connection charges and the NWIS (no published equivalent) are
excluded. PPA price A$75/MWh is a placeholder (**VERIFY**).

## Cooling water

Physics-based: water = heat rejected evaporatively / latent heat (2.43 MJ/kg)
x (1 + 1/(cycles - 1)) + drift. Evaporative share rises linearly from 0 at
18 C to 1 at 26 C dry-bulb (design assumptions). This replaces the earlier
flat WUE of 1.8 L/kWh.

## Flicker and oscillation (VERIFY)

The Pst = 1 curve for rectangular voltage changes uses the IEC 61000-4-15
Table 5 calibration points (1, 2, 7, 39, 110, 1620 changes/min ->
2.724, 2.211, 1.459, 0.906, 0.725, 0.402 %) as widely reproduced; the values
could not be confirmed against the standard text; HV-EHV planning levels
Pst 0.8 / Plt 0.6 are indicative values from IEC 61000-3-7 / AS/NZS 61000.3.7
(referenced by NER S5.1.5).

Swing sizes, periods and risky frequency bands follow Choukse et al. (2025),
*Power Stabilization for AI Training Datacenters*, arXiv:2508.14318
(https://arxiv.org/html/2508.14318v1): swings once per second to once per
tens of seconds, energy at 0.2-3 Hz; GPUs above 50% of server power;
GB200 power smoothing with a floor up to 90% of TDP; risky bands about
0.16-2.5 Hz and 7-100 Hz.

## Reduced multi-bus model

- Fault levels for the 12 Central Queensland 275 kV buses: TAPR 2025 App. I.
- **Topology and route lengths: OpenStreetMap** (Overpass API extract saved as
  `inputs/cq_network/osm_cq_275kV.json`; (c) OpenStreetMap contributors,
  ODbL), derived by `build_cq_topology.py` (17 direct connections; one
  spurious Calliope River-Calvale path through Wurdong removed).
- Circuit counts: Powerlink TAPR 2025 Appendix G grid-section definitions and
  Chapter 6 where published; others marked "assumed" in `branches.csv`.
- Impedance 0.03 + j0.32 ohm/km per circuit is typical (VERIFY); ratings are
  not published. Geoscience Australia's transmission dataset could not be
  downloaded (service blocks automated access), so OSM was used.

## Gladstone grid-section thermal proxy

Powerlink TAPR 2025 Chapter 6, Section 6.6.4 and Figure 6.17:
https://www.powerlink.com.au/sites/default/files/2025-10/TAPR%202025%20Chapter%206%20-%20Network%20capability%20and%20performance.pdf

The Gladstone grid section is thermally limited (Bouldercombe-Raglan, Larcom
Creek-Calliope River, Bouldercombe-Calliope River, Calvale-Wurdong and
Calliope River-Wurdong 275 kV circuits) and bound for 54 hours in 2024/25. The
2024/25 transfer duration curve was **digitised by eye** from Figure 6.17
(approximate, about +/-30 MW). It is a corridor-level indication, not a
thermal study.

## Emissions (verified against the primary source)

DCCEEW, *National Greenhouse Accounts Factors 2025*, Table 1 (location-based
scope 2): **Queensland 0.67**, **WA North Western Interconnected System 0.56**
kg CO2-e/kWh.
https://www.dcceew.gov.au/sites/default/files/documents/national-greenhouse-account-factors-2025.pdf

## Planning criteria (VERIFY)

- Base load PF by connection voltage: 0.98 (>250 kV), 0.96 (50-250 kV), 0.90
  (<50 kV), adopted from NER S5.3.5. Check the current version of the Rules.
- 0.95 pu steady-state minimum is a screening criterion.
- Voltage-step limit: cite **NER S5.1.5** (voltage fluctuations) and **NER
  S5.3.7** (fluctuating loads). The 3% figure is acceptable for an initial
  check (supervisor advice) but is an **assumption until Powerlink confirms
  the limit for this connection**.
- Thermal headroom **cannot be estimated from fault level**. It requires line
  and transformer ratings, existing flows and N-1 load-flow results, which
  only the network owner (Powerlink / Horizon Power) can provide.
- WUE 1.8 L/kWh of IT energy is a widely cited industry average, not a
  design value.

## Dynamic screening

- Island frequency criteria: NEM Frequency Operating Standard, island system,
  generation/load/network event: containment 49.0-51.0 Hz, stabilisation
  49.5-50.5 Hz within 5 minutes (AEMC FOS Table A.1.2):
  https://www.aemc.gov.au/sites/default/files/2020-01/Frequency%20operating%20standard%20-%20effective%201%20January%202020%20-%20TYPO%20corrected%2019DEC2019.PDF
- Frequency criteria: NEM mainland Frequency Operating Standard,
  generation/load event: containment 49.5-50.5 Hz, stabilisation 49.85-50.15 Hz
  within 5 minutes (AEMC Reliability Panel, effective 9 October 2023).
  https://www.aemc.gov.au/sites/default/files/2023-04/FOS%20-%20MARKUP.pdf
  The same bands are applied to the NWIS only for comparison (**VERIFY**
  against the Pilbara Networks Rules).
- Maximum fault levels (maximum operating condition): Powerlink TAPR 2025
  Appendix I, 2025/26 maximum 3ph: Bouldercombe 275 kV 22.0 kA, Calliope River
  275 kV 22.1 kA, Larcom Creek 275 kV 16.7 kA.
- **Verified:** Queensland minimum inertia threshold 12,000 MWs and secure
  level 13,700 MWs, and the island frequency bands (AEMO 2024 Inertia Report,
  Tables and Table 16); largest credible NEM contingency 750 MW (Kogan Creek,
  710/750 MW, TAPR 2025 Table 6.1).
- **Still assumed (VERIFY):** mainland and NWIS inertia, primary frequency
  response and demand; the NWIS 75 MW contingency; the 1 Hz/s RoCoF limit; the
  20 s voltage-control time constant. An older Horizon Power release gives
  NWIS maximum demand of 122 MW (about 2012); current values are higher.

## Hourly power flow (run_hourly_power_flow.py)

- Unit output: AEMO Dispatch SCADA, most recent 365 days (Nov 2025 - Nov 2026),
  aggregated to the multi-bus buses in `inputs/grid/cq_bus_hourly.csv`.
  Unit-to-bus mapping (`hourly_flow.unit_buses`) is by location: Stanwell units
  and BESS -> Stanwell; Gladstone PS -> Gladstone PS; Callide B/C and Moura SF ->
  Calvale; Aldoga SF -> Larcom Creek; Lilyvale, Clermont, Emerald, Middlemount and
  Rugby Run SF -> Lilyvale; Clarke Creek WF and Broadsound -> Broadsound;
  Bouldercombe BESS -> Bouldercombe. **VERIFY** connection points with Powerlink.
- Demand: AEMO QLD1 operational demand (`inputs/prices/QLD1_recent_hourly.csv`).
- Zone loads: the RMS study's indicative loads, 70% fixed (industrial) and 30%
  scaled with Queensland demand; Gladstone-zone and Nebo loads multiplied by
  factors fitted to the published section curves.
- Validation targets: Gladstone (TAPR 2025 Fig 6.17) and CQ-SQ (TAPR 2025 Fig
  6.19, digitised, approximate) 2024/25 transfer duration curves. Note the
  modelled year (Nov 2025 - Nov 2026) differs from the published one.
- Line ratings: not published. **Assumed 900 MVA per 275 kV circuit (VERIFY)**;
  only the change caused by the data centre is independent of this assumption.

## RMS sensitivity and fault ride-through (run_rms_sensitivity.py)

- Inertia 12,000 MWs (AEMO minimum threshold) and 13,000 MWs; delivered governor
  response 20-100% of the modelled response (droop scaled); governor valve time
  constant 0.5, 2 and 6 s (6 s matches the reduced-order screen).
- Fault: three-phase, 0.005 pu reactance (~20,000 MVA, several times the
  published bus fault levels, i.e. near-bolted), cleared in 100 ms (**VERIFY**
  against NER S5.1a.8 / Powerlink protection clearing times).
- Facility trip: whole facility disconnects 20 ms into the fault if its bus
  voltage falls below 0.7 pu. This is an **assumption** standing in for
  UPS/protection settings; NERC's review of the July 2024 Virginia event [NERC
  2025] reports ~1,500 MW of data-centre load transferring to backup after
  voltage dips.
- Loads are constant current during faults (constant power has no solution near
  zero voltage).

## Recent prices (run_recent_prices.py)

AEMO Price and Demand files for QLD1, January 2025 to the latest published
month (November 2026 at the time of the run), with NASA POWER 2025 weather for
Rockhampton. The 2026 window uses 2025 weather on the same calendar hours.

## Flexible load (run_flexible_load.py)

- Lost-compute value per IT MWh: swept A$300, 1,000 and 3,000. Illustrative
  basis (**VERIFY**): ~A$6 bn of IT equipment for 200 MW of IT, 5-year life at 7%
  is ~A$1.5 bn/yr, or ~A$850 per MWh of IT at full output.
- Flexible share: 50% of IT can be paused at short notice (checkpointed training).

## GPU-cluster trace (build_gpu_trace_profile.py)

Alibaba cluster-trace-gpu-v2020 (`pai_task_table`, `pai_machine_spec`), from
https://github.com/alibaba/clusterdata/tree/master/cluster-trace-gpu-v2020.
Cite: Weng, Q. et al. (2022) "MLaaS in the Wild: Workload Analysis and
Scheduling in Large-Scale Heterogeneous GPU Clusters", NSDI '22. The trace has
no time-stamped utilisation, so the profile is GPUs allocated to running tasks
divided by installed GPUs (6,742), 54 days after removing one week at each end.
It is a shared multi-tenant cluster (training and inference), not a single
synchronous training run, and its clock phase is undocumented.

## Site water and land scores

Scores (1-5) in `inputs/candidate_sites.csv`, each with its evidence:
- Water: Sunwater Rookwood Weir fact sheet; Gladstone Area Water Board
  (Awoonga Dam, Fitzroy to Gladstone Pipeline,
  https://www.business.qld.gov.au/industries/mining-energy-water/water/industry-infrastructure/infrastructure/major-projects/fitzroy-gladstone-pipeline-project);
  WA Government Pilbara water statements (Dampier desalination,
  https://www.wa.gov.au/government/media-statements/Cook%20Labor%20Government/Expanded-desalination-plant-to-help-secure-Pilbara%27s-water-supply-20260304);
  Business News on Port Hedland supply constraints.
- Land: Gladstone State Development Area 26,934 ha (Coordinator-General,
  https://statedevelopment.qld.gov.au/coordinator-general/state-development-areas/current/gladstone-state-development-area);
  Maitland SIA 2,500 ha and Boodarie SIA 4,000 ha (DevelopmentWA); Gracemere
  Industrial Area (Rockhampton Regional Council).
- Fibre/latency: great-circle distance to the nearest of Brisbane, Sydney or
  Perth (computed).

## Reliability statement

The cited sources are authoritative public planning documents, but they are
incomplete for a connection application. Both utilities state that
published values are indicative and that a connection study is required.

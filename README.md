# Northern Australia AI data-centre feasibility screening

Reproducible public-data screening of candidate AI data-centre sites across
Northern Australia: **Rockhampton, Gladstone and Livingstone (Queensland,
NEM)** and **Karratha and Port Hedland (Western Australia, NWIS)**. It
combines OpenDSS network screens, a pvlib solar model, an hourly facility and
battery dispatch model, cost/price/emissions layers and a multi-criteria site
ranking.

## Run everything

```powershell
python -m pip install -r requirements.txt
.\run_all.cmd
```

`run_all` downloads any missing NASA POWER weather (5 sites x 2020-2024),
AEMO Queensland prices (2020-2024 and 2025-2026), one year of AEMO unit-level generation
(~440 MB, first run only) and the Alibaba GPU trace task table (~35 MB), runs
ANDES code generation once (single-process), runs the unit tests, every study, the validation
checks, and finally writes `results/AUTOMATED_STUDY_REPORT.md`. A full run
takes roughly 10-20 minutes once the data are downloaded, depending on the machine. Start reading the results from that report.

All assumptions are in `inputs/study_config.json`; candidate sites are in
`inputs/candidate_sites.csv`; connection-point network data are in
`inputs/site_cases.csv`. Change those files rather than the Python code.

## Models

| Script | What it does |
|---|---|
| `run_regional_equivalent_sweep.py` | Thevenin screen of every connection point in `site_cases.csv`: voltage-limited MW, SCR, PV-curve nose, load-trip and training-swing voltage steps, PF x source-voltage sensitivity |
| `plot_regional_equivalent_results.py` | Figures for the above |
| `run_pilbara_ring_study.py` | Reduced Karratha 132 kV ring: intact and all three line outages |
| `run_pilbara_sensitivity.py` | Ring sweep over source strength, PF and source voltage |
| `run_pilbara_uncertainty.py` | Latin-hypercube uncertainty, convergence check and tornado chart |
| `run_renewable_bess_assessment.py` | Single-year PV/BESS demand-cap cases at the primary site |
| `run_advanced_resource_study.py` | Multi-year PV/BESS grid with least-cost selection, emissions and on-site renewable share |
| `run_multisite_comparison.py` | Climate, solar, network, cost and emissions comparison and weighted ranking of the five sites |
| `run_solar_sensitivity.py` | Irradiance-bias sensitivity of the least-cost design and year-to-year solar variability; uses measured data from `inputs/solar_measured` when provided |
| `run_solar_validation.py` | Compares the solar model with metered output of operating Central Queensland farms (AEMO SCADA) |
| `run_cost_uncertainty.py` | Monte Carlo over capital cost, discount rate, price level/year, solar yield and tariff: cost range, design robustness, tornado |
| `run_climate_scenarios.py` | +1/+2/+3 C warming: peak demand, PUE, water and PV per site |
| `run_backup_reliability.py` | Standby genset sizing, redundancy vs start-failure risk, cost, fuel and emissions |
| `run_price_aware_dispatch.py` | Rule-based vs price-aware (perfect-foresight LP) battery dispatch: cost, emissions, curtailment |
| `run_growth_scenarios.py` | Staged facility growth (100-750 MW) vs declining system strength; minimum fault level each stage needs |
| `run_oscillation_screening.py` | AI training-load oscillation: flicker severity (Pst/Plt) and inter-area mode-band risk |
| `run_cq_multibus_study.py` | Reduced multi-bus Central Queensland 275 kV model fitted to TAPR fault levels: hosting with topological N-1, multi-site interaction, incremental flows |
| `build_cq_topology.py` | Derives the Central Queensland 275 kV topology and route lengths from OpenStreetMap |
| `run_rms_dynamic_study.py` | Open-source RMS dynamic study (ANDES: GENROU, TGOV1, SEXS) of the supervisor's event list at minimum and maximum conditions |
| `build_gpu_trace_profile.py` | Hourly GPU-allocation profile from the public Alibaba PAI GPU trace (2020) |
| `run_hourly_power_flow.py` | One year of hourly DC power flow on the multi-bus model with metered AEMO unit output; validated against Powerlink's Gladstone and CQ-SQ duration curves; circuit loading and N-1 change per siting option |
| `run_rms_sensitivity.py` | ANDES: inertia / governor-response sweep for the islanded case, and fault ride-through with facility trip at every bus |
| `run_recent_prices.py` | Least-cost design re-run at 2025 and 2026 prices |
| `run_flexible_load.py` | Training-load flexibility: price response and curtailment instead of a battery, with a lost-compute value |
| `run_trace_load_profile.py` | Synthetic facility profiles vs the real GPU-cluster trace: peaks, ramps, battery needs |
| `run_dynamic_screening.py` | Reduced-order voltage and frequency event screening (min/max operating conditions; load pickup/loss, PV trip, BESS trip, network outage) |
| `run_model_validation.py` | Independent checks of the OpenDSS implementation |
| `generate_study_report.py` | Report, thesis results table and run metadata (input hashes, package versions) |

The project is under git; commit after each change so results stay traceable.

Shared code lives in `studylib/` (weather and solar, facility load and water,
rule-based and LP dispatch, economics and network tariffs, network maths,
multi-bus equivalent, dynamics). Unit tests are in `tests/` (`python -m pytest`).

## Result classes

- **275 kV Thevenin screens (Bouldercombe, Calliope River, Larcom Creek):**
  voltage-only. Public thermal headroom and background flows are unavailable,
  so these are not firm hosting capacity.
- **22 kV upper bounds (Pegs Creek, Bulgarra, Wedgefield):** optimistic,
  based on published spare transformer capacity and *maximum* fault levels.
- **Karratha 132 kV ring:** reduced-network sensitivity with assumed
  impedance and source strength.
- **PV/BESS:** chronological demand-cap planning sensitivity. It shows whether
  on-site resources keep grid import below a ceiling; it does not increase the
  network's physical rating.
- **Multi-site ranking:** relative screening between these five sites only.

Read `DATA_SOURCES.md` and `RESULTS_INTERPRETATION.md` before quoting
outputs. The
`inputs/utility_model_template` folder is ready for a de-identified utility
network equivalent; its placeholder rows are not network data.

## Licence and citation

Code: MIT (`LICENSE`). Input data keep their original terms (`DATA_LICENCES.md`).
The Alibaba GPU trace is not redistributed; `build_gpu_trace_profile.py`
downloads it.

To cite: Biswas, D. (2026) *Feasibility of AI Data Centres in Northern
Australia: Grid Hosting, Energy Supply and Site Screening*. Engineering
design thesis, with code and data at the repository linked in the thesis.


"""Generate the study report, thesis results table and run metadata.

Every number in results/AUTOMATED_STUDY_REPORT.md and
results/thesis_results_table.csv is read from the latest model outputs, so
the written results can never drift from the simulations.
"""

from __future__ import annotations

import importlib.metadata
import json
import platform
import sys
from datetime import datetime, timezone

import pandas as pd

from studylib.config import CONFIG_PATH, INPUTS, RESULTS, load_config, site_by_id
from studylib.io import sha256, write_csv


PACKAGES = ["opendssdirect.py", "dss-python", "numpy", "pandas", "matplotlib", "scipy", "pvlib"]


def markdown_table(frame: pd.DataFrame, digits: int = 2) -> str:
    def fmt(value):
        if isinstance(value, float):
            return "n/a" if value != value else f"{value:,.{digits}f}"
        return str(value)

    header = "| " + " | ".join(frame.columns) + " |"
    rule = "| " + " | ".join(["---"] * len(frame.columns)) + " |"
    rows = ["| " + " | ".join(fmt(value) for value in row) + " |" for row in frame.itertuples(index=False, name=None)]
    return "\n".join([header, rule, *rows])


def package_versions() -> dict[str, str]:
    versions = {}
    for name in PACKAGES:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = "not installed"
    return versions


def main() -> None:
    config = load_config()
    target = config["connection"]["target_mw"]
    resource = config["renewable_bess"]
    limit = resource["selected_trace_limit_mw"]
    primary = site_by_id(resource["primary_site"])["name"]

    regional = pd.read_csv(RESULTS / "regional_equivalent_sweep_summary.csv")
    regional_sens = pd.read_csv(RESULTS / "regional_equivalent_pf_voltage_sensitivity.csv")
    ring = pd.read_csv(RESULTS / "pilbara_ring_sweep_summary.csv")
    ring_sens = pd.read_csv(RESULTS / "pilbara_source_strength_sensitivity.csv")
    monte = pd.read_csv(RESULTS / "pilbara_monte_carlo_summary.csv").set_index("Statistic")["Value"]
    tornado = pd.read_csv(RESULTS / "pilbara_tornado.csv").sort_values("SwingMW", ascending=False)
    single = pd.read_csv(RESULTS / "renewable_bess_summary.csv")
    best = pd.read_csv(RESULTS / "advanced_resource_best_cases.csv")
    sites = pd.read_csv(RESULTS / "multisite_comparison.csv")
    validation = pd.read_csv(RESULTS / "model_validation.csv")
    dyn_voltage = pd.read_csv(RESULTS / "dynamic_voltage_events.csv")
    dyn_frequency = pd.read_csv(RESULTS / "dynamic_frequency_events.csv")
    solar_bias = pd.read_csv(RESULTS / "solar_bias_sensitivity.csv")
    solar_years = pd.read_csv(RESULTS / "solar_capacity_factor_by_year.csv")
    price_aware = pd.read_csv(RESULTS / "price_aware_dispatch_comparison.csv")
    growth_need = pd.read_csv(RESULTS / "growth_required_fault_level.csv")
    oscillation = pd.read_csv(RESULTS / "oscillation_flicker_screen.csv")
    mb_cal = pd.read_csv(RESULTS / "cq_multibus_calibration.csv")
    mb_host = pd.read_csv(RESULTS / "cq_multibus_hosting.csv")
    mb_multi = pd.read_csv(RESULTS / "cq_multibus_multi_site.csv")
    mb_flows = pd.read_csv(RESULTS / "cq_multibus_incremental_flows.csv")
    section_proxy = pd.read_csv(RESULTS / "cq_grid_section_proxy.csv")
    rms = pd.read_csv(RESULTS / "rms_dynamic_events.csv")
    solar_valid = pd.read_csv(RESULTS / "solar_validation_summary.csv")
    cost_unc = pd.read_csv(RESULTS / "cost_uncertainty_summary.csv")
    cost_freq = pd.read_csv(RESULTS / "cost_uncertainty_design_frequency.csv")
    cost_tornado = pd.read_csv(RESULTS / "cost_uncertainty_tornado.csv").sort_values("SwingAUDm", ascending=False)
    climate = pd.read_csv(RESULTS / "climate_scenarios.csv")
    backup = pd.read_csv(RESULTS / "backup_sizing.csv")
    price_select = pd.read_csv(RESULTS / "price_aware_least_cost_selection.csv")
    grid_profile = pd.read_csv(INPUTS / "grid" / f"{config['grid_emissions']['region']}_hourly_emissions.csv", parse_dates=["timestamp"])
    flow_valid = pd.read_csv(RESULTS / "hourly_flow_validation.csv")
    flow_cal = pd.read_csv(RESULTS / "hourly_flow_calibration.csv")
    flow_section = pd.read_csv(RESULTS / "hourly_flow_gladstone_section.csv")
    flow_circuits = pd.read_csv(RESULTS / "hourly_flow_circuits.csv")
    rms_thresholds = pd.read_csv(RESULTS / "rms_frequency_response_thresholds.csv")
    rms_faults = pd.read_csv(RESULTS / "rms_fault_ride_through.csv")
    recent = pd.read_csv(RESULTS / "recent_prices_selection.csv")
    flexible = pd.read_csv(RESULTS / "flexible_load.csv")
    trace_profile = pd.read_csv(RESULTS / "trace_load_profile_comparison.csv")

    firm_ring = ring[ring["Scenario"].str.startswith("n_minus_1")]["LastPassingAddedMW"].min()
    thesis_rows: list[dict[str, object]] = []

    def add(study, case, value, unit, outcome, classification):
        thesis_rows.append({"study": study, "case": case, "result_value": value, "result_unit": unit,
                            "binding_or_outcome": outcome, "classification": classification})

    for row in regional.itertuples():
        add(f"{row.ConnectionPoint} Thevenin", f"{row.Case}, PF {row.BasePowerFactor:.2f}", row.LastPassingMW, "MW",
            row.BindingConstraint, row.Classification)
        add(f"{row.ConnectionPoint} Thevenin", f"{row.Case}, SCR at {target:.0f} MW", round(row.SCRAtTarget, 1), "ratio",
            "Short-circuit ratio", "Screening indicator")
    for row in ring.itertuples():
        add("Karratha 132 kV ring", f"{row.Scenario}, PF {row.PowerFactor:.2f}", row.LastPassingAddedMW, "MW",
            row.BindingConstraint, "Reduced-network sensitivity")
    add("Karratha 132 kV ring", "sensitivity firm N-1 range",
        f"{ring_sens['FirmNMinus1HostingEstimateMW'].min():.0f}-{ring_sens['FirmNMinus1HostingEstimateMW'].max():.0f}", "MW",
        "Source strength, PF and source voltage sweep", "Assumption sensitivity")
    for statistic in ("P05", "P50", "P95"):
        add("Pilbara uncertainty", f"firm N-1 {statistic}", round(float(monte[statistic]), 1), "MW",
            f"{int(monte['Samples'])}-sample Latin hypercube", "Not a utility probability")
    add("Pilbara uncertainty", "line-rating ceiling", round(float(monte["LineRatingCeilingMW"]), 1), "MW",
        f"{100 * float(monte['ShareOfSamplesThermallyLimited']):.0f}% of samples thermally limited", "Assumption sensitivity")
    for row in best[(best["LoadProfile"] == "training")].itertuples():
        add(f"{primary} multi-year sizing", f"training, {row.ImportLimitMW:.0f} MW limit, {row.Selection}",
            f"{row.PvMW:.0f} MW PV + {row.BessPowerMW:.0f} MW/{row.BessEnergyMWh:.0f} MWh", "configuration",
            f"AUD {row.TotalAnnualCostAUDm:.1f} m/yr; on-site RE {100 * row.OnsiteRenewableFraction:.0f}%", "Planning sensitivity")
    for row in sites.itertuples():
        add("Multi-site comparison", row.Site, int(row.Rank), "rank",
            f"score {row.Score_WeightedScore:.2f}; {row.FeasibilityGate}", "Relative screening ranking")
    add("Model validation", "maximum Thevenin voltage difference",
        f"{validation[validation['Check'] == 'Thevenin voltage']['AbsoluteDifference'].max():.2e}", "pu",
        "Independent phasor calculation", "Implementation check")
    for point, frame in dyn_voltage[dyn_voltage["Condition"] == "Minimum"].groupby("ConnectionPoint", sort=False):
        failures = frame[~frame["Passes"]]
        add("Dynamic screening (voltage)", f"{point}, minimum condition", f"{len(frame) - len(failures)}/{len(frame)}",
            "events passing", "; ".join(sorted(set(failures["Event"]))) or "All events pass",
            "Reduced-order screening; step limit is an assumption pending Powerlink confirmation")
    for (grid, condition), frame in dyn_frequency.groupby(["Grid", "Condition"], sort=False):
        add("Dynamic screening (frequency)", f"{grid}, {condition.lower()} condition",
            f"{int(frame['Passes'].sum())}/{len(frame)}", "events passing",
            f"largest event = {frame['EventShareOfLargestContingency'].max():.0%} of largest credible contingency",
            "Assumed system parameters - VERIFY")
    write_csv(RESULTS / "thesis_results_table.csv", thesis_rows)

    regional_table = regional[["ConnectionPoint", "Case", "FaultLevelMVA", "BasePowerFactor", "LastPassingMW", "SCRAtTarget",
                               "PVNoseMW", "FullTripVoltageStepPercent", "BindingConstraint"]]
    sens_pivot = regional_sens.pivot_table(index=["ConnectionPoint", "Case"], columns=["PowerFactor", "SourceVoltagePu"],
                                           values="LastPassingMW").reset_index()
    sens_pivot.columns = [" ".join(str(c) for c in col).strip() if isinstance(col, tuple) else col for col in sens_pivot.columns]
    sens_pivot.columns = [c if c in ("ConnectionPoint", "Case") else f"PF {c.split()[0]} / {c.split()[1]} pu" for c in sens_pivot.columns]
    ring_table = ring[["Scenario", "PowerFactor", "LastPassingAddedMW", "SCRAtTarget", "TripVoltageStepAtLimitPercent", "BindingConstraint"]]
    single_table = single[single["GridLimitMW"] == limit][["CaseLabel", "PeakGridImportMW", "HoursAboveImportLimit",
                                                            "BatteryPvChargingMWh", "BatteryGridChargingMWh", "CompliesWithImportLimit"]]
    best_table = best[["LoadProfile", "ImportLimitMW", "Selection", "PvMW", "BessPowerMW", "BessEnergyMWh",
                       "NetworkChargesAUDm", "TotalAnnualCostAUDm", "AnnualScope2EmissionsKt", "HourlyMatchedEmissionsKt",
                       "CfeScore", "OnsiteRenewableFraction"]]
    site_table = sites[["Rank", "Site", "FeasibilityGate", "FirmScreeningMW", "PeakFacilityDemandMW", "SCRAtTarget",
                        "SolarCapacityFactor", "AnnualMeanPUE", "AnnualWaterUseML", "CostOfSupplyAUDperMWh",
                        "EmissionsIntensityTperMWh", "Score_WeightedScore"]]
    site_extra = sites[["Site", "WaterScore", "LandScore", "HubDistanceKm", "NearestHub", "WaterBasis", "LandBasis"]]
    rms_table = rms[["Condition", "DataCentreBus", "Event", "FrequencyNadirHz", "FrequencyZenithHz", "RoCoF500msHzPerS",
                     "MaxVoltageStepPercent", "VoltageRecoveryTimeS", "Outcome"]]
    section_table = section_proxy[["Case", "IncrementalSectionFlowMW", "EffectiveLimitMW", "PeakSectionFlowMW",
                                   "HoursPerYearAboveLimit", "Interpretation"]]
    workload_table = sites[["Site", "PeakFacilityDemandMW", "MixedWorkloadPeakMW", "LeastCostDesign", "MixedWorkloadLeastCostDesign",
                            "CostOfSupplyAUDperMWh", "MixedWorkloadCostOfSupplyAUDperMWh", "CfeScore"]]
    climate_table = climate[climate["LoadProfile"] == "training"].pivot(index="Site", columns="WarmingC", values="PeakFacilityDemandMW").reset_index()
    climate_table.columns = ["Site"] + [f"+{c:g} C peak MW" for c in climate_table.columns[1:]]
    water_table = climate[climate["LoadProfile"] == "training"].pivot(index="Site", columns="WarmingC", values="AnnualWaterUseML").reset_index()
    water_table.columns = ["Site"] + [f"+{c:g} C water ML" for c in water_table.columns[1:]]
    backup_table = backup[["Site", "UnitsNeededForPeak", "SpareUnitsForTargetRisk", "GensetUnits", "InstalledBackupMW",
                           "BackupCapexAUDm", "AnnualisedBackupCostAUDm", "ProbabilityInsufficientWithNPlus1", "AnnualTestEmissionsKt"]]
    valid_table = solar_valid[["Farm", "PeriodStart", "PeriodEnd", "EstimatedACCapacityMW", "MeteredCFFilteredHours",
                               "ModelCFFilteredHours", "MeteredToModelRatio", "HourlyR2Daylight", "ShareOfHoursExcludedForPrice"]]
    mean_ratio = float(solar_valid["MeteredToModelRatio"].mean())
    grid_profile["hour"] = grid_profile["timestamp"].dt.hour
    diurnal = grid_profile.groupby("hour")[["intensity_t_per_mwh", "zero_emission_share"]].mean()
    grid_period = f"{grid_profile['timestamp'].min():%d %b %Y} - {grid_profile['timestamp'].max():%d %b %Y}"
    solar_table = solar_bias[["IrradianceScale", "MeanCapacityFactor", "Selection", "PvMW", "BessPowerMW", "BessEnergyMWh",
                              "TotalAnnualCostAUDm", "AnnualScope2EmissionsKt"]]
    solar_spread = solar_years.groupby("Site", sort=False)["CapacityFactor"].agg(["min", "mean", "max"]).reset_index()
    solar_source = solar_years["IrradianceSource"].iloc[0]
    price_table = price_aware[["Design", "Controller", "AnnualEnergyCostAUDm", "NetworkChargesAUDm", "TotalAnnualCostAUDm",
                               "SavingVsRuleAUDm", "CurtailedSolarMWh", "EquivalentFullCycles", "AnnualScope2EmissionsKt"]]
    growth_table = growth_need[["ConnectionPoint", "StageMW", "RequiredFaultKA", "RequiredAsShareOfPublished",
                                "SystemStrengthHeadroomPercent"]]
    osc_summary = []
    for point, frame in oscillation.groupby("ConnectionPoint", sort=False):
        passing = frame.groupby("SwingFractionOfIT")["PassesPlanningLevels"].all()
        allowed = passing[passing].index.max() if passing.any() else 0.0
        worst = frame.loc[frame["EstimatedPst"].idxmax()]
        osc_summary.append({"ConnectionPoint": point, "LargestSwingPassingAtAllPeriods": f"{allowed:.0%} of IT",
                            "WorstPst": worst["EstimatedPst"], "WorstSwingMW": worst["SwingMW"], "WorstPeriodS": worst["PeriodS"]})
    osc_table = pd.DataFrame(osc_summary)
    mb_cal_table = mb_cal[["Bus", "Name", "PublishedMinNormalMVA", "ModelMinNormalMVA", "FitErrorPercent",
                           "PublishedMinN1MVA", "ModelMinN1MVA", "N1ValidationErrorPercent"]]
    mb_host_table = mb_host[["Name", "SingleBusTheveninIntactMW", "IntactHostingMW", "WorstN1HostingMW", "WorstOutage", "N1LimitingBus"]]
    top_flows = mb_flows.sort_values("ShareOfAddedLoadPercent", ascending=False).groupby("Case", sort=False).head(5)
    context = sites[sites["RegionalDemandContext"].notna() & (sites["RegionalDemandContext"] != "")][["Site", "RegionalDemandContext"]]
    excluded = sites["ExcludedCriteria"].iloc[0]
    dv_table = dyn_voltage[["ConnectionPoint", "Condition", "Event", "PreEventVoltagePu", "VoltageStepPercent",
                            "RecoveryTimeToBandS", "Outcome"]]
    df_table = dyn_frequency[["Grid", "Condition", "Event", "GridImportChangeMW", "EventShareOfLargestContingency",
                              "NadirHz", "ZenithHz", "RoCoF500msHzPerS", "Outcome"]]
    requirement = dyn_frequency.groupby(["Grid", "Event"], sort=False, as_index=False).agg(
        MinimumInertiaForRoCoFMWs=("MinimumInertiaForRoCoFMWs", "first"),
        MinimumPfrForContainmentMWperHz=("MinimumPfrForContainmentMWperHz", "max"),
    )
    dyn_cfg = config["dynamic_screening"]
    flow_valid_table = flow_valid.pivot(index="PercentOfTime", columns="GridSection",
                                        values=["PublishedMW_2024_25", "ModelMW"]).reset_index()
    flow_valid_table.columns = ["PercentOfTime"] + [f"{b} {'published' if a.startswith('Published') else 'model'} MW"
                                                    for a, b in flow_valid_table.columns[1:]]
    flow_change = flow_circuits[flow_circuits["Case"].isin(["245 MW at Bouldercombe", "245 MW at Calliope River"])]
    flow_change = flow_change.sort_values("ChangeInMaxN1LoadingPoints", ascending=False).groupby("Case").head(5)
    flow_change_table = flow_change[["Case", "Circuit", "MeanChangeMW", "MaxN1LoadingPercent", "ChangeInMaxN1LoadingPoints",
                                     "HoursN1AboveRating"]]
    fault_summary = rms_faults.groupby(["Condition", "DataCentreBus"], as_index=False).agg(
        FaultBusesTested=("FaultBus", "count"), FaultsThatTripFacility=("DataCentreTrips", "sum"),
        WorstZenithHz=("FrequencyZenithHz", "max"), WorstNadirHz=("FrequencyNadirHz", "min"))
    ride = config["rms_sensitivity"]["fault_ride_through"]
    recent_table = recent[["PriceWindow", "Controller", "Design", "TotalAnnualCostAUDm", "MeanPriceAUDperMWh",
                           "CostOfBaseDesignAUDm", "RegretOfBaseDesignAUDm"]]
    flexible_table = flexible[["Case", "LostComputeValuePerMWh", "PvMW", "BessPowerMW", "HoursPausedPerYear",
                               "LostItGWhPerYear", "LostComputeValueAUDm", "TotalAnnualCostAUDm", "SavingVsBaseDesignAUDm",
                               "MeetsImportLimit"]]
    trace_table = trace_profile[["Profile", "PeakFacilityMW", "MeanFacilityMW", "LoadFactor", "P99HourlyRampMW",
                                 "HoursPerYearAboveLimit", "LargestContinuousExcessMWh", "LeastCostDesign", "LeastCostTotalAUDm"]]

    report = f"""# Automated study report

Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} from the current model outputs.
Connection target: **{target:.0f} MW** (at or above the modelled training-profile peak demand).
All results are public-data screening values, not connection approvals.

## 1. Thevenin screening of connection points

Base load power factor follows the voltage-banded NER minimum; source voltage
{config['network_criteria']['base_source_voltage_pu']:.2f} pu; criterion {config['network_criteria']['minimum_voltage_pu']} pu.
Thermal limits are applied only where a public headroom value exists. Thermal
headroom cannot be inferred from fault level: it needs line and transformer
ratings, existing flows and N-1 load-flow results from the network owner. The
{config['connection']['load_trip_step_limit_percent']:g}% voltage-step limit is an initial-screening assumption
(NER S5.1.5, S5.3.7) until Powerlink confirms the limit for this connection.

{markdown_table(regional_table)}

![Regional screening](regional_equivalent_screening.png)

### Sensitivity to load PF and pre-connection source voltage (voltage-limited MW)

{markdown_table(sens_pivot, 0)}

![PF and voltage sensitivity](regional_pf_voltage_sensitivity.png)

## 2. Karratha 132 kV reduced ring

{markdown_table(ring_table)}

Firm N-1 at base assumptions: **{firm_ring:.0f} MW**. Across the source-strength,
PF and source-voltage sweep: {ring_sens['FirmNMinus1HostingEstimateMW'].min():.0f}-{ring_sens['FirmNMinus1HostingEstimateMW'].max():.0f} MW.

![Pilbara sensitivity](pilbara_source_strength_sensitivity.png)

## 3. Pilbara uncertainty ({int(monte['Samples'])} Latin-hypercube samples)

P05 / P50 / P95 firm N-1: **{monte['P05']:.0f} / {monte['P50']:.0f} / {monte['P95']:.0f} MW**.
{100 * monte['ShareOfSamplesThermallyLimited']:.0f}% of samples are limited by the 157 MVA line rating,
whose ceiling is {monte['LineRatingCeilingMW']:.0f} MW, so the upper percentiles are a rating cap rather than a
statistical finding. Most influential parameter: **{tornado.iloc[0]['Parameter']}**
(swing {tornado.iloc[0]['SwingMW']:.0f} MW). Probability of reaching {target:.0f} MW: {monte[f'ProbabilityAtLeast{target:.0f}MW']:.0%}.

![Pilbara uncertainty](pilbara_monte_carlo.png)

## 4. {primary} single-year PV/BESS ({resource['single_year']}, {limit:.0f} MW import limit)

{markdown_table(single_table)}

![Single-year summary](renewable_bess_summary.png)
![Stress week](renewable_bess_stress_week.png)

## 5. {primary} multi-year least-cost sizing ({min(resource['weather_years'])}-{max(resource['weather_years'])})

{markdown_table(best_table)}

Costs are annualised CSIRO GenCost capital costs, grid energy at historical
hourly AEMO prices, and Powerlink 2026/27 transmission network charges
(locational demand charge on monthly peak import plus energy-based
non-locational and common-service charges; GST removed). PV is selected even
where the limit does not require it because its levelised cost is below the
average wholesale-plus-network price.

Emissions are reported two ways: **location-based scope 2** with the annual
NGA factor (the reporting convention), and **hourly-matched** emissions using
the Queensland grid's hour-by-hour intensity ({grid_period}, AEMO SCADA x CDEII
factors). The **CFE score** is the share of facility energy met in the same
hour by carbon-free supply (on-site PV plus the grid's zero-emission share).
Queensland grid intensity ranges from {diurnal['intensity_t_per_mwh'].min():.2f} t/MWh at
{int(diurnal['intensity_t_per_mwh'].idxmin())}:00 to {diurnal['intensity_t_per_mwh'].max():.2f} t/MWh at
{int(diurnal['intensity_t_per_mwh'].idxmax())}:00 on average, so the timing of grid imports matters.

![Multi-year sizing](advanced_resource_sizing.png)

## 6. Northern Australia multi-site comparison

{markdown_table(site_table, 3)}

Criteria excluded for missing data: {excluded}. NWIS energy cost uses an
ASSUMED flat price. Score differences between nearby Queensland sites are
within the resolution of the NASA POWER grid and should not be over-interpreted.

{markdown_table(context) if not context.empty else ''}

![Multi-site comparison](multisite_comparison.png)

Training-only vs a {100 * config['facility']['mixed_training_share']:.0f}/{100 - 100 * config['facility']['mixed_training_share']:.0f} training/inference mix:

{markdown_table(workload_table, 2)}

Water, land and connectivity evidence (scores 1-5 from cited public
sources; distance to the nearest data-centre hub as a latency proxy):

{markdown_table(site_extra, 0)}

Water use is a physics-based estimate (latent heat of evaporation plus
blowdown, with dry cooling below the configured temperature), so hot sites use
more. Network charges are excluded from the cost-of-supply column for
cross-grid comparability; they are reported in section 5.

## 7. Solar-resource uncertainty and validation

### Validation against operating Central Queensland solar farms

{markdown_table(valid_table, 3)}

Hours with QLD1 price <= 0 (economic curtailment) and outage days are
excluded. Average metered/model ratio: **{mean_ratio:.2f}**, i.e. the NASA-driven
model {"over" if mean_ratio < 1 else "under"}-predicts real tracking-farm output by about
{abs(1 - mean_ratio) * 100:.0f}% on comparable hours. Farm mounting and DC/AC ratio are
assumed, so the ratio also absorbs plant differences.

![Solar validation](solar_validation.png)

### Irradiance-bias sensitivity

Irradiance source: {solar_source}. Irradiance is scaled to test NASA POWER
bias; the least-cost design is re-selected at each level.

{markdown_table(solar_table, 3)}

Year-to-year capacity factor (min / mean / max over the weather years):

{markdown_table(solar_spread, 3)}

![Solar sensitivity](solar_sensitivity.png)

Ground-measured data placed in `inputs/solar_measured/<site_id>.csv` replaces
NASA POWER automatically.

## 8. Rule-based vs price-aware battery dispatch

{markdown_table(price_table, 2)}

Three controllers: rule-based peak shaving; a daily linear programme with
**perfect foresight** (an upper bound on price-aware savings); and a realistic
controller that plans each day on persistence forecasts (yesterday's prices
and PV) and keeps an energy reserve for import-limit duty. Savings come mainly
from avoiding extreme price spikes and from curtailing PV in negative-price
hours.

Least-cost design among the tested grid under each controller:

{markdown_table(price_select, 2)}

![Price-aware dispatch](price_aware_dispatch.png)

## 8a. Economic uncertainty (Monte Carlo)

{markdown_table(cost_unc, 3)}

How often each design is the least-cost choice:

{markdown_table(cost_freq.head(6), 3)}

Inputs ranked by influence on the base design's total cost:

{markdown_table(cost_tornado[["Parameter", "Low", "High", "TotalAtLowAUDm", "TotalAtHighAUDm", "SwingAUDm"]], 1)}

![Cost uncertainty](cost_uncertainty.png)

## 8a-1. Recent prices (2025-2026)

The base design was chosen on 2020-2024 prices. Re-running the design grid at
2025 prices (with 2025 weather) and 2026 prices to date (2025 weather):

{markdown_table(recent_table, 2)}

"Regret" is the extra annual cost of keeping the design chosen on 2020-2024
prices instead of re-optimising for the recent window.

![Recent prices](recent_prices.png)

## 8a-2. The data centre as a flexible load

Pausing the flexible share of training ({100 * config['flexible_load']['flexible_it_share']:.0f}% of IT) when the spot price
exceeds an assumed value of lost compute, or trimming load to hold the import
limit instead of using a battery. Totals include the value of lost compute
(assumption, swept):

{markdown_table(flexible_table, 2)}

![Flexible load](flexible_load.png)

## 8a-3. Real GPU-cluster load profile

The synthetic IT profile compared with the hourly GPU allocation of a large
production cluster (Alibaba PAI GPU trace, 2020; tiled over 2020-2024):

{markdown_table(trace_table, 2)}

![Trace load profile](trace_load_profile.png)

The trace has much larger hour-to-hour ramps than the synthetic profile. Run
at training-level utilisation, its peak reaches the full-IT, hot-weather
demand, which no battery in the design grid can cap at the import limit.

## 8b. Climate-change sensitivity

{markdown_table(climate_table, 1)}

{markdown_table(water_table, 0)}

![Climate scenarios](climate_scenarios.png)

## 8c. Backup supply and reliability (assumed parameters - VERIFY)

{markdown_table(backup_table, 3)}

With ~80 standby units, simple N+1 redundancy leaves a high chance that too
few units start; the spare-unit count above is sized for the configured
start-failure target.

![Backup reliability](backup_reliability.png)

## 9. Staged growth and declining system strength

Minimum fault level each facility stage needs to pass the voltage, SCR and
trip-step criteria, compared with today's published minimum N-1 value:

{markdown_table(growth_table, 2)}

![Growth vs system strength](growth_system_strength.png)

## 10. Training-load oscillation and flicker

{markdown_table(osc_table, 2)}

Repetitive training-load swings are screened as rectangular voltage changes
against the Pst = 1 curve (digitised approximation - VERIFY) and HV-EHV
planning levels (NER S5.1.5 / AS/NZS 61000.3.7). Swing periods of 1-10 s fall in
the inter-area mode band and need small-signal study regardless. Large
repetitive swings would need on-site smoothing (UPS/BESS/supercapacitor) or
job-level power management.

![Oscillation screen](oscillation_flicker_screen.png)

## 11. Reduced multi-bus Central Queensland model

Topology and route lengths from OpenStreetMap, circuit counts from the TAPR,
typical 275 kV impedances (VERIFY). Source impedances fitted to published
minimum system-normal fault levels, validated against the published minimum
N-1 levels it was not fitted to:

{markdown_table(mb_cal_table, 1)}

Voltage-limited hosting with a system-wide {config['network_criteria']['minimum_voltage_pu']} pu criterion and
topological line outages:

{markdown_table(mb_host_table, 0)}

Simultaneous data centres:

{markdown_table(mb_multi, 4)}

Lines carrying the largest share of the added load (incremental, no background
flows; these are the elements the thermal data request to Powerlink should cover):

{markdown_table(top_flows[["Case", "Line", "IncrementalFlowMW", "ShareOfAddedLoadPercent"]], 1)}

![Multi-bus study](cq_multibus_study.png)

### Gladstone grid-section thermal proxy

Powerlink's TAPR 2025 names the Gladstone grid section as thermally limited
(54 constrained hours in 2024/25). Its 2024/25 transfer duration curve
(digitised from TAPR Figure 6.17, approximate) is shifted by each case's
incremental section flow from the multi-bus model:

{markdown_table(section_table, 1)}

![Gladstone grid section](cq_grid_section_proxy.png)

A data centre at Bouldercombe draws power out of the Gladstone zone and
relieves the constrained section; one at Calliope River adds to it. This is a
corridor-level indication, not a thermal study: line ratings, background flows
and N-1 load flows are still needed from Powerlink.

### Hourly power flow (one year of metered generation)

DC power flow for every hour of the most recent 365 days: AEMO-metered output of the
Central Queensland units at their buses, zone loads scaled with Queensland
demand, the external system as slack. Two load multipliers were fitted so the
modelled Gladstone and CQ-SQ section flows match Powerlink's published
2024/25 duration curves:

{markdown_table(flow_cal, 2)}

{markdown_table(flow_valid_table, 0)}

The model reproduces the middle of both curves but understates their spread
(generation elsewhere in Queensland is not modelled), so it is used for the
**change** each siting option causes, not for constraint hours.

{markdown_table(flow_section[["Case", "MeanChangeMW", "P99SectionFlowMW", "HoursAboveEffectiveLimit"]], 1)}

Circuits whose worst single-outage loading rises most (assumed {config['hourly_flow']['assumed_rating_mva']['default']} MVA rating per
circuit; VERIFY):

{markdown_table(flow_change_table, 1)}

![Hourly power flow](hourly_power_flow.png)

A Bouldercombe connection relieves the Gladstone section but loads the
Stanwell-Bouldercombe circuits; a Calliope River connection does the opposite.
The ratings of both groups are the key items of the Powerlink data request.

## 12. Open-source RMS dynamic study (ANDES)

Synchronous-machine (GENROU), governor (TGOV1) and exciter (SEXS) models on the
reduced Central Queensland network, with typical parameters; the minimum
condition is set near AEMO's Queensland secure inertia level and treated as
islanded; the maximum condition is interconnected to the NEM.

{markdown_table(rms_table, 3)}

![RMS study](rms_dynamic_study.png)

The RMS results are less severe than the reduced-order screen below (for
example an islanded full-load loss peaks near 50.5 Hz rather than 51 Hz),
because every machine carries its own governor response. Both rely on assumed
governor settings and neither replaces a PowerFactory/PSS/E study with
network-owner models.

### Frequency-response sensitivity (islanded Queensland)

Minimum delivered governor response (share of the modelled response, and the
equivalent MW/Hz) that keeps the islanded full-load loss and pickup inside
49.0-51.0 Hz:

{markdown_table(rms_thresholds, 3)}

The disagreement between the RMS model and the reduced-order screen is
explained by governor speed: with fast valves the event passes with about 40%
of the modelled response, but with the 6 s response assumed in the
reduced-order screen it fails at any response level.

### Fault ride-through

Near-bolted three-phase fault at each bus, cleared in {ride['fault_clear_s'] * 1000:.0f} ms. If the data-centre
bus voltage falls below {ride['trip_threshold_pu']} pu (assumed facility trip setting), the whole
facility is disconnected during the fault:

{markdown_table(fault_summary, 3)}

![RMS sensitivity](rms_sensitivity.png)

## 12a. Dynamic event screening (reduced-order)

Minimum condition = published minimum N-1 fault level and low system inertia;
maximum condition = published maximum fault level and high inertia. Events:
sudden load loss and pickup, training-load swing, PV trip, BESS trip and
network outage. Voltage recovery assumes a {dyn_cfg['voltage']['recovery_time_constant_s']:g} s control time
constant. Frequency criteria: NEM mainland Frequency Operating Standard
(containment {dyn_cfg['frequency_standard']['containment_band_hz'][0]}-{dyn_cfg['frequency_standard']['containment_band_hz'][1]} Hz). Queensland-islanded inertia is AEMO's
minimum threshold (2024 Inertia Report); NEM and NWIS inertia and all
frequency-response values are assumptions. The event size relative to the
largest credible contingency and the minimum requirements below are the
robust outputs.

### Voltage

{markdown_table(dv_table, 2)}

![Dynamic voltage screening](dynamic_voltage_screening.png)

### Frequency

{markdown_table(df_table, 3)}

### Minimum system capability each event needs

{markdown_table(requirement, 0)}

![Dynamic frequency screening](dynamic_frequency_screening.png)

This screening identifies which events and conditions need detailed RMS/EMT
study (PowerFactory or PSS/E with network-owner models); it does not replace it.

## 13. Model validation

{len(validation)} checks; all pass: {bool(validation['Passes'].all())}. Maximum
Thevenin voltage difference {validation[validation['Check'] == 'Thevenin voltage']['AbsoluteDifference'].max():.2e} pu.

## Limitations

Firm capacity requires network-owner impedances, line and transformer
ratings, existing flows, N-1 load-flow results, control settings and approved
outage cases; thermal headroom cannot be estimated from fault level alone. Large-load dynamic
behaviour (fault ride-through, sub-second training-load oscillation) needs
EMT/RMS studies. The NWIS is an isolated system where frequency stability and
generation adequacy, not modelled here, may dominate.
"""
    (RESULTS / "AUTOMATED_STUDY_REPORT.md").write_text(report, encoding="utf-8")

    # Raw downloads (git-ignored, regenerated) are not hashed; their processed outputs are.
    input_files = sorted(path for path in INPUTS.rglob("*") if path.is_file() and "raw" not in path.parts)
    metadata = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "python": sys.version,
        "platform": platform.platform(),
        "packages": package_versions(),
        "config_path": str(CONFIG_PATH.relative_to(CONFIG_PATH.parents[1])),
        "config": config,
        "input_sha256": {str(path.relative_to(INPUTS.parent)).replace("\\", "/"): sha256(path) for path in input_files},
    }
    (RESULTS / "run_metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(RESULTS / "AUTOMATED_STUDY_REPORT.md")


if __name__ == "__main__":
    main()

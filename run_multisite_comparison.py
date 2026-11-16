"""Multi-criteria comparison of the Northern Australia candidate sites.

For each site in inputs/candidate_sites.csv this combines:

* climate: peak facility demand, PUE, wet-bulb and indicative water use;
* solar resource: single-axis-tracking PV capacity factor;
* network: firm (N-1 where available) screening limit and SCR from the
  network studies, compared with the site's own peak demand;
* energy cost and emissions of the least-cost compliant PV/BESS design at the
  primary import limit (historical prices for NEM sites, configured flat
  price for the NWIS);
* sourced water-security and industrial-land scores (candidate_sites.csv) and
  distance to the nearest data-centre hub (latency proxy).

Scores are min-max normalised across sites. A criterion is excluded for all
sites if any site lacks data for it, so totals stay comparable. Sites that
fail the firm network screen are ranked after all sites that pass it.
"""

from __future__ import annotations

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from run_advanced_resource_study import least_cost, run_site
from studylib.config import RESULTS, load_config, load_sites
from studylib.economics import regional_demand
from studylib.facility import climate_metrics, facility_load
from studylib.io import save_figure
from studylib.weather import load_site_weather


CONFIG = load_config()
RESOURCE = CONFIG["renewable_bess"]
WEIGHTS = CONFIG["scoring"]["weights"]
# Direction: +1 = higher is better, -1 = lower is better. Caps stop "more than enough" dominating.
CRITERIA = {
    "firm_hosting_ratio": ("FirmHostingRatio", +1, 1.0),
    "short_circuit_ratio": ("SCRAtTarget", +1, 10.0),
    "solar_capacity_factor": ("SolarCapacityFactor", +1, None),
    "annual_mean_pue": ("AnnualMeanPUE", -1, None),
    "energy_cost": ("CostOfSupplyAUDperMWh", -1, None),
    "emissions_intensity": ("EmissionsIntensityTperMWh", -1, None),
    "water_use": ("AnnualWaterUseML", -1, None),
    "water_score": ("WaterScore", +1, None),
    "hub_distance": ("HubDistanceKm", -1, None),
    "land_score": ("LandScore", +1, None),
}


def network_metrics(reference: str) -> dict[str, object]:
    if reference == "pilbara_ring":
        ring = pd.read_csv(RESULTS / "pilbara_ring_sweep_summary.csv")
        contingencies = ring[ring["Scenario"].str.startswith("n_minus_1")]
        firm = contingencies.loc[contingencies["LastPassingAddedMW"].idxmin()]
        return {"NetworkCase": f"Karratha ring {firm['Scenario']}", "FirmScreeningMW": float(firm["LastPassingAddedMW"]),
                "SCRAtTarget": float(firm["SCRAtTarget"]), "BindingConstraint": firm["BindingConstraint"]}
    kind, site_case = reference.split(":", 1)
    if kind != "regional":
        raise ValueError(f"Unknown network_result {reference!r}")
    regional = pd.read_csv(RESULTS / "regional_equivalent_sweep_summary.csv")
    rows = regional[regional["SiteId"] == site_case]
    if rows.empty:
        raise KeyError(f"{site_case} not found in regional_equivalent_sweep_summary.csv")
    preferred = rows[rows["Case"] == "n_minus_1"]
    row = (preferred if not preferred.empty else rows).iloc[0]
    return {"NetworkCase": f"{row['ConnectionPoint']} {row['Case']}", "FirmScreeningMW": float(row["LastPassingMW"]),
            "SCRAtTarget": float(row["SCRAtTarget"]), "BindingConstraint": row["BindingConstraint"]}


def hub_distance_km(latitude: float, longitude: float) -> tuple[float, str]:
    """Great-circle distance to the nearest configured data-centre hub (latency proxy)."""
    import math

    best = (float("inf"), "")
    for hub in CONFIG["scoring"]["data_centre_hubs"]:
        p1, p2 = math.radians(latitude), math.radians(hub["latitude"])
        dp, dl = p2 - p1, math.radians(hub["longitude"] - longitude)
        a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
        km = 6371.0 * 2 * math.asin(math.sqrt(a))
        best = min(best, (km, hub["name"]))
    return best


def optional_score(value: str) -> float:
    value = (value or "").strip()
    return float(value) if value else np.nan


def score(frame: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    normalised = pd.DataFrame(index=frame.index)
    excluded = []
    for key, (column, direction, cap) in CRITERIA.items():
        values = frame[column].astype(float)
        if values.isna().any():
            excluded.append(key)
            continue
        if cap is not None:
            values = values.clip(upper=cap)
        spread = values.max() - values.min()
        scaled = pd.Series(1.0, index=values.index) if spread < 1e-12 else (values - values.min()) / spread
        normalised[key] = scaled if direction > 0 else 1.0 - scaled
    used = {key: WEIGHTS[key] for key in normalised.columns}
    total_weight = sum(used.values())
    normalised["WeightedScore"] = sum(normalised[key] * weight / total_weight for key, weight in used.items())
    return normalised, excluded


def main() -> None:
    years = RESOURCE["weather_years"]
    limit = RESOURCE["selected_trace_limit_mw"]
    rows, designs = [], []
    for site in load_sites():
        weather = load_site_weather(site["site_id"], years)
        load = facility_load(weather, "training")
        climate = climate_metrics(weather, load)
        network = network_metrics(site["network_result"])
        sizing = run_site(site["site_id"], profiles=("training", "mixed"), limits=[limit])
        best = least_cost(sizing)
        best_mixed = best[(best["LoadProfile"] == "mixed") & (best["Selection"] == "Least cost (any charging)")]
        mixed_load = facility_load(weather, "mixed")["facility_mw"]
        best = best[best["LoadProfile"] == "training"]
        chosen = best[best["Selection"] == "Least cost (any charging)"]
        design = chosen.iloc[0] if not chosen.empty else None
        if design is not None:
            designs.append(design)
        facility_mwh = climate["AnnualFacilityEnergyGWh"] * 1000.0
        context = ""
        if site["grid"] == "NEM":
            peak_regional = float(regional_demand(site["price_region"]).max())
            context = f"{100 * climate['PeakFacilityDemandMW'] / peak_regional:.1f}% of {site['price_region']} peak demand ({peak_regional:,.0f} MW)"
        rows.append(
            {
                "SiteId": site["site_id"],
                "Site": site["name"],
                "State": site["state"],
                "Grid": site["grid"],
                **climate,
                "SolarCapacityFactor": float(weather["solar_cf"].mean()),
                **network,
                "FirmHostingRatio": network["FirmScreeningMW"] / climate["PeakFacilityDemandMW"],
                "NetworkPassesPeakDemand": network["FirmScreeningMW"] >= climate["PeakFacilityDemandMW"],
                "NetworkNote": site["network_note"],
                "DesignImportLimitMW": limit,
                "LeastCostDesign": "none compliant" if design is None else
                    f"{design['PvMW']:.0f} MW PV + {design['BessPowerMW']:.0f} MW/{design['BessEnergyMWh']:.0f} MWh BESS"
                    + (" (grid charging)" if design["GridChargingAllowed"] else ""),
                "TotalAnnualCostAUDm": np.nan if design is None else design["TotalAnnualCostAUDm"],
                # Network charges are excluded here for cross-grid comparability (no published NWIS tariff).
                "CostOfSupplyAUDperMWh": np.nan if design is None else
                    (design["TotalAnnualCostAUDm"] - design["NetworkChargesAUDm"]) * 1e6 / facility_mwh,
                "NetworkChargesAUDm": np.nan if design is None else design["NetworkChargesAUDm"],
                "NetworkTariffApplied": False if design is None else bool(design["NetworkTariffApplied"]),
                "OnsiteRenewableFraction": np.nan if design is None else design["OnsiteRenewableFraction"],
                "AnnualScope2EmissionsKt": np.nan if design is None else design["AnnualScope2EmissionsKt"],
                "EmissionsIntensityTperMWh": np.nan if design is None else design["AnnualScope2EmissionsKt"] * 1000.0 / facility_mwh,
                "EnergyPriceBasis": "AEMO historical hourly RRP" if site["grid"] == "NEM" else "ASSUMED flat price (no public market)",
                "RegionalDemandContext": context,
                "MixedWorkloadPeakMW": float(mixed_load.max()),
                "MixedWorkloadLeastCostDesign": "none compliant" if best_mixed.empty else
                    f"{best_mixed.iloc[0]['PvMW']:.0f} MW PV + {best_mixed.iloc[0]['BessPowerMW']:.0f} MW/{best_mixed.iloc[0]['BessEnergyMWh']:.0f} MWh",
                "MixedWorkloadCostOfSupplyAUDperMWh": np.nan if best_mixed.empty else
                    (best_mixed.iloc[0]["TotalAnnualCostAUDm"] - best_mixed.iloc[0]["NetworkChargesAUDm"]) * 1e6
                    / (mixed_load.sum() / len(years)),
                "CfeScore": np.nan if design is None else design["CfeScore"],
                "HourlyMatchedEmissionsKt": np.nan if design is None else design["HourlyMatchedEmissionsKt"],
                "WaterScore": optional_score(site["water_score"]),
                "HubDistanceKm": hub_distance_km(float(site["latitude"]), float(site["longitude"]))[0],
                "NearestHub": hub_distance_km(float(site["latitude"]), float(site["longitude"]))[1],
                "WaterBasis": site.get("water_basis", ""),
                "LandBasis": site.get("land_basis", ""),
                "LandScore": optional_score(site["land_score"]),
            }
        )
        print(f"{site['name']}: done", flush=True)

    frame = pd.DataFrame(rows).set_index("SiteId")
    normalised, excluded = score(frame)
    frame = frame.join(normalised.add_prefix("Score_"))
    # Feasibility gate: a site whose firm network screen cannot carry its own peak
    # demand ranks below every site that can, whatever its weighted score.
    frame["FeasibilityGate"] = np.where(frame["NetworkPassesPeakDemand"], "Passes network screen", "Fails network screen - augmentation required")
    order = frame.sort_values(["NetworkPassesPeakDemand", "Score_WeightedScore"], ascending=[False, False]).index
    frame.loc[order, "Rank"] = range(1, len(order) + 1)
    frame["Rank"] = frame["Rank"].astype(int)
    frame["ExcludedCriteria"] = ", ".join(excluded) if excluded else "none"
    frame = frame.sort_values("Rank")
    frame.to_csv(RESULTS / "multisite_comparison.csv")
    pd.DataFrame(designs).to_csv(RESULTS / "multisite_least_cost_designs.csv", index=False)

    fig, (ax_heat, ax_bar) = plt.subplots(1, 2, figsize=(15, 5.2), gridspec_kw={"width_ratios": [2.2, 1]})
    score_columns = [column for column in normalised.columns if column != "WeightedScore"]
    matrix = frame[[f"Score_{column}" for column in score_columns]].to_numpy()
    ax_heat.imshow(matrix, cmap="RdYlGn", vmin=0, vmax=1, aspect="auto")
    raw_columns = [CRITERIA[column][0] for column in score_columns]
    for (i, j), value in np.ndenumerate(matrix):
        raw = frame[raw_columns[j]].iloc[i]
        ax_heat.text(j, i, f"{raw:.3g}", ha="center", va="center", fontsize=8)
    ax_heat.set_xticks(range(len(score_columns)), [f"{c.replace('_', ' ')}\n(w={WEIGHTS[c]:.2f})" for c in score_columns], fontsize=7)
    ax_heat.set_yticks(range(len(frame)), frame["Site"])
    ax_heat.set_title("Criterion scores (colour = normalised 0-1, text = raw value)")
    ax_bar.barh(frame["Site"], frame["Score_WeightedScore"], color="#0072B2")
    ax_bar.invert_yaxis()
    ax_bar.set_xlim(0, 1)
    ax_bar.set_xlabel("Weighted score (relative to these five sites)")
    ax_bar.set_title("Weighted score (ranked: network-feasible sites first)")
    for i, (value, passes) in enumerate(zip(frame["Score_WeightedScore"], frame["NetworkPassesPeakDemand"])):
        ax_bar.text(value + 0.01, i, f"{value:.2f}" + ("" if passes else "  (network fails)"), va="center", fontsize=8)
    ax_bar.grid(axis="x", alpha=0.25)
    note = f"Excluded (missing data): {', '.join(excluded)}" if excluded else ""
    fig.suptitle("Northern Australia candidate-site comparison (screening; weights in study_config.json)\n" + note, fontsize=11)
    fig.tight_layout()
    save_figure(fig, RESULTS, "multisite_comparison")
    print(frame[["Site", "Rank", "Score_WeightedScore", "FirmScreeningMW", "PeakFacilityDemandMW", "SolarCapacityFactor", "CostOfSupplyAUDperMWh", "LeastCostDesign"]].to_string())


if __name__ == "__main__":
    main()

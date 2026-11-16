"""Solar-resource uncertainty: irradiance bias correction and year-to-year spread.

NASA POWER is uncalibrated against ground stations and the modelled
single-axis-tracking capacity factor (~30% in Queensland) is at the high end
of operating Australian farms. This study scales irradiance (GHI, DNI, DHI)
by the configured factors and re-selects the least-cost PV/BESS design at
the primary site, showing how sensitive the design and cost are to that
bias. It also reports each site's capacity factor by year. If a measured
file is placed in inputs/solar_measured, it is used automatically.
"""

from __future__ import annotations

import matplotlib.pyplot as plt
import pandas as pd

from run_advanced_resource_study import least_cost, run_site
from studylib.config import RESULTS, load_config, load_sites, site_by_id
from studylib.io import save_figure
from studylib.weather import load_site_weather


CONFIG = load_config()
RESOURCE = CONFIG["renewable_bess"]


def main() -> None:
    site_id = RESOURCE["primary_site"]
    limit = RESOURCE["selected_trace_limit_mw"]
    years = RESOURCE["weather_years"]

    design_rows = []
    for scale in CONFIG["solar"]["irradiance_scale_sensitivity"]:
        weather = load_site_weather(site_id, years, scale)
        best = least_cost(run_site(site_id, profiles=("training",), limits=[limit], irradiance_scale=scale))
        for row in best.itertuples():
            design_rows.append(
                {
                    "IrradianceScale": scale,
                    "IrradianceSource": weather.attrs["irradiance_source"],
                    "MeanCapacityFactor": float(weather["solar_cf"].mean()),
                    "Selection": row.Selection,
                    "PvMW": row.PvMW,
                    "BessPowerMW": row.BessPowerMW,
                    "BessEnergyMWh": row.BessEnergyMWh,
                    "TotalAnnualCostAUDm": row.TotalAnnualCostAUDm,
                    "AnnualScope2EmissionsKt": row.AnnualScope2EmissionsKt,
                    "OnsiteRenewableFraction": row.OnsiteRenewableFraction,
                }
            )
        print(f"scale {scale}: done", flush=True)
    designs = pd.DataFrame(design_rows)
    designs.to_csv(RESULTS / "solar_bias_sensitivity.csv", index=False)

    yearly_rows = []
    for site in load_sites():
        weather = load_site_weather(site["site_id"], years)
        by_year = weather.groupby(weather["timestamp"].dt.year)["solar_cf"].mean()
        for year, value in by_year.items():
            yearly_rows.append({"SiteId": site["site_id"], "Site": site["name"], "Year": int(year),
                                "CapacityFactor": float(value), "IrradianceSource": weather.attrs["irradiance_source"]})
    yearly = pd.DataFrame(yearly_rows)
    yearly.to_csv(RESULTS / "solar_capacity_factor_by_year.csv", index=False)

    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    any_charging = designs[designs["Selection"] == "Least cost (any charging)"]
    axes[0].plot(any_charging["MeanCapacityFactor"] * 100, any_charging["TotalAnnualCostAUDm"], marker="o", color="#0072B2")
    for row in any_charging.itertuples():
        axes[0].annotate(f"x{row.IrradianceScale:.2f}: {row.PvMW:.0f} MW PV +\n{row.BessPowerMW:.0f}/{row.BessEnergyMWh:.0f} MWh",
                         (row.MeanCapacityFactor * 100, row.TotalAnnualCostAUDm), fontsize=7, xytext=(5, 5), textcoords="offset points")
    axes[0].set_xlabel("Modelled PV capacity factor (%)")
    axes[0].set_ylabel("Least-cost total annual cost (AUD m)")
    axes[0].set_title(f"{site_by_id(site_id)['name']}: least-cost design vs irradiance bias ({limit:.0f} MW limit)")
    axes[0].grid(alpha=0.25)
    for site_name, frame in yearly.groupby("Site", sort=False):
        axes[1].plot(frame["Year"], frame["CapacityFactor"] * 100, marker="o", label=site_name)
    axes[1].set_xlabel("Year")
    axes[1].set_ylabel("PV capacity factor (%)")
    axes[1].set_title("Year-to-year solar variability (tracking PV, NASA POWER)")
    axes[1].legend(fontsize=8)
    axes[1].grid(alpha=0.25)
    fig.tight_layout()
    save_figure(fig, RESULTS, "solar_sensitivity")
    print(designs.round(3).to_string(index=False))


if __name__ == "__main__":
    main()

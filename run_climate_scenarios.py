"""Climate-change sensitivity: effect of warming on demand, cooling, water and PV.

A data centre is a 20-30 year asset, so the hottest future years matter more
than past averages. Each site's 2020-2024 weather is shifted uniformly by the
configured warming (dry- and wet-bulb), and the facility load, PUE, cooling
water and PV output are recomputed. The connection target is compared with
the new peak demand, showing whether today's connection size would still be
adequate.
"""

from __future__ import annotations

import matplotlib.pyplot as plt
import pandas as pd

from studylib.config import RESULTS, load_config, load_sites
from studylib.facility import climate_metrics, facility_load
from studylib.io import save_figure, write_csv
from studylib.weather import load_site_weather, solar_ac_per_mw


CONFIG = load_config()
TARGET = CONFIG["connection"]["target_mw"]


def main() -> None:
    years = CONFIG["renewable_bess"]["weather_years"]
    rows = []
    for site in load_sites():
        base = load_site_weather(site["site_id"], years)
        for warming in CONFIG["climate_scenarios"]["warming_c"]:
            weather = base.copy()
            weather["temp_c"] = weather["temp_c"] + warming
            weather["wet_bulb_c"] = weather["wet_bulb_c"] + warming
            solar_cf, _ = solar_ac_per_mw(weather, float(site["latitude"]), float(site["longitude"]))
            for profile in ("training", "mixed"):
                load = facility_load(weather, profile)
                metrics = climate_metrics(weather, load)
                rows.append({
                    "SiteId": site["site_id"], "Site": site["name"], "WarmingC": warming, "LoadProfile": profile,
                    "PeakFacilityDemandMW": metrics["PeakFacilityDemandMW"],
                    "AnnualMeanPUE": metrics["AnnualMeanPUE"],
                    "PeakPUE": metrics["PeakPUE"],
                    "AnnualFacilityEnergyGWh": metrics["AnnualFacilityEnergyGWh"],
                    "AnnualWaterUseML": metrics["AnnualWaterUseML"],
                    "SolarCapacityFactor": float(solar_cf.mean()),
                    "HoursPerYearAbove35C": float((weather["temp_c"] > 35.0).sum() / len(years)),
                    "PeakExceedsConnectionTarget": metrics["PeakFacilityDemandMW"] > TARGET,
                })
    write_csv(RESULTS / "climate_scenarios.csv", rows)
    frame = pd.DataFrame(rows)
    training = frame[frame["LoadProfile"] == "training"]

    fig, axes = plt.subplots(1, 3, figsize=(17, 5))
    for site_name, subset in training.groupby("Site", sort=False):
        axes[0].plot(subset["WarmingC"], subset["PeakFacilityDemandMW"], marker="o", label=site_name)
        axes[1].plot(subset["WarmingC"], subset["AnnualWaterUseML"], marker="o", label=site_name)
        axes[2].plot(subset["WarmingC"], subset["AnnualMeanPUE"], marker="o", label=site_name)
    axes[0].axhline(TARGET, color="#B22222", linestyle="--", label=f"{TARGET:.0f} MW connection target")
    axes[0].set_ylabel("Peak facility demand (MW)")
    axes[1].set_ylabel("Cooling water (ML/yr)")
    axes[2].set_ylabel("Annual mean PUE")
    for ax, title in zip(axes, ("Peak demand", "Water use", "Cooling efficiency")):
        ax.set_xlabel("Warming relative to 2020-2024 (deg C)")
        ax.set_title(f"{title} (training workload)")
        ax.grid(alpha=0.25)
    axes[0].legend(fontsize=7)
    fig.suptitle("Climate-change sensitivity (uniform warming; VERIFY against regional projections)")
    fig.tight_layout()
    save_figure(fig, RESULTS, "climate_scenarios")
    pivot = training.pivot(index="Site", columns="WarmingC", values="PeakFacilityDemandMW").round(1)
    print(pivot.to_string())


if __name__ == "__main__":
    main()

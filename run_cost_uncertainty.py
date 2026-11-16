"""Monte Carlo uncertainty of the least-cost PV/BESS design and its cost.

Uncertain inputs (Latin hypercube): PV and BESS capital cost, discount rate,
wholesale price level, which historical price year applies, solar irradiance
(NASA bias) and network tariff level. Dispatch is precomputed once per design
and irradiance level, so each sample only re-prices the designs. Outputs:

* the distribution of least-cost total annual cost (P10/P50/P90);
* how often each design is the least-cost choice (design robustness);
* a tornado chart of the deterministic least-cost design's total cost.
"""

from __future__ import annotations

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.stats import qmc

from run_advanced_resource_study import build_cases
from studylib.config import RESULTS, load_config, site_by_id
from studylib.dispatch import dispatch
from studylib.economics import capital_recovery_factor, hourly_price, network_charges
from studylib.facility import facility_load
from studylib.io import save_figure
from studylib.weather import load_site_weather


CONFIG = load_config()
RESOURCE = CONFIG["renewable_bess"]
ECO = CONFIG["economics"]
UNC = CONFIG["cost_uncertainty"]
CONTINUOUS = ["pv_capex_multiplier", "bess_capex_multiplier", "discount_rate", "price_level_multiplier", "network_tariff_multiplier"]


def precompute(site, years, limit):
    """Per design and irradiance scale: compliance, energy cost by year, network charge."""
    records = []
    for scale in CONFIG["solar"]["irradiance_scale_sensitivity"]:
        weather = load_site_weather(site["site_id"], years, scale)
        demand = facility_load(weather, "training")["facility_mw"]
        price = hourly_price(weather["timestamp"], site["grid"], site["price_region"])
        year_index = weather["timestamp"].dt.year.to_numpy()
        for pv_mw, power, energy, grid_charging in build_cases():
            result = dispatch(demand, pv_mw * weather["solar_cf"].to_numpy(), power, energy, limit, grid_charging)
            cost_by_year = pd.Series(result.grid * price).groupby(year_index).sum() / 1e6
            records.append({
                "scale": scale, "pv": pv_mw, "power": power, "energy": energy, "grid_charging": grid_charging,
                "complies": not bool((result.exceedance > 1e-6).any()),
                "energy_cost_by_year": cost_by_year.to_dict(),
                "network": network_charges(result.grid, weather["timestamp"], site["site_id"], len(years))["NetworkChargesAUDm"],
            })
        print(f"irradiance x{scale}: dispatch done", flush=True)
    return records


def resource_cost(pv, power, energy, pv_mult, bess_mult, rate) -> float:
    pv_capex = pv * 1000 * ECO["pv_capex_per_kw"] * pv_mult
    bess_capex = (energy * 1000 * ECO["bess_capex_per_kwh"] + power * 1000 * ECO["bess_capex_per_kw"]) * bess_mult
    annual = (pv_capex * capital_recovery_factor(rate, ECO["pv_life_years"]) + pv * 1000 * ECO["pv_fixed_om_per_kw_year"]
              + bess_capex * capital_recovery_factor(rate, ECO["bess_life_years"]) + bess_capex * ECO["bess_fixed_om_fraction_of_capex"])
    return annual / 1e6


def evaluate(records, params, year) -> pd.DataFrame:
    rows = []
    for r in records:
        if r["scale"] != params["irradiance_scale"] or not r["complies"]:
            continue
        total = (resource_cost(r["pv"], r["power"], r["energy"], params["pv_capex_multiplier"], params["bess_capex_multiplier"], params["discount_rate"])
                 + params["price_level_multiplier"] * r["energy_cost_by_year"][year]
                 + params["network_tariff_multiplier"] * r["network"])
        rows.append({"design": f"{r['pv']:.0f} MW PV + {r['power']:.0f}/{r['energy']:.0f} MWh" + (" (grid chg)" if r["grid_charging"] else ""),
                     "total": total})
    return pd.DataFrame(rows)


def main() -> None:
    site = site_by_id(RESOURCE["primary_site"])
    years = [int(y) for y in RESOURCE["weather_years"]]
    limit = RESOURCE["selected_trace_limit_mw"]
    records = precompute(site, years, limit)
    scales = CONFIG["solar"]["irradiance_scale_sensitivity"]

    base = {"pv_capex_multiplier": 1.0, "bess_capex_multiplier": 1.0, "discount_rate": ECO["discount_rate"],
            "price_level_multiplier": 1.0, "network_tariff_multiplier": 1.0,
            "irradiance_scale": CONFIG["solar"]["irradiance_scale"]}

    def averaged(params) -> pd.DataFrame:
        frames = [evaluate(records, params, year) for year in years]
        out = frames[0].copy()
        out["total"] = np.mean([f["total"].to_numpy() for f in frames], axis=0)
        return out

    base_costs = averaged(base)
    base_design = base_costs.loc[base_costs["total"].idxmin(), "design"]

    sampler = qmc.LatinHypercube(d=len(CONTINUOUS) + 2, seed=CONFIG["random_seed"])
    unit = sampler.random(int(UNC["samples"]))
    rows = []
    for u in unit:
        params = {name: UNC[name][0] + u[i] * (UNC[name][1] - UNC[name][0]) for i, name in enumerate(CONTINUOUS)}
        params["irradiance_scale"] = scales[min(int(u[-2] * len(scales)), len(scales) - 1)]
        year = years[min(int(u[-1] * len(years)), len(years) - 1)]
        costs = evaluate(records, params, year)
        best = costs.loc[costs["total"].idxmin()]
        base_cost = costs.loc[costs["design"] == base_design, "total"]
        rows.append({**params, "price_year": year, "LeastCostDesign": best["design"], "LeastCostTotalAUDm": best["total"],
                     "BaseDesignTotalAUDm": float(base_cost.iloc[0]) if not base_cost.empty else np.nan,
                     "RegretOfBaseDesignAUDm": float(base_cost.iloc[0]) - best["total"] if not base_cost.empty else np.nan})
    samples = pd.DataFrame(rows)
    samples.to_csv(RESULTS / "cost_uncertainty_samples.csv", index=False)

    least = samples["LeastCostTotalAUDm"]
    summary = pd.DataFrame({
        "Statistic": ["DeterministicLeastCostDesign", "DeterministicTotalAUDm", "P10TotalAUDm", "P50TotalAUDm", "P90TotalAUDm",
                      "ShareWhereBaseDesignIsOptimal", "MedianRegretOfBaseDesignAUDm", "P90RegretOfBaseDesignAUDm"],
        "Value": [base_design, float(base_costs["total"].min()), least.quantile(0.1), least.quantile(0.5), least.quantile(0.9),
                  float((samples["LeastCostDesign"] == base_design).mean()),
                  samples["RegretOfBaseDesignAUDm"].median(), samples["RegretOfBaseDesignAUDm"].quantile(0.9)],
    })
    summary.to_csv(RESULTS / "cost_uncertainty_summary.csv", index=False)
    frequency = samples["LeastCostDesign"].value_counts(normalize=True).rename("ShareOfSamplesOptimal").reset_index()
    frequency.columns = ["Design", "ShareOfSamplesOptimal"]
    frequency.to_csv(RESULTS / "cost_uncertainty_design_frequency.csv", index=False)

    ranges = {**{name: UNC[name] for name in CONTINUOUS}, "irradiance_scale": [min(scales), max(scales)]}
    base_total = float(base_costs.loc[base_costs["design"] == base_design, "total"].iloc[0])
    tornado = []
    for name, (low, high) in ranges.items():
        values = []
        for setting in (low, high):
            costs = averaged({**base, name: setting})
            values.append(float(costs.loc[costs["design"] == base_design, "total"].iloc[0]))
        tornado.append({"Parameter": name, "Low": low, "High": high, "TotalAtLowAUDm": values[0],
                        "TotalAtHighAUDm": values[1], "SwingAUDm": abs(values[1] - values[0])})
    price_years = []
    for year in years:
        costs = evaluate(records, base, year)
        price_years.append(float(costs.loc[costs["design"] == base_design, "total"].iloc[0]))
    tornado.append({"Parameter": "price_year", "Low": years[int(np.argmin(price_years))], "High": years[int(np.argmax(price_years))],
                    "TotalAtLowAUDm": min(price_years), "TotalAtHighAUDm": max(price_years), "SwingAUDm": max(price_years) - min(price_years)})
    tornado_frame = pd.DataFrame(tornado).sort_values("SwingAUDm")
    tornado_frame.to_csv(RESULTS / "cost_uncertainty_tornado.csv", index=False)

    fig, axes = plt.subplots(1, 3, figsize=(18, 5.4))
    axes[0].hist(least, bins=30, color="#0072B2", edgecolor="white")
    for q, colour in ((0.1, "#D55E00"), (0.5, "#009E73"), (0.9, "#7A5195")):
        axes[0].axvline(least.quantile(q), color=colour, linewidth=2, label=f"P{int(q * 100)}: {least.quantile(q):.0f}")
    axes[0].set_xlabel("Least-cost total annual cost (AUD m)")
    axes[0].set_ylabel("Samples")
    axes[0].set_title(f"Cost uncertainty ({len(samples)} samples)")
    axes[0].legend(fontsize=8)
    top = frequency.head(6).iloc[::-1]
    axes[1].barh(top["Design"], top["ShareOfSamplesOptimal"] * 100, color="#009E73")
    axes[1].set_xlabel("Share of samples where design is least-cost (%)")
    axes[1].set_title("Design robustness")
    axes[1].tick_params(axis="y", labelsize=7)
    y = np.arange(len(tornado_frame))
    axes[2].barh(y, tornado_frame["TotalAtLowAUDm"] - base_total, left=base_total, color="#D55E00", label="low setting")
    axes[2].barh(y, tornado_frame["TotalAtHighAUDm"] - base_total, left=base_total, color="#0072B2", label="high setting")
    axes[2].axvline(base_total, color="black", linewidth=1)
    axes[2].set_yticks(y, tornado_frame["Parameter"], fontsize=8)
    axes[2].set_xlabel("Total annual cost of base design (AUD m)")
    axes[2].set_title(f"Tornado: {base_design}")
    axes[2].legend(fontsize=8)
    fig.suptitle(f"{site['name']}: economic uncertainty at {limit:.0f} MW import limit (assumed input ranges)")
    fig.tight_layout()
    save_figure(fig, RESULTS, "cost_uncertainty")
    print(summary.to_string(index=False))
    print(frequency.head(5).to_string(index=False))
    print(tornado_frame[["Parameter", "SwingAUDm"]].round(1).to_string(index=False))


if __name__ == "__main__":
    main()

"""Validate the solar model against metered output of real Queensland farms.

For each farm in solar_validation, NASA POWER weather for the farm's
location and the same months as the AEMO SCADA data is run through the
study's pvlib model, and compared hour by hour with the farm's metered
output. To compare like with like:

* AC capacity is estimated as the 99.5th percentile of hourly output;
* hours with QLD1 spot price <= 0 are excluded (economic curtailment);
* days with metered energy < 30% of modelled energy are excluded (outages).

The ratio of metered to modelled capacity factor on the remaining hours
indicates whether the NASA-driven model over- or under-predicts output, and
is used to calibrate solar.irradiance_scale (the validation itself always runs
on unscaled NASA POWER irradiance).
Mounting type and DC/AC ratio of the farms are assumed (VERIFY).
"""

from __future__ import annotations

import io
import time
import urllib.parse
import urllib.request

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from download_aemo_prices import fetch_month
from download_nasa_power_weather import PARAMETERS, TIME_COLUMNS
from studylib.config import INPUTS, RESULTS, WEATHER_DIR, load_config
from studylib.io import save_figure, write_csv
from studylib.weather import RENAME, solar_ac_per_mw


CONFIG = load_config()
VALIDATION = CONFIG["solar_validation"]


def nasa_weather(duid: str, latitude: float, longitude: float, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    path = WEATHER_DIR / f"validation_{duid}_{start:%Y%m%d}_{end:%Y%m%d}.csv"
    if not path.exists():
        query = urllib.parse.urlencode({
            "parameters": ",".join(PARAMETERS), "community": "RE", "longitude": longitude, "latitude": latitude,
            "start": f"{start:%Y%m%d}", "end": f"{end:%Y%m%d}", "format": "CSV", "header": "false", "time-standard": "LST",
        })
        url = f"https://power.larc.nasa.gov/api/temporal/hourly/point?{query}"
        last_error = None
        for attempt in range(4):
            try:
                with urllib.request.urlopen(url, timeout=300) as response:
                    frame = pd.read_csv(io.StringIO(response.read().decode("utf-8")))
                break
            except Exception as exc:  # network retry boundary
                last_error = exc
                time.sleep(3 * 2**attempt)
        else:
            raise RuntimeError(f"NASA POWER download failed for {duid}: {last_error}")
        frame[TIME_COLUMNS + PARAMETERS].to_csv(path, index=False)
    raw = pd.read_csv(path).replace(-999.0, np.nan)
    weather = raw.rename(columns=RENAME)
    weather["timestamp"] = pd.to_datetime(raw.rename(columns={"YEAR": "year", "MO": "month", "DY": "day", "HR": "hour"})[["year", "month", "day", "hour"]])
    weather = weather.dropna(subset=["ghi", "dni", "dhi", "temp_c", "wind_10m"])
    for column in ("ghi", "dni", "dhi"):
        weather[column] = weather[column].clip(lower=0.0)
    return weather.reset_index(drop=True)


def hourly_prices(start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
    months = pd.period_range(start, end, freq="M")
    frames = [fetch_month("QLD1", period.year, period.month) for period in months]
    raw = pd.concat(frames, ignore_index=True)
    raw["SETTLEMENTDATE"] = pd.to_datetime(raw["SETTLEMENTDATE"])
    raw["timestamp"] = (raw["SETTLEMENTDATE"] - pd.Timedelta(minutes=5)).dt.floor("h")
    return raw.groupby("timestamp")["RRP"].mean()


def main() -> None:
    farms = pd.read_csv(INPUTS / "grid" / "solar_farm_hourly.csv", parse_dates=["timestamp"])
    start, end = farms["timestamp"].min().normalize(), farms["timestamp"].max().normalize()
    prices = hourly_prices(start, end)
    summary, monthly_rows, profiles = [], [], {}
    for farm in VALIDATION["farms"]:
        metered = farms[farms["duid"] == farm["duid"]].set_index("timestamp")["mwh"]
        if metered.empty:
            print(f"{farm['duid']}: no SCADA data, skipped")
            continue
        weather = nasa_weather(farm["duid"], farm["latitude"], farm["longitude"], start, end)
        model_cf, _ = solar_ac_per_mw(weather, farm["latitude"], farm["longitude"])
        frame = pd.DataFrame({"model_cf": model_cf}, index=pd.DatetimeIndex(weather["timestamp"]))
        frame = frame.join(metered.rename("metered_mwh"), how="inner").join(prices.rename("price"), how="left")
        capacity = float(frame["metered_mwh"].quantile(0.995))
        frame["metered_cf"] = frame["metered_mwh"] / capacity
        daily = frame.groupby(frame.index.date)[["metered_cf", "model_cf"]].sum()
        good_days = daily.index[daily["metered_cf"] >= 0.3 * daily["model_cf"]]
        keep = frame[np.isin(frame.index.date, good_days) & (frame["price"] > 0)]
        ratio = float(keep["metered_cf"].sum() / keep["model_cf"].sum())
        daylight = keep[keep["model_cf"] > 0.01]
        r2 = float(np.corrcoef(daylight["metered_cf"], daylight["model_cf"])[0, 1] ** 2)
        summary.append({
            "Duid": farm["duid"], "Farm": farm["name"],
            "PeriodStart": str(frame.index.min().date()), "PeriodEnd": str(frame.index.max().date()),
            "EstimatedACCapacityMW": capacity,
            "MeteredCFAllHours": float(frame["metered_cf"].mean()),
            "ModelCFAllHours": float(frame["model_cf"].mean()),
            "MeteredCFFilteredHours": float(keep["metered_cf"].mean()),
            "ModelCFFilteredHours": float(keep["model_cf"].mean()),
            "MeteredToModelRatio": ratio,
            "HourlyR2Daylight": r2,
            "ShareOfHoursExcludedForPrice": float((frame["price"] <= 0).mean()),
            "DaysExcludedAsOutage": int(len(daily) - len(good_days)),
        })
        monthly = keep.groupby(keep.index.to_period("M"))[["metered_cf", "model_cf"]].mean()
        for period, row in monthly.iterrows():
            monthly_rows.append({"Duid": farm["duid"], "Month": str(period), "MeteredCF": row["metered_cf"], "ModelCF": row["model_cf"]})
        profiles[farm["name"]] = keep.groupby(keep.index.hour)[["metered_cf", "model_cf"]].mean()
        print(f"{farm['name']}: metered/model = {ratio:.3f}, R2 = {r2:.3f}", flush=True)
    write_csv(RESULTS / "solar_validation_summary.csv", summary)
    write_csv(RESULTS / "solar_validation_monthly.csv", monthly_rows)

    table = pd.DataFrame(summary)
    fig, axes = plt.subplots(1, len(profiles) + 1, figsize=(5 * (len(profiles) + 1), 4.6))
    for ax, (name, profile) in zip(axes, profiles.items()):
        ax.plot(profile.index, profile["metered_cf"], color="#222222", marker="o", markersize=3, label="Metered (AEMO SCADA)")
        ax.plot(profile.index, profile["model_cf"], color="#E69F00", marker="o", markersize=3, label="Model (pvlib + NASA POWER)")
        ax.set_title(name, fontsize=9)
        ax.set_xlabel("Hour of day (NEM time)")
        ax.set_ylabel("Mean output / AC capacity")
        ax.grid(alpha=0.25)
    axes[0].legend(fontsize=7)
    last = axes[-1]
    x = np.arange(len(table))
    last.bar(x - 0.2, table["MeteredCFFilteredHours"] * 100, 0.4, label="Metered", color="#222222")
    last.bar(x + 0.2, table["ModelCFFilteredHours"] * 100, 0.4, label="Model", color="#E69F00")
    for i, ratio in enumerate(table["MeteredToModelRatio"]):
        last.text(i, max(table.loc[i, ["MeteredCFFilteredHours", "ModelCFFilteredHours"]]) * 100 + 0.5, f"x{ratio:.2f}", ha="center", fontsize=8)
    last.set_xticks(x, table["Duid"])
    last.set_ylabel("Capacity factor, filtered hours (%)")
    last.set_title("Metered vs modelled capacity factor")
    last.legend(fontsize=7, loc="lower right")
    fig.suptitle("Solar model validation against operating Central Queensland farms (curtailment and outage hours removed)")
    fig.tight_layout()
    save_figure(fig, RESULTS, "solar_validation")
    print(table[["Farm", "MeteredCFFilteredHours", "ModelCFFilteredHours", "MeteredToModelRatio", "HourlyR2Daylight"]].round(3).to_string(index=False))


if __name__ == "__main__":
    main()

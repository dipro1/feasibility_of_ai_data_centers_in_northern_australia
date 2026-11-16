"""Price-aware PV + BESS dispatch by rolling linear programming.

Each day is optimised over a horizon of `horizon + lookahead` hours with
perfect foresight of prices, load and PV, and the first `horizon` hours are
committed (state of charge carried forward). Objective:

    min  sum price*(grid_to_load + grid_to_battery)
       + degradation_cost * discharge
       + penalty * import_above_limit
       + epsilon * curtailment

subject to load and PV balances, battery energy balance, power and SOC
limits, and the import limit (soft, heavily penalised so infeasible hours
are reported rather than failing). This is a benchmark of what a
price-aware controller could achieve, not a forecast-based controller.
"""

from __future__ import annotations

import math

import numpy as np
from scipy.optimize import linprog
from scipy.sparse import coo_matrix

from .config import load_config
from .dispatch import DispatchResult


PENALTY_PER_MWH = 1e5
RESERVE_MARGIN = 1.2  # forecast-controller reserve = 120% of the forecast limit-duty energy
PV_FORECAST_CREDIT = 0.5  # only half the persistence PV forecast is trusted when sizing the reserve
CURTAIL_EPSILON = 1e-3
# Variable blocks, each of length n.
PV_LOAD, PV_CHARGE, CURTAIL, GRID_LOAD, GRID_CHARGE, DISCHARGE, SOC, SLACK = range(8)


def _solve_window(demand, pv, price, power, energy, limit, allow_grid, soc0, eta, soc_min, degradation):
    n = len(demand)
    nvar = 8 * n

    def index(block: int, t: int) -> int:
        return block * n + t

    rows, cols, vals, b_eq = [], [], [], []
    row = 0
    for t in range(n):  # load balance
        for block in (PV_LOAD, DISCHARGE, GRID_LOAD):
            rows.append(row); cols.append(index(block, t)); vals.append(1.0)
        b_eq.append(demand[t]); row += 1
    for t in range(n):  # PV balance
        for block in (PV_LOAD, PV_CHARGE, CURTAIL):
            rows.append(row); cols.append(index(block, t)); vals.append(1.0)
        b_eq.append(pv[t]); row += 1
    for t in range(n):  # battery energy balance
        rows += [row, row, row, row]
        cols += [index(SOC, t), index(PV_CHARGE, t), index(GRID_CHARGE, t), index(DISCHARGE, t)]
        vals += [1.0, -eta, -eta, 1.0 / eta]
        if t > 0:
            rows.append(row); cols.append(index(SOC, t - 1)); vals.append(-1.0)
            b_eq.append(0.0)
        else:
            b_eq.append(soc0)
        row += 1
    a_eq = coo_matrix((vals, (rows, cols)), shape=(row, nvar)).tocsr()

    rows, cols, vals, b_ub = [], [], [], []
    row = 0
    for t in range(n):  # import limit (soft)
        rows += [row, row, row]
        cols += [index(GRID_LOAD, t), index(GRID_CHARGE, t), index(SLACK, t)]
        vals += [1.0, 1.0, -1.0]
        b_ub.append(limit); row += 1
    for t in range(n):  # charging power
        rows += [row, row]
        cols += [index(PV_CHARGE, t), index(GRID_CHARGE, t)]
        vals += [1.0, 1.0]
        b_ub.append(power); row += 1
    rows.append(row); cols.append(index(SOC, n - 1)); vals.append(-1.0)  # end SOC >= start SOC
    b_ub.append(-min(soc0, energy)); row += 1
    a_ub = coo_matrix((vals, (rows, cols)), shape=(row, nvar)).tocsr()

    cost = np.zeros(nvar)
    cost[GRID_LOAD * n:(GRID_LOAD + 1) * n] = price
    cost[GRID_CHARGE * n:(GRID_CHARGE + 1) * n] = price
    cost[DISCHARGE * n:(DISCHARGE + 1) * n] = degradation
    cost[SLACK * n:(SLACK + 1) * n] = PENALTY_PER_MWH
    cost[CURTAIL * n:(CURTAIL + 1) * n] = CURTAIL_EPSILON

    bounds = [(0, None)] * nvar
    for t in range(n):
        bounds[index(DISCHARGE, t)] = (0, power)
        bounds[index(GRID_CHARGE, t)] = (0, power if allow_grid else 0)
        bounds[index(SOC, t)] = (soc_min, energy)
    result = linprog(cost, A_ub=a_ub, b_ub=b_ub, A_eq=a_eq, b_eq=b_eq, bounds=bounds, method="highs")
    if result.status != 0:
        raise RuntimeError(f"Dispatch LP failed: {result.message}")
    x = result.x.reshape(8, n)
    return x


def optimal_dispatch(
    demand: np.ndarray,
    pv: np.ndarray,
    price: np.ndarray,
    bess_power_mw: float,
    bess_energy_mwh: float,
    import_limit_mw: float,
    allow_grid_charging: bool,
) -> DispatchResult:
    config = load_config()
    resource = config["renewable_bess"]
    horizon = int(config["price_aware_dispatch"]["horizon_hours"])
    lookahead = int(config["price_aware_dispatch"]["lookahead_hours"])
    degradation = float(config["economics"]["battery_degradation_cost_per_mwh"])
    eta = math.sqrt(resource["round_trip_efficiency"])
    soc_min = resource["minimum_soc_fraction"] * bess_energy_mwh
    soc = initial_soc = resource["initial_soc_fraction"] * bess_energy_mwh

    n = len(demand)
    out = {name: np.zeros(n) for name in ("pv_load", "pv_charge", "curtail", "grid_load", "grid_charge", "discharge", "soc")}
    for start in range(0, n, horizon):
        end = min(start + horizon + lookahead, n)
        commit = min(horizon, n - start)
        x = _solve_window(demand[start:end], pv[start:end], price[start:end], bess_power_mw, bess_energy_mwh,
                          import_limit_mw, allow_grid_charging, soc, eta, soc_min, degradation)
        for name, block in (("pv_load", PV_LOAD), ("pv_charge", PV_CHARGE), ("curtail", CURTAIL),
                            ("grid_load", GRID_LOAD), ("grid_charge", GRID_CHARGE), ("discharge", DISCHARGE), ("soc", SOC)):
            out[name][start:start + commit] = x[block][:commit]
        soc = float(out["soc"][start + commit - 1])

    # Remove numerical noise from the LP solution.
    for name in out:
        out[name] = np.where(np.abs(out[name]) < 1e-7, 0.0, out[name])
    grid = out["grid_load"] + out["grid_charge"]
    return DispatchResult(
        grid=grid,
        pv_to_load=out["pv_load"],
        pv_charge=out["pv_charge"],
        grid_charge=out["grid_charge"],
        discharge=out["discharge"],
        soc=out["soc"],
        exceedance=np.maximum(grid - import_limit_mw, 0.0),
        curtailed=out["curtail"],
        initial_soc=initial_soc,
    )


def forecast_dispatch(
    demand: np.ndarray,
    pv: np.ndarray,
    price: np.ndarray,
    bess_power_mw: float,
    bess_energy_mwh: float,
    import_limit_mw: float,
    allow_grid_charging: bool,
) -> DispatchResult:
    """Price-aware dispatch using only forecasts available a day ahead.

    Each day is planned with the same LP but on persistence forecasts
    (yesterday's prices and PV for the same hours; demand is the facility's
    own scheduled load and treated as known). The planned battery power is
    then applied to the real day, with a real-time safety rule: surplus PV is
    stored first, and the battery always discharges (or stops charging) so
    that grid import never exceeds the limit while energy remains.
    """
    config = load_config()
    resource = config["renewable_bess"]
    horizon = int(config["price_aware_dispatch"]["horizon_hours"])
    lookahead = int(config["price_aware_dispatch"]["lookahead_hours"])
    degradation = float(config["economics"]["battery_degradation_cost_per_mwh"])
    eta = math.sqrt(resource["round_trip_efficiency"])
    soc_min = resource["minimum_soc_fraction"] * bess_energy_mwh
    soc = initial_soc = resource["initial_soc_fraction"] * bess_energy_mwh

    n = len(demand)
    price_forecast = np.concatenate([price[:24], price[:-24]])
    pv_forecast = np.concatenate([pv[:24], pv[:-24]])
    out = {name: np.zeros(n) for name in ("pv_load", "pv_charge", "curtail", "grid", "grid_charge", "discharge", "soc")}
    for start in range(0, n, horizon):
        end = min(start + horizon + lookahead, n)
        commit = min(horizon, n - start)
        plan = _solve_window(demand[start:end], pv_forecast[start:end], price_forecast[start:end], bess_power_mw,
                             bess_energy_mwh, import_limit_mw, allow_grid_charging, soc, eta, soc_min, degradation)
        planned_net = plan[PV_CHARGE] + plan[GRID_CHARGE] - plan[DISCHARGE]  # + = charging
        # Energy reserve for import-limit duty: forecast shortfall above the limit for
        # the rest of the committed day, plus a margin. Arbitrage may not dip into it.
        shortfall = np.maximum(demand[start:start + commit] - PV_FORECAST_CREDIT * pv_forecast[start:start + commit]
                               - import_limit_mw, 0.0)
        reserve = soc_min + RESERVE_MARGIN * np.cumsum(shortfall[::-1])[::-1] / eta
        for k in range(commit):
            t = start + k
            direct = min(demand[t], pv[t])
            surplus = pv[t] - direct
            remaining = demand[t] - direct
            room = max(bess_energy_mwh - soc, 0.0) / eta
            available = max(soc - soc_min, 0.0) * eta
            pv_charge = min(surplus, bess_power_mw, room)
            surplus -= pv_charge
            charge_extra = max(planned_net[k] - pv_charge, 0.0) if allow_grid_charging else 0.0
            charge_extra = min(charge_extra, bess_power_mw - pv_charge, max(room - pv_charge, 0.0),
                               max(import_limit_mw - remaining, 0.0))
            need = max(remaining - import_limit_mw, 0.0)
            discretionary = max(soc - reserve[k], 0.0) * eta
            discharge = min(max(-planned_net[k], 0.0), bess_power_mw, available, remaining, need + discretionary)
            # Safety rule: never exceed the import limit while energy remains.
            if remaining - discharge > import_limit_mw:
                discharge = min(remaining - import_limit_mw, bess_power_mw, available)
            if discharge > 0:
                charge_extra = 0.0
            soc += (pv_charge + charge_extra) * eta - discharge / eta
            out["pv_load"][t] = direct
            out["pv_charge"][t] = pv_charge
            out["curtail"][t] = surplus
            out["grid_charge"][t] = charge_extra
            out["discharge"][t] = discharge
            out["grid"][t] = remaining - discharge + charge_extra
            out["soc"][t] = soc
    grid = out["grid"]
    return DispatchResult(
        grid=grid,
        pv_to_load=out["pv_load"],
        pv_charge=out["pv_charge"],
        grid_charge=out["grid_charge"],
        discharge=out["discharge"],
        soc=out["soc"],
        exceedance=np.maximum(grid - import_limit_mw, 0.0),
        curtailed=out["curtail"],
        initial_soc=initial_soc,
    )

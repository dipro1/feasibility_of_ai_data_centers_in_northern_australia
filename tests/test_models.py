"""Fast unit tests for the shared model code. Run with:  python -m pytest -q"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from studylib import network as net  # noqa: E402
from studylib.config import load_config, ner_minimum_pf, read_csv_rows, SITE_CASES_PATH  # noqa: E402
from studylib.dispatch import dispatch  # noqa: E402
from studylib.economics import annualised_resource_cost, capital_recovery_factor  # noqa: E402
from studylib.facility import facility_load  # noqa: E402


def synthetic_weather(hours: int = 24 * 14) -> pd.DataFrame:
    timestamps = pd.date_range("2024-01-01", periods=hours, freq="h")
    hour = timestamps.hour.to_numpy()
    return pd.DataFrame(
        {
            "timestamp": timestamps,
            "temp_c": 22 + 10 * np.clip(np.sin((hour - 6) / 24 * 2 * np.pi), 0, None),
            "wet_bulb_c": np.full(hours, 20.0),
        }
    )


def test_config_is_valid():
    config = load_config()
    assert config["connection"]["target_mw"] > 0


def test_ner_power_factor_bands():
    assert ner_minimum_pf(275) == pytest.approx(0.98)
    assert ner_minimum_pf(132) == pytest.approx(0.96)
    assert ner_minimum_pf(22) == pytest.approx(0.90)


@pytest.mark.parametrize("allow_grid", [False, True])
def test_dispatch_energy_balance_and_soc_limits(allow_grid):
    rng = np.random.default_rng(1)
    demand = 180 + 40 * rng.random(500)
    pv = np.clip(250 * np.sin(np.arange(500) / 24 * 2 * np.pi), 0, None)
    power, energy, limit = 60.0, 240.0, 200.0
    result = dispatch(demand, pv, power, energy, limit, allow_grid)
    resource = load_config()["renewable_bess"]
    eta = math.sqrt(resource["round_trip_efficiency"])

    # Load balance every hour: load = PV to load + discharge + (grid - grid charging).
    np.testing.assert_allclose(result.pv_to_load + result.discharge + result.grid - result.grid_charge, demand, atol=1e-9)
    # PV balance: PV = to load + to battery + curtailed.
    np.testing.assert_allclose(result.pv_to_load + result.pv_charge + result.curtailed, pv, atol=1e-9)
    # Battery energy balance over the horizon.
    stored = (result.pv_charge.sum() + result.grid_charge.sum()) * eta - result.discharge.sum() / eta
    assert result.soc[-1] - result.initial_soc == pytest.approx(stored, abs=1e-6)
    # SOC and power limits respected.
    assert result.soc.min() >= resource["minimum_soc_fraction"] * energy - 1e-9
    assert result.soc.max() <= energy + 1e-9
    assert (result.pv_charge + result.grid_charge).max() <= power + 1e-9
    assert result.discharge.max() <= power + 1e-9
    if not allow_grid:
        assert result.grid_charge.sum() == 0


def test_no_battery_means_import_equals_net_load():
    demand = np.array([100.0, 250.0, 50.0])
    pv = np.array([0.0, 30.0, 80.0])
    result = dispatch(demand, pv, 0.0, 0.0, 200.0, True)
    np.testing.assert_allclose(result.grid, [100.0, 220.0, 0.0])
    np.testing.assert_allclose(result.curtailed, [0.0, 0.0, 30.0])


def test_facility_load_profiles():
    weather = synthetic_weather()
    training = facility_load(weather, "training")
    inference = facility_load(weather, "inference")
    facility = load_config()["facility"]
    assert training["pue"].min() >= facility["base_pue"] - 1e-12
    assert training["facility_mw"].mean() > inference["facility_mw"].mean()
    with pytest.raises(ValueError):
        facility_load(weather, "unknown")


def test_thevenin_analytic_matches_known_case():
    # Bouldercombe N-1 at 0.95 PF, 1.0 pu: earlier OpenDSS study gave 409 MW last pass.
    fault = net.fault_level_mva(275, 8.6)
    assert net.analytic_voltage_pu(275, fault, 10, 409, 0.95) >= 0.95
    assert net.analytic_voltage_pu(275, fault, 10, 410, 0.95) < 0.95


def test_voltage_beyond_nose_is_nan():
    fault = net.fault_level_mva(22, 6.9)
    nose = net.maximum_transfer_mw(22, fault, 10, 0.9)
    assert math.isnan(net.analytic_voltage_pu(22, fault, 10, nose * 1.01, 0.9))
    assert not math.isnan(net.analytic_voltage_pu(22, fault, 10, nose * 0.99, 0.9))


def test_every_site_case_has_valid_inputs():
    for row in read_csv_rows(SITE_CASES_PATH):
        assert float(row["voltage_kv"]) > 0
        assert float(row["fault_current_3ph_ka"]) > 0
        assert row["fault_current_basis"] in {"minimum", "maximum"}


def test_capital_recovery_and_costs():
    assert capital_recovery_factor(0.0, 20) == pytest.approx(0.05)
    assert capital_recovery_factor(0.07, 30) == pytest.approx(0.0806, abs=1e-4)
    costs = annualised_resource_cost(100, 50, 200)
    assert costs["PvCapexAUDm"] == pytest.approx(100 * 1000 * load_config()["economics"]["pv_capex_per_kw"] / 1e6)
    assert costs["AnnualisedResourceCostAUDm"] > 0


def test_frequency_model_matches_analytic_rocof_and_settled_deviation():
    from studylib import dynamics as dyn

    system = dyn.FrequencySystem(demand_mw=10000, inertia_mws=40000, pfr_mw_per_hz=2000,
                                 pfr_headroom_mw=1e9, governor_time_constant_s=5, load_damping_percent_per_hz=1.5)
    time, frequency = dyn.simulate_frequency(system, 300.0, 50.0, 0.0, 120.0, 0.001, 1.0)
    early = (time > 1.0) & (time <= 1.05)
    measured_rocof = (frequency[early][-1] - frequency[early][0]) / (time[early][-1] - time[early][0])
    assert measured_rocof == pytest.approx(dyn.initial_rocof(system, 300.0, 50.0), rel=0.03)
    expected_settled = -300.0 / (2000 + 0.015 * 10000)
    assert frequency[-1] - 50.0 == pytest.approx(expected_settled, rel=0.01)
    assert dyn.quasi_steady_deviation(system, 300.0, 0.0) == pytest.approx(expected_settled, rel=1e-6)


def test_voltage_behind_impedance_limits():
    z = net.thevenin_impedance(275, net.fault_level_mva(275, 8.6), 10)
    assert net.voltage_behind_impedance_pu(275, z, 0j) == pytest.approx(1.0)
    assert net.voltage_behind_impedance_pu(275, z, 0j, source_pu=1.03) == pytest.approx(1.03)
    assert math.isnan(net.voltage_behind_impedance_pu(275, z, complex(5000, 0)))


def test_price_aware_lp_never_costs_more_than_rule_and_respects_limits():
    from studylib.optimal_dispatch import optimal_dispatch

    hours = 96
    t = np.arange(hours)
    demand = 190 + 30 * np.sin(t / 24 * 2 * np.pi)
    pv = np.clip(220 * np.sin((t % 24 - 6) / 12 * np.pi), 0, None)
    price = 80 + 120 * np.sin((t % 24 - 15) / 24 * 2 * np.pi) ** 2
    rule = dispatch(demand, pv, 50.0, 200.0, 210.0, True)
    lp = optimal_dispatch(demand, pv, price, 50.0, 200.0, 210.0, True)
    assert (lp.grid * price).sum() <= (rule.grid * price).sum() + 1e-6
    np.testing.assert_allclose(lp.pv_to_load + lp.discharge + lp.grid - lp.grid_charge, demand, atol=1e-5)
    assert lp.exceedance.max() <= 1e-6
    assert lp.soc.min() >= 0.1 * 200.0 - 1e-6 and lp.soc.max() <= 200.0 + 1e-6


def test_network_charges_and_water_model():
    from studylib.economics import network_charges
    from studylib.facility import cooling_water_litres

    timestamps = pd.Series(pd.date_range("2024-01-01", periods=24 * 60, freq="h"))
    grid = np.full(len(timestamps), 100.0)
    charges = network_charges(grid, timestamps, "rockhampton", 1)
    tariff = load_config()["economics"]["network_tariff"]
    expected_demand = 2 * 100_000 * tariff["locational_price_per_kw_month"]["rockhampton"] / 1.1 / 1e6
    assert charges["LocationalDemandChargeAUDm"] == pytest.approx(expected_demand)
    assert network_charges(grid, timestamps, "karratha", 1)["NetworkChargesAUDm"] == 0.0

    weather = pd.DataFrame({"temp_c": [10.0, 40.0]})
    water = cooling_water_litres(weather, {"facility_mw": np.array([100.0, 100.0])})
    assert water[0] == 0.0
    cfg = load_config()["facility"]["water"]
    evaporation = 100 * 3600 / cfg["latent_heat_mj_per_kg"]
    assert water[1] == pytest.approx(evaporation * (1 + 1 / (cfg["cycles_of_concentration"] - 1) + cfg["drift_fraction"]))


def test_pst_curve_interpolation_hits_configured_points():
    from run_oscillation_screening import pst1_limit_percent

    osc = load_config()["oscillation_screening"]
    for rate, limit in zip(osc["pst1_curve_changes_per_min"], osc["pst1_curve_delta_v_percent"]):
        assert pst1_limit_percent(rate) == pytest.approx(limit, rel=1e-9)


def test_multibus_fault_levels_match_opendss_fault_study():
    from studylib import multibus as mb

    network = mb.load_network(load_config()["multibus"]["model_dir"], 10.0)
    mb.fit_sources(network)
    numpy_levels = mb.fault_levels(network)
    dss_levels = mb.opendss_fault_levels(network)
    for bus, level in numpy_levels.items():
        assert dss_levels[bus] == pytest.approx(level, rel=1e-3)


def test_mixed_profile_is_weighted_average():
    weather = synthetic_weather()
    share = load_config()["facility"]["mixed_training_share"]
    training = facility_load(weather, "training")["it_mw"]
    inference = facility_load(weather, "inference")["it_mw"]
    mixed = facility_load(weather, "mixed")["it_mw"]
    np.testing.assert_allclose(mixed, share * training + (1 - share) * inference)


def test_cfe_score_bounds_and_simple_case(monkeypatch):
    from studylib import economics

    timestamps = pd.Series(pd.date_range("2025-01-01", periods=4, freq="h"))
    monkeypatch.setattr(economics, "hourly_grid_intensity",
                        lambda ts, key: (np.full(len(ts), 0.5), np.full(len(ts), 0.25)))
    demand = np.full(4, 100.0)
    pv = np.array([0.0, 50.0, 100.0, 150.0])
    result = dispatch(demand, pv, 0.0, 0.0, 1000.0, False)
    metrics = economics.time_matched_metrics(result, demand, timestamps, "QLD1", 1)
    # PV serves 250 MWh directly; grid serves 150 MWh of which 25% is clean.
    assert metrics["CfeScore"] == pytest.approx((250 + 0.25 * 150) / 400)
    assert metrics["HourlyMatchedEmissionsKt"] == pytest.approx(150 * 0.5 / 1000)


def test_backup_redundancy_maths():
    from run_backup_reliability import expected_shortfall_mw, probability_short, redundancy_for_target

    assert probability_short(10, 10, 0.0) == pytest.approx(0.0)
    assert probability_short(1, 1, 0.1) == pytest.approx(0.1)
    assert expected_shortfall_mw(1, 1, 0.1, 3.0) == pytest.approx(0.3)
    spare = redundancy_for_target(80, 0.01, 0.001)
    assert probability_short(80 + spare, 80, 0.01) <= 0.001 < probability_short(80 + spare - 1, 80, 0.01)


def test_forecast_controller_energy_balance_and_limits():
    from studylib.optimal_dispatch import forecast_dispatch

    hours = 24 * 6
    t = np.arange(hours)
    demand = 190 + 30 * np.sin(t / 24 * 2 * np.pi)
    pv = np.clip(260 * np.sin((t % 24 - 6) / 12 * np.pi), 0, None)
    price = 80 + 150 * np.sin((t % 24 - 15) / 24 * 2 * np.pi) ** 2
    result = forecast_dispatch(demand, pv, price, 150.0, 600.0, 230.0, True)
    resource = load_config()["renewable_bess"]
    eta = math.sqrt(resource["round_trip_efficiency"])
    np.testing.assert_allclose(result.pv_to_load + result.discharge + result.grid - result.grid_charge, demand, atol=1e-6)
    np.testing.assert_allclose(result.pv_to_load + result.pv_charge + result.curtailed, pv, atol=1e-6)
    stored = (result.pv_charge.sum() + result.grid_charge.sum()) * eta - result.discharge.sum() / eta
    assert result.soc[-1] - result.initial_soc == pytest.approx(stored, abs=1e-6)
    assert result.soc.min() >= resource["minimum_soc_fraction"] * 600.0 - 1e-6
    assert result.exceedance.max() <= 1e-6


def test_hub_distance_is_great_circle_to_nearest_hub():
    from run_multisite_comparison import hub_distance_km

    km, hub = hub_distance_km(-27.4698, 153.0251)
    assert hub == "Brisbane" and km == pytest.approx(0.0, abs=1e-6)
    km, hub = hub_distance_km(-23.3781, 150.5136)  # Rockhampton
    assert hub == "Brisbane" and 500 < km < 560


def test_topology_builder_finds_direct_connections():
    import networkx as nx
    from build_cq_topology import direct_connections

    graph = nx.Graph()
    coords = {1: (-23.0, 150.0), 2: (-23.0, 150.1), 3: (-23.0, 150.2), 4: (-23.0, 150.3)}
    for node, (lat, lon) in coords.items():
        graph.add_node(node, lat=lat, lon=lon)
    for a, b in ((1, 2), (2, 3), (3, 4)):
        graph.add_edge(a, b, km=10.2, name="", circuits="")
    centres = {"A": coords[1], "B": coords[3], "C": coords[4]}
    found = direct_connections(graph, centres)
    pairs = set(zip(found["from_bus"], found["to_bus"]))
    assert pairs == {("A", "B"), ("B", "C")}  # A-C is not direct: it passes through B


def test_dc_power_flow_balances_and_lodf_matches_rerun():
    from run_hourly_power_flow import DcModel
    from studylib import multibus as mb

    cfg = load_config()["multibus"]
    network = mb.load_network(cfg["model_dir"], cfg["source_xr"])
    mb.fit_sources(network)
    model = DcModel(network)
    rng = np.random.default_rng(1)
    injection = rng.normal(0, 200, size=(len(model.buses), 3))
    flows = model.flows(injection)
    # Net injection leaves through the external ties.
    ties = [k for k, e in enumerate(model.ids) if e.startswith("EXT_")]
    np.testing.assert_allclose(flows[ties].sum(axis=0), injection.sum(axis=0), rtol=1e-9, atol=1e-6)
    # LODF prediction equals a re-solve with the line removed (a meshed line).
    out = "STAN_CALV_1"
    k = model.ids.index(out)
    predicted = flows + model.lodf[:, k][:, None] * flows[k][None, :]
    network.branches = [b for b in network.branches if b["branch_id"] != out]
    rerun = DcModel(network)
    actual = rerun.flows(injection)
    for i, element in enumerate(rerun.ids):
        np.testing.assert_allclose(predicted[model.ids.index(element)], actual[i], atol=1e-6)


def test_largest_continuous_excess():
    from run_trace_load_profile import largest_excess_mwh

    demand = np.array([190, 210, 230, 190, 250, 260, 255, 100.0])
    assert largest_excess_mwh(demand, 200.0) == pytest.approx(50 + 60 + 55)


def test_price_response_pauses_only_above_value():
    from run_flexible_load import FLEX, price_response

    load = {"it_mw": np.full(4, 180.0), "pue": np.full(4, 1.2), "facility_mw": np.full(4, 216.0)}
    price = np.array([50.0, 999.0, 1001.0, 5000.0])
    demand, paused = price_response(load, price, 1000.0)
    share = FLEX["flexible_it_share"]
    np.testing.assert_allclose(paused, [0, 0, share * 180, share * 180])
    np.testing.assert_allclose(demand, 216.0 - paused * 1.2)


def test_gpu_trace_hourly_allocation():
    from build_gpu_trace_profile import hourly_allocation

    tasks = pd.DataFrame({"start_time": [0.0, 1800.0], "end_time": [3600.0, 7200.0],
                          "plan_gpu": [100.0, 50.0], "inst_num": [2.0, 4.0]})
    hourly = hourly_allocation(tasks)
    # Hour 0: 2 GPUs all hour + 2 GPUs for half an hour; hour 1: 2 GPUs all hour.
    np.testing.assert_allclose(hourly.to_numpy()[:2], [3.0, 2.0])

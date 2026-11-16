# Results interpretation

Exact numbers are generated into `results/AUTOMATED_STUDY_REPORT.md` and
`results/thesis_results_table.csv` on every run. This document explains how
to read them. Values quoted below are from the October 2026 run; if you
change any input, quote the regenerated report instead.

## 1. What the network screens can and cannot say

| Result class | Says | Does not say |
|---|---|---|
| 275 kV Thevenin screen | Whether the bus is strong enough (voltage, SCR, PV-curve margin, voltage step) for the target load | Whether lines/transformers have thermal headroom. Thermal headroom cannot be estimated from fault level; it needs line and transformer ratings, existing flows and N-1 load-flow results from Powerlink |
| 22 kV upper bound | The most a distribution-level connection could take on published spare capacity | Anything near data-centre scale; these are included to show why transmission connection is required |
| Karratha ring | How firm N-1 capacity depends on unknown impedance, source strength, PF and voltage | Real Horizon Power capacity, or NWIS frequency/adequacy limits |

**The 275 kV results are dominated by assumptions.** With the NER-banded base
PF (0.98 at 275 kV) and a 1.00 pu source, Bouldercombe N-1 passes about
530 MW. The same bus at 0.95 PF passes about 409 MW, and at unity PF with a
1.05 pu source it passes over 1,400 MW. Report the sensitivity grid
(`regional_pf_voltage_sensitivity.png`), not a single number.

**Short-circuit ratio** at the 245 MW target is about 17 at Bouldercombe and
Calliope River under N-1 (strong), about 6 at Larcom Creek under N-1
(moderate) and about 5 at Karratha (weak). Converter-dominated loads at an SCR
below roughly 3-5 typically need detailed stability studies.

**Larcom Creek is a useful finding.** It looks as strong as Calliope River
intact, but its published minimum N-1 fault level (3.3 kA) cuts the
voltage-limited capacity to about 200 MW, below the target. Gladstone
connections should therefore be screened at Calliope River, or Larcom Creek
should be paired with network reinforcement.

**Load-trip voltage step.** Losing the whole facility at a 275 kV node
produces a step of roughly 1.7-2.1%, inside the 3% screening limit. The limit
is cited to NER S5.1.5 (voltage fluctuations) and S5.3.7 (fluctuating loads);
3% is an initial-check assumption until Powerlink confirms the limit for this
connection. At
Larcom Creek under N-1 the step is about 7%, which fails. A sudden
data-centre trip (as seen in real large-load events) is therefore a binding
issue only at weak nodes.

## 2. Pilbara

The reduced ring's firm N-1 capacity is roughly 50-90 MW across plausible
assumptions. It never approaches the 245 MW target. The upper end is a hard
ceiling set by the 157 MVA line rating (about 94 MW added load), not a
statistical result. The tornado chart shows that **source strength** is the
only parameter that moves the answer materially, so it is the single most
valuable data item to request from Horizon Power.

For the NWIS the binding constraint is likely to be at system level. A
~250 MW load is a large fraction of the whole isolated system, so frequency
stability, reserve and generation adequacy must be raised with Horizon
Power and the Pilbara ISO.

## 2a. Dynamic event screening

Following supervisor guidance, each connection point was tested at minimum
(published minimum N-1 fault level, low inertia) and maximum (published
maximum fault level, high inertia) operating conditions for: sudden load
loss and pickup, a training-load swing, a PV trip, a BESS trip and a network
outage (intact to N-1).

- **Bouldercombe and Calliope River pass every event** in both conditions.
  The worst voltage step is about 2% (full load loss or pickup at minimum
  fault level). A network outage under full load causes only about 0.35%.
- **Larcom Creek fails** the full-load pickup, PV-trip and network-outage
  events (4.6-6.3% steps). At its N-1 fault level the full load is already
  below 0.95 pu before any event.
- **Pilbara points have no power-flow solution at 245 MW**, so the dynamic
  question does not arise until the network is reinforced.
- **Frequency, NEM:** the largest event (245 MW) is about one third of the
  largest credible contingency already covered by contingency FCAS (about
  750 MW, VERIFY). With assumed inertia it stays inside the 49.5-50.5 Hz
  containment band in both conditions. A Queensland-islanded case (QNI
  separation) is not modelled and should be checked in the detailed study.
- **Frequency, NWIS:** the facility is several times larger than the
  assumed largest credible contingency. Most events drive frequency beyond
  the linear model's validity, meaning load shedding or generator protection
  would operate. The robust output is the requirement: holding RoCoF to
  1 Hz/s on a full-facility trip needs about 6,100 MWs of system inertia, and
  containment needs roughly 290-500 MW/Hz of primary response, regardless of
  the assumed values.

Voltage recovery times depend directly on the assumed 20 s voltage-control
time constant and are illustrative only. This screening shows which cases need
RMS/EMT study in PowerFactory or PSS/E with network-owner dynamic models; it
does not replace that study.

## 2b. System strength, oscillation and multi-site interaction

- **System-strength headroom.** At Bouldercombe and Calliope River a 245 MW
  facility needs about 72% of today's minimum N-1 fault level, so about 28% of
  system strength could be lost (for example to coal retirement) before the
  screen fails; the 3% trip-step criterion binds first. A 500 MW stage would
  not pass even today. Larcom Creek cannot host 245 MW at today's level.
- **Training-load oscillation.** Repetitive swings above roughly 10% of IT
  capacity at fast periods would exceed flicker planning levels even at the
  strong 275 kV nodes, and swings with 1-10 s periods sit in the inter-area
  mode band. The facility would need power smoothing (UPS/BESS/supercapacitor)
  or workload-level power management, a design requirement rather than a
  network one.
- **Multi-bus model (OpenStreetMap topology).** Rebuilt on the real 275 kV
  routes (17 direct connections, route lengths from OpenStreetMap, circuit
  counts from the TAPR). Bouldercombe hosts 623 MW intact and 584 MW under the
  worst line outage; Calliope River 610 / 544 MW. Two 245 MW facilities
  together keep every bus above 0.96 pu. Model N-1 fault levels are on
  average 15% above Powerlink's published values, so the single-bus N-1
  numbers remain the conservative reference.
- **Thermal proxy: Bouldercombe is preferable to Calliope River.** Powerlink
  names the Gladstone grid section as thermally limited (54 constrained hours
  in 2024/25). A 245 MW load at Calliope River adds about 150 MW to that
  section and, on the digitised 2024/25 duration curve, raises the hours above
  its effective limit from about 57 to about 330 a year. The same load at
  Bouldercombe draws about 75 MW out of the Gladstone zone and reduces them to
  about 18. Calliope River would therefore rely on generation re-dispatch or
  Powerlink's planned Gladstone Project (about 2030).
- **Queensland islanded: the critical dynamic case.** At AEMO's verified
  minimum inertia threshold (12,000 MWs) the reduced-order screen exceeds the
  51.0 Hz island limit (51.06 Hz) on a full-facility loss. The open-source RMS
  model (ANDES), at about AEMO's secure level (~13,000 MWs) with a governor on
  every unit, peaks at 50.49 Hz and passes. The outcome depends on inertia
  and governor response that only AEMO/Powerlink data can settle, so this is
  the first case for a detailed study, and facility ride-through or a
  staged-trip scheme is a sensible design requirement.
- **RMS study.** All 24 RMS cases (two conditions, two buses, six events)
  are stable and within the frequency bands, with voltage steps of 0.03-2.3% (largest: adjacent-circuit outage at Calliope River, maximum condition)
  and damped electromechanical oscillations (about 0.7 Hz).

## 3. PV, BESS, cost and emissions

- Modelled peak demand is about 243 MW at Rockhampton (training profile:
  200 MW IT x 0.9 utilisation x PUE up to about 1.35 on the hottest hours).
  The old 230 MW "target" was 200 MW x the base PUE of 1.15. It understated
  the hot-weather peak, so the network target is now 245 MW.
- With single-axis tracking, PV alone still cannot hold a 200 MW import cap
  because the peak occurs at night or on hot evenings. With CSIRO GenCost
  2025-26 costs (PV A$1,621/kW), the calibrated solar model and network
  charges, the least-cost rule-based design is 200 MW PV + 50 MW / 100 MWh
  battery (grid charging) at about A$218 m/yr. That is about 1.7% more than
  grid-only supply (A$215 m/yr, which does not meet the limit) and cuts
  annual-factor emissions by 27%. A solar-only-charged design needs about
  400 MW PV with 50 MW / 200 MWh (A$230 m/yr).
- **PV is close to break-even.** At A$1,621/kW, tracking PV costs roughly
  A$60-65/MWh levelised after calibration, near the 2020-2024 average
  Queensland wholesale plus network energy price, so the PV size is sensitive
  to the price outlook.
- Grid charging means the battery fills from a coal-heavy grid overnight. The
  cost-versus-emissions panel shows the trade-off between cheaper designs and
  lower scope 2 emissions.
- **Network charges** (Powerlink 2026/27 published prices) add a locational
  demand charge and energy-based charges; with them included, the least-cost
  design shifts towards more PV.
- **Price-aware dispatch.** A realistic controller that plans each day on
  persistence forecasts, with an energy reserve for import-limit duty,
  captures 54-77% of the perfect-foresight saving. With it, 300 MW PV +
  150 MW / 600 MWh becomes the least-cost compliant design at about
  A$205 m/yr: 6% cheaper than the rule-based optimum, with 16% lower emissions
  and a 53% 24/7 carbon-free-energy score. The perfect-foresight upper bound
  is A$192 m/yr.
- **Solar model validated.** Against three operating Central Queensland farms,
  real output averaged 89% of the NASA-driven model (Moura matched almost
  exactly; Lilyvale, an older plant, was 22% lower). All studies now use the
  calibrated irradiance (x0.9). Solar-data uncertainty has a small effect on
  cost (about 2% for a 15% irradiance change).
- **Water** now depends on climate: about 1.3-1.5 L/kWh at the Queensland
  sites and about 2 L/kWh in the Pilbara.
- The inference profile peaks at about 157 MW, so the connection requirement
  depends strongly on workload type.

## 4. Multi-site ranking

All nine criteria are now scored (water and land from cited public sources,
fibre as distance to the nearest data-centre hub). Gladstone ranks first
(0.63), mainly because of the 26,934 ha Gladstone State Development Area,
then Rockhampton (0.61) and Livingstone (0.57). The weighted score does not
include the thermal proxy, which favours Bouldercombe (Rockhampton/
Livingstone) over Calliope River (Gladstone); the trade-off is industrial
land and port access at Gladstone against grid headroom and water security
at Rockhampton. Karratha and Port Hedland fail the network screen by a factor
of 4-14 and also score lowest on water, so they rank last. Their cost scores
still rest on an **assumed** NWIS energy price.

## 3a. Robustness and practical-design results

- **Hourly emissions.** The Queensland grid is about 0.50 t CO2-e/MWh at
  midday and 0.73 overnight (AEMO unit data, last 12 months). Hourly-matched
  emissions are a few percent below the annual-factor (NGA) figure for the
  recommended design. For the price-aware battery, the annual-factor method
  shows a 5% emissions increase, but hourly matching shows almost none,
  because the battery mostly charges in cleaner midday hours. The 24/7
  carbon-free-energy score is about 50% for the recommended design.
- **Economic uncertainty.** The least-cost total spans roughly A$118-384 m/yr
  (P10-P90), driven almost entirely by which historical price year applies.
  Every leading design uses a 50 MW battery; the PV size moves between 100
  and 400 MW. Choosing the base design costs a median A$1.3 m/yr (under 1%)
  more than the per-sample optimum, and A$11 m/yr at the 90th percentile.
- **Workload mix.** A 50/50 training/inference facility peaks at about 185-200 MW,
  below a 200 MW import limit, so it needs PV but no battery.
- **Climate change.** Each 1 C of warming adds about 2 MW to peak demand. At
  Rockhampton, +1 C already exceeds the 245 MW target, so the connection
  should carry a climate margin of about 5-10 MW.
- **Backup supply.** About 80 standby units are needed. Simple N+1 leaves about a
  20% chance that too few start, so about N+5 is needed for a 0.1% risk. Backup
  adds roughly A$280-300 m capital (about A$28 m/yr), comparable to the PV
  investment, and is a material cost item.
- **Flicker (measured swing data).** Using swing sizes and periods reported
  from production AI clusters (Choukse et al., 2025), unmitigated swings
  (about 50% of IT load) exceed flicker planning levels even at the strong
  275 kV nodes, while GPU power smoothing of the kind available on NVIDIA
  GB200 (about 10% residual swing) passes. Power smoothing is therefore a
  connection design requirement.
- **Verified inputs.** Costs (CSIRO GenCost 2025-26), emission factors (NGA
  2025: Queensland 0.67, NWIS 0.56 kg CO2-e/kWh), Queensland inertia
  thresholds and island frequency bands (AEMO 2024 Inertia Report) are now
  taken from the primary sources.

## 5. Extensions (November 2026)

- **Hourly power flow.** With one year of metered Central Queensland unit
  output, the multi-bus model reproduces the middle of Powerlink's published
  Gladstone and CQ-SQ duration curves after fitting two zone-load factors
  (RMS error ~180 MW over the 3-97% range), but its spread is narrower than
  the published curves, so it is used for the **change** each option causes.
  It confirms the corridor picture and adds one: a Bouldercombe connection
  relieves the Gladstone section (-75 MW) but raises the worst single-outage
  loading of the Stanwell-Bouldercombe circuits by ~16 points (against an
  assumed 900 MVA rating); a Calliope River connection loads the Gladstone
  section (+149 MW). Both circuit groups' ratings belong in the Powerlink request.
- **Why the dynamic models disagree.** In the islanded minimum case, the full
  load loss stays inside 49-51 Hz if fast governors (0.5 s valve) deliver at
  least ~37% of the modelled response (~570-585 MW/Hz). With 2 s valves ~65% is
  needed; with the 6 s response assumed in the reduced-order screen it fails at
  any level. The outcome therefore hinges on how fast Queensland's frequency
  response is, which AEMO/Powerlink data must confirm.
- **Fault ride-through.** With an assumed 0.7 pu trip setting, a 100 ms fault at
  almost any 275 kV bus in Central Queensland dips the data-centre bus enough
  to trip the whole facility (11 of 12 fault locations for Bouldercombe in the
  islanded case). The combined fault plus 245 MW loss stays inside the band
  with the full modelled governor response (peak ~50.5 Hz), but the sweep above
  shows that margin goes if much less response is delivered, so ride-through
  capability (UPS and
  protection settings that keep the facility connected) is a connection
  requirement, not an option.
- **Recent prices.** At 2025 and 2026 prices the rule-based least-cost design
  is unchanged (200 MW PV + 50 MW / 100 MWh) and costs less (about A$193 m and
  A$156 m a year). The forecast-controlled design changes with the price year,
  so the battery size should be fixed with the operating strategy.
- **Flexible load.** Pausing half the training load when prices exceed the
  value of lost compute saves A$5-19 m a year on top of the base design
  (13-378 paused hours a year for A$3,000-300/MWh). Trimming load instead of
  using a battery to hold the 200 MW limit (~1,000 h/yr, ~3.5 GWh of IT a
  year) is cheaper than the battery unless lost compute is worth more than
  about A$2,000/MWh.
- **Real GPU-cluster profile.** The Alibaba trace has 4-6x larger hour-to-hour
  ramps than the synthetic profile. Run at training-level utilisation, its
  peak reaches the full-IT hot-weather demand (~268 MW) and stays high for
  days, so no battery in the tested range can cap import at 200 MW. The
  connection request should be based on full IT capacity x peak PUE, not on
  average utilisation.

## Thesis-ready conclusion (edit numbers from the generated report)

> Public-data OpenDSS screening of five Northern Australia candidate sites
> shows that the Central Queensland 275 kV nodes (Bouldercombe and Calliope
> River) are strong enough in voltage, short-circuit ratio and load-trip
> terms to host a ~245 MW AI data centre under N-1 minimum fault levels,
> and pass a reduced-order screening of load pickup/loss, PV/BESS trip and
> network-outage events. Firm capacity cannot be confirmed without line and
> transformer ratings, existing flows and N-1 load-flow results from
> Powerlink. Larcom Creek fails under its N-1 fault level. The Pilbara
> sites are limited to roughly 50-90 MW (Karratha 132 kV ring) or about 20 MW
> (22 kV), well below the requirement, and system-level NWIS constraints
> remain unassessed. A corridor-level thermal proxy based on Powerlink's
> published Gladstone grid-section data favours Bouldercombe over Calliope
> River, and an open-source RMS study finds all screened events stable, with
> the Queensland-islanded load loss the critical case. Hourly modelling with
> 2020-2024 weather shows that 200-300 MW of tracking PV with a 50-150 MW
> battery holds import to 200 MW at close to grid-only cost while cutting
> emissions by 16-27%.
> These findings define the utility data and reinforcement studies needed
> before any connection could be considered viable.

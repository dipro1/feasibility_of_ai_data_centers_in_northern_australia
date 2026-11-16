# Reduced Central Queensland 275 kV equivalent

Same schema as `inputs/utility_model_template`, so Powerlink data can replace
these files directly.

- `buses.csv`: published TAPR 2025 Appendix I fault levels (minimum system
  normal, minimum N-1, maximum). Coordinates are approximate, for drawing only.
- `branches.csv`: topology and route lengths from **OpenStreetMap**
  (`osm_cq_275kV.json`, (c) OpenStreetMap contributors, ODbL), derived by
  `build_cq_topology.py` (`osm_topology.csv` lists every direct substation
  connection found and the OSM line names along it). Circuit counts follow
  Powerlink TAPR 2025 Appendix G grid-section definitions and Chapter 6 where
  published; the remaining counts are marked "assumed" in `data_status`.
  Impedance is typical for 275 kV (0.03 + j0.32 ohm/km per circuit); ratings
  are not published.
- `contingencies.csv`: every single-circuit outage (topological N-1).

Source buses (`bus_type = source`) have Thevenin impedances fitted by
`run_cq_multibus_study.py` so that every bus reproduces its published minimum
system-normal fault level (with one global line-impedance multiplier). The fit
is then checked against the published minimum N-1 fault levels, which it was
not fitted to. Teebar Creek and Halys (south) are not modelled as buses: the
Wurdong-Teebar Creek route ends at Gin Gin, and the Calvale-Halys circuits are
represented by the Calvale source.

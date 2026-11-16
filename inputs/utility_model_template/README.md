# Utility model import template

Replace the example rows in these CSV files with de-identified network-owner
data. Do not invent missing values. The files separate topology, ratings,
operating conditions and contingencies so the public screening model can later
be replaced by a connection-level model.

- `buses.csv`: bus names and nominal voltages.
- `branches.csv`: line/cable sequence impedances and ratings.
- `transformers.csv`: transformer ratings, impedances and taps.
- `injections.csv`: coincident load and generation operating cases.
- `contingencies.csv`: credible N-1 element outages.

All identifiers must be unique and branch/transformer bus names must exist in
`buses.csv`.

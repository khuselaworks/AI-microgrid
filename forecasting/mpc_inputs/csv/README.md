# Controller forecast tables

Each `{tier}_load_kw.csv` or `{tier}_pv_kw.csv` row is a forecast issue time followed by 288 five-minute predictions in kW. `measured_series.csv` contains the measured series; `day_ahead_stitched.csv` joins midnight-issued forecasts into continuous daily schedules. See the parent guide for timestamp conventions and the three forecast tiers.

[Back to parent folder](../README.md)

# Local input data

Place private CSV or Excel inputs here; Git ignores their contents.
Input datasets are not distributed with this project.

Load CSV files need a timestamp column and a numeric target column (use
`--column` to select it). Solar inputs may be CSV or Excel; see
`load_target` in `datasets.py` for supported column detection and formats.
Pass the input path explicitly with `--source`.

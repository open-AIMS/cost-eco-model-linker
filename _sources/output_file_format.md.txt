# Output file format

The functions in this library generate the following output file types for the economics modelling:

- Metrics summary files (saved by default under `Indicators/rci`, `Indicators/rfi`, and `Indicators/rti` as parquet files)
- A cost overview file (saved by default in `Costs` as a CSV file)
- EIA cost breakdown files (saved in `Costs` as CSV files)
- Cost parameter files (saved in `Costs` as CSV files)
- An intervention key file (saved in `intervention_keys` as a CSV file)

By default, for a single unique intervention ID (a unique intervention scenario run for any
number of reps and any number of climate models), the above examples would generate 6
metric output files. This would include separate files for each of the 3 metrics (the RCI,
RTI and RFI), and separate files for counterfactual and intervention scenarios. The cost
sampling output would be a single file, as cost sampling is only done for intervention
scenarios and all cost metrics are included in a single file.

## Economics metric summary file structure

The following is an example metrics summary file generated to input to `CREAM`, for the RCI metric:
```{csv-table} Part of an example economics metrics input file
:header-rows: 1
:file: interventionID_ecol0_intervention_var_rci.csv
```

A file such as the above is generated for *each intervention run*, i.e., for each unique
combination of deployment volume, enhancement level, deployment years etc. run in
`ReefModEngine.jl`, for each metric and for each counterfactual and intervention.

The outputs in the columns `sim_1`, `sim_2` etc. represent different draws for the same
intervention scenario, which can be across multiple climate models. Each draw may sample
ecological, expert and other forms of uncertainty depending on the uncertainty settings
when the files were generated (see `calculate_metrics.default_uncertainty_dict()`).
The other columns summarize key information such year, year relative to the first
intervention year, reef name, distance to port, reef area in different management zones and
other spatial information.

Each intervention is identifiable through an intervention ID captured in the file name. For
a set of runs, an intervention ID key file (as a CSV) is also generated and saved in
`intervention_keys`, which links key intervention parameters to an intervention ID.
An example intervention ID key file is included below. Note that the number of 1YO corals
reported is the actual number of outplants recorded in `ReefModEngine.jl`, so it varies
slightly between climate model replicates due to slightly different space available for
coral growth in different climate scenarios. The input value for deployment volume will not
vary for the same intervention ID.

The functions here treat the varying number of outplants for a single intervention as a
stochastic sample for that intervention in the cost model sampling. The intervention ID key
files are necessary to assure scenario sampling IDs match between the cost and metrics
samples, but also for future reference as a record of the intervention scenarios which were
run for a particular set of economics input files.
```{csv-table} Part of an example intervention ID key file
:header-rows: 1
:file: intervention_ID_key_test_results_econ_metrics.csv
```

## Cost summary file structure

The cost files generated have the structure shown in the example below. The `component` column refers to 11 key cost codes:

1. CAPEX - sum of production and deployment cost
2. Contingency CAPEX - % of CAPEX
3. OPEX - sum of production and deployment cost
4. Sustaining capital OPEX - set to zero for now (assumed to be included in OPEX through contract)
5. Contingency OPEX - % of OPEX
6. Vessel fuel - only relevant if volunteer vessels are used - set to zero for now
7. CAPEX-monitoring - set to zero (assumed no monitoring cost)
8. Contingency CAPEX-monitoring - % of CAPEX-monitoring
9. OPEX-monitoring - set to zero (assumed no monitoring cost)
10. Sustaining capital OPEX-monitoring - set to zero (assumed no monitoring cost)
11. Contingency OPEX-monitoring - % of OPEX-monitoring

Each of the columns `draw1`, `draw2` etc refer to unique samples drawn from the cost models for a particular
intervention scenario by varying key cost model parameters (specified in `config.csv`). The cost files and
metric summary files are linked by their intervention ID, as described in the intervention ID key file generated
when the metric summary files are produced. Cost files are only generated for the intervention scenarios, as counterfactual
scenario will have zero cost, so for a given unique intervention ID, one cost file is generated.
```{csv-table} Part of an example cost output file
:header-rows: 1
:file: cost_output_template.csv
```

## Cost overview file structure

For each unique intervention scenario, a cost overview file named
`ID{scen}_cost_overview.csv` is written to the `Costs` directory. This file contains one
row per combination of intervention year, ecological replicate, and cost model draw. The
columns record the number of devices and 1YO corals deployed, the raw production and
deployment CAPEX and OPEX, the LM CAPEX, the LM OPEX before and after applying the
distance multiplier, and subtotals and totals for CAPEX and OPEX across all model
components. These files are the primary source for per-draw uncertainty quantification of
intervention costs.

## EIA output files

For each unique intervention scenario, three EIA files are written per cost model type
(production, deployment, and LM), named `EIA_{id}_{model}_{variant}.csv`. The three
variants are:

- `raw` records the cost broken down by ANZSIC industry code for the last cost model draw.
- `proportional` records each industry code's share as a proportion of total CAPEX for the
  last draw.
- `scaled` multiplies the proportional shares by the model's total post-inventory CAPEX,
  providing cost-weighted industry code values.

EIA files reflect only the last cost model draw and are intended for auditing the industry
cost structure, not for uncertainty propagation. Per-draw totals are available in the cost
overview files.

## Cost parameter files

For each replicate of each intervention scenario, a cost parameter file named
`ID{id}_rep{rep}_cost_params_{model}_pid{pid}.csv` is written to the `Costs` directory.
This file records the sampled factor values used for that replicate. The
`distance_from_port` value recorded is taken from the first reefset of the first
intervention year and serves as a representative value; in multi-reefset scenarios,
per-reefset distances are applied during evaluation but are not separately logged.
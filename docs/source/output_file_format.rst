Output file format
==================

The functions in this library generate two key output file types for the economics modelling,

* A metrics summary file (saved by default in `econ_outputs`)
* A cost summary file (saved by default in `cost_outputs`)

Economics metric summary file structure
---------------------------------------

The following is part of and example metrics summary file generated to input to CREAM, for the area saved RCI metric :

.. csv-table:: Example economics metrics input file
   :header-rows: 1
   :file: interventionID_ecol0_intervention_var_rci.csv

A file such as the above is generated for `each intervention run`, i.e. for each unique combination of climate model, deployment
volume, enhancement level, deployment years etc. settings run in ReefModEngine.jl. The outputs in the columns `sim_1`, `sim_2` etc.
represent different draws for the same intervention scenario. Each draw may sample ecological, expert and other forms of
uncertainty depending on the uncertainty settings when the files were generated (see :meth:`calculate_metrics.default_uncertainty_dict`).
The other columns summarize key information such a year, year relative to the first intervention year, reef name,
distance to port and other spatial information.

Each intervention is identifiable through an intervention ID captured in the file name. For a set of runs, an intervention
ID key file is also generated, which links key intervention parameters to an intervention ID, such as in the below. Note that the
number of 1YO corals reported is the actual number of outplants, so it varies slightly between climate model replicates,
while the input value for deployment volume will not vary for the same intervention ID.

.. csv-table:: Example economics metrics input file
   :header-rows: 1
   :file: intervention_ID_key_test_results_econ_metrics.csv

Cost summary file structure
---------------------------
The cost files generated have the stucture shown in the example below. The `component` column refers to 11 key cost codes:
    * 1 - CAPEX - sum of production and deployment cost
    * 2 - Contingency CAPEX - % of CAPEX
    * 3 - OPEX - sum of production and deployment cost
    * 4 - Sustaining capital OPEX - set to zero for now (assumed to be included in OPEX through contract)
    * 5 - Contingency OPEX - % of OPEX
    * 6 - Vessel fuel - only relevant if volunteer vessels are used - set to zero for now
    * 7 - CAPEX-monitoring - set to zero (assumed no monitoring cost)
    * 8 - Contingency CAPEX-monitoring - % of CAPEX-monitoring
    * 9 - OPEX-monitoring - set to zero (assumed no monitoring cost)
    * 10 - Sustaining capital OPEX-monitoring - set to zero (assumed no monitoring cost)
    * 11 - Contingency OPEX-monitoring - % of OPEX-monitoring

Each of the columns `draw1`, `draw2` etc refer to unique samples drawn from the cost models for a particular
intervention scenario by varying key cost model parameters (specified in `config.csv`). The cost files and
metric summry files are linked by their intervention ID, as described in the intervention ID key file generated
when the metric summary files are produced.

.. csv-table:: Example intervention ID key file
   :header-rows: 1
   :file: cost_output_template.csv

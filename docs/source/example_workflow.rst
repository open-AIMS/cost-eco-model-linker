Example workflow
================

With default settings, the follow is an example wrokflow for generating files needed for CREAM modelling based
on a set of ReefModEngine.jl runs.

The following modules are first imported:

.. code-block:: python

    import src.cost_calculations as cc
    import src.process_RME_data as prd
    import pandas as pd


The key metrics summary files for each intervention/counterfactual run can then be generated using
`prd.create_economics_metric_files()`. This can be run with default parameters with just the filepath
to the rme results and number of simulations, or with more detail specifications on types of uncertainty
to sample and which metrics to calculate. The number of simulations `nsims` includes sampling the types
of uncertainty specified by the uncertainty flags `ecol_uncert`, `shelt_uncert` and `expert_uncert`.

If `ecol_uncert=1`, ecological uncertainty is sampled in the results by sampling rme reps for a particular
set of results (stochastic samples within a single climate model). If `ecol_uncert=0` the mean over all
ecological reps is instead used. If `expert_uncert=1`, expert uncertainty is incorporated in the results
by sampling a set of expert opinons on what thresholds of the 5 metrics incorporated in the Reef Condition
Index should be considered "Poor", "Good", "Very Good", etc. condition. If `expert_uncert=0` the mean of the
7 experts opions is used (see `./datasets/ExpertReefCondition_AllResults.csv`). At the moment shelter volume
uncertainty sampling has not been incorporated (`shelt_uncert=0` is the default), as it needs access to
number of corals in each taxa and sizeclass in the rme resultset, but will be incorporated in future versions.

Metric functions to include can be added using the optional parameter `metrics = [area_saved_above_thresh, area_weighted_rci]`.
The defaults include area saved in and above good condition and area weighted RCI.

.. code-block:: python

    # Filepath to RME runs to process
    rme_files_path = "path to ReefModEngine.jl results"
    # Number of sims for metrics sampling (default includes ecological and expert uncertainty in RCI calcs)
    nsims = 3
    # Create metric datafiles for economics modelling and extract filename for intervention key
    int_keys_fn = prd.create_economics_metric_files(rme_files_path, nsims, ecol_uncert=1, shelt_uncert=0, expert_uncert=1,
                                  metrics = [area_saved_above_thresh, area_weighted_rci])


After creating the metric summary files for CREAM, the cost files can be created by sampling the cost models
for the same scenarios run in the rme result set. The files are linked by an intervention ID key file,
which details the file names for the metric summary files and what intervention parameters they correspond to.
This contains key information for sampling the cost parameters, and also for interpreting any resulting economics
analyses. The filepath of this intervention ID key file is captured in the output of `prd.create_economics_metric_files()`.
It can then be loaded into a datframe using pandas. The cost models can then be sampled using the ID key dataframe
and `n_draws`, which specifies the number of samples to make of the cost model parameters, the distribution ranges
of which are specified in `config.csv`.

.. code-block:: python

    # Number of cost model draws
    n_draws = 2**2
    # Load ID doc which links scenarios to settings and outputs
    ID_key = pd.read_csv(int_keys_fn)
    # Create cost datafiles for the intervention run ids in ID_key
    cc.calculate_costs(ID_key, n_draws)


The sampled cost files will be available in the `cost_outputs` folder and the metric summary files will be
saved in the `eco_outputs` folder.

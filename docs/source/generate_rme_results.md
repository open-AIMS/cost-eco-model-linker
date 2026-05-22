# Generating scenarios in ReefModEngine.jl

Simulation results from ReefModEngine.jl are currently used to generate the required inputs
for economic modelling. An example of running a test set of scenarios is provided here, but more detail on running scenarios in ReefModEngine.jl is available in its [documentation](https://open-aims.github.io/ReefModEngine.jl/v1.4.1/getting_started).

`rrap-cost-linker` has been tested with version 1.0.43 of the RME.

## Example running scenarios in ReefModEngine.jl

In the example here, we want to generate economics output files for a single intervention, outplanting
10000000 corals over Moore reef every year for 5 years (GBRMPA ID 16-071). After running the scenarios
in `ReefModEngine.jl`, the key information that is used to generate economics input files from a set of
RME runs is the result set folder filepath, in this case `test_results_ext_moore_domain`.
```{literalinclude} running_rme.jl
:language: julia
```

## Suggested settings for ReefModEngine.jl runs

Certain ReefModEngine.jl settings are recommended for running scenarios to produce economics outputs, while
some settings will depend on the intervention scenarios the user wishes to run.

The following options are run settings:
```julia
set_option("thread_count", 2)  # Set to use two threads
set_option("use_fixed_seed", 1)  # Turn on use of a fixed seed value
set_option("fixed_seed", 123.0)  # Set the fixed seed value
set_option("initial_set_fixed", 1)  # Fix initial cover, so not randomised at each rep
set_option("recovery_value_enabled", 0)  # Don't save recovery value to save runtime
```

The options `thread_count` and `recovery_value_enabled` are optional but will make runs
take less time and use less memory. The more threads used, the quicker the code will run.
`recovery_value_enabled` is best set to zero as this records additional data logs which are
not needed for economics analysis and take up significant memory during runtime, as well as
making runs take longer.

The option `use_fixed_seed` is recommended for reproducibility. `initial_set_fixed` should
be set to 1, so that initial cover is not randomised each rep.

The following lines set the start and end years for the simulation:
```julia
start_year = 2007
end_year = 2099
```

These start and end years must be used so that the outputs will work in CREAM. The early
start year is required because the first 20 years of simulation are used to normalise
recorded numbers of juvenile corals in the metrics.

The following sets the number of stochastic climate draws to run from a particular climate
model:
```julia
reps = 20  # Number of repeats: number of random environmental sequences to run
```

The more runs the better for the economics modelling as this better represents ecological
stochasticity, but this will also make runs take longer.

All available gcms can be extracted by running:
```julia
gcms = [@RME gcmName(i::Cint)::Cstring for i in 1:10]
```

To run the same intervention for a set of climate models, you can loop over the desired
climate model names and reinitialise and run
the same intervention for each climate model. The results can then be concatenated via
```julia
concat_results!(result_store, start_year, end_year, reps)
```

within the loop.

Climate models should be chosen to adequately represent the spread of possible climate
outcomes. Climate models and reps
are sampled within the ecological modelling sampling when generating the metrics tables for
`CREAM` (see [metrics](metrics.md)).

The following code sets the intervention to be simulated, in this case a number of corals to be outplanted over a set of years on Moore Reef:
```julia
for (iv_yr_idx, iv_year) in enumerate(iv_years)
    set_outplant_deployment!("outplant_moore_iv_$(iv_yr_idx)", "moore_ext_set", n_corals_per_year, iv_year, reef_areas_km², fill(d_density_m², 6))
end
```

Note that the only intervention type the `rrap-cost-linker` can currently calculate
costs for is outplanting. Other interventions, such as larval methods, will be later
implemented, but require a separate cost model. Hence, for the current version,
interventions should only be of `outplant` type, set using `set_outplant_deployment!`.

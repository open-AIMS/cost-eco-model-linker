# Cost Models

The cost models used to calculate intervention costs are Excel-based models developed by
the RRAP Translation to Deployment team. The models must be requested via QUT (Nick Dendle
at QUT).

The compatible versions of the cost models are:

- Coral Aquaculture Deployment : `3.9.0 CA Deployment Model.xlsx`
- Coral Aquaculture Production : `3.9.1 CA Production Model.xlsx`
- Larval Maintenance : `3.9.6 LM Model.xlsx`

## Configuration files

Sampling of cost models depends on versioned configuration CSV files bundled with the
package. There is one file per model type, named `{version}_{type}_config.csv`:

- `3.9.1_prod_config.csv` (Coral Aquaculture Production)
- `3.9.0_deploy_config.csv` (Coral Aquaculture Deployment)
- `3.9.6_LM_config.csv` (Larval Maintenance)

These files are located inside the `rrap_cl` package directory. It is not
currently possible to supply alternative config files at runtime.

An example of the production config file for the latest compatible version is below:

```{csv-table} Config file for latest model version
:header-rows: 1
:file: config.csv
```

The file must include the following columns:

- `cost_type` : the model type the parameter belongs to (`production`, `deployment`, or `lm`).
- `sheet` : the Excel sheet name the parameter occurs on.
- `cell_pos` : the cell address for the parameter (e.g., `E5`).
- `factor_names` : a label for the factor.
- `range_lower`, `range_upper` : the lower and upper bounds for uncertainty sampling.
- `SA_range_lower`, `SA_range_upper` : bounds used for sensitivity analysis sampling (may differ from uncertainty bounds).
- `best_point_value` : the deterministic default value written to the spreadsheet when no sample is taken.
- `discrete_values` : a comma-separated list of allowed values for discrete factors (empty if continuous).
- `UNC_distribution` : the sampling distribution (`unif`, `logunif`, `discrete`, or `norm`).
- `is_cat` : a flag indicating whether the parameter is categorical (integer-valued).
- `comments` : free-text notes.

Each config file includes rows for the `capex` and `opex` output cells alongside the
input factor rows. The `capex` and `opex` rows use `variable_type = output` and are used
to locate the cells from which setup and operational costs are read after each spreadsheet
evaluation.

## Cost model parameter descriptions

The following table describes the key parameters sampled in the deployment and production cost models.
There are many other parameters which could be sampled, but the following were chosen as key sources of uncertainty
through consultation with the Translation to Deployment Team and sensitivity analyses.

| **Parameter name** | **Description** | **Expected range** | **Model** |
|--------------------|-----------------|-------------------|-----------|
| num_1yoec | The target number of corals to be outplanted in a year. Determines the required number of devices, where each device carries 3 baby corals, with a survival rate to 1YO of *coral_yield_1YOEC*. | (1000, 5000000) | Deployment and Production |
| species_no | Number of unique species/regions combinations to be outplanted. Each species and region needs different tanks for production. | (1, 12+) | Production |
| col_spawn_gam_bun, gam_bun_egg, egg_embryo, embryo_freeswim | Parameters detailing conversions from the spawn to gamete to egg to embryo to freeswimming stage of the coral lifecycle. | See example config file | Production |
| freeswim_settle, settle_just, just_mature | Parameters detailing conversions from the freeswimming to settlement-ready larvae, to just-settled larva to just-settled unit to mature unit. | See example config file | Production |
| coral_yield_1YOEC | The number of surviving 1 YO corals per outplanted device. | (0.6, 0.8) | Production and Deployment |
| optimal_rear_density | The optimal density to rear baby corals at. | (1,3) | Production |
| port | Index specifying which of port to use, generally overwritten in favour of using distance to port directly. | (0,4) | Deployment |
| DAJ_a_r | DAJ assembly rate (jig-sec/device). | (9, 10) | Deployment |
| DAJ_c_s | DAJ count at sea (DAJ/ship) | (1,2) | Deployment |
| deck_space | Deck space on the ship in m2 | Depends on the ship being used | Deployment |
| cape_ferg_price | Daily rate of the ship being used | Depends on the ship | Deployment |
| ship_endurance | Number of days the ship can stay out without going back to port | Depends on ship | Deployment |
| distance_from_port | Distance from port to intervention reef in nautical miles. The spreadsheet lookup table supports a maximum of 119.99 NM; values above this are capped automatically. When distance exceeds 59 NM, day-trip vessel configuration is disabled and the vessel type is set to Large Liveaboard. | Depends on intervention reef(s) | Deployment |
| secs_per_dev | On transect deployment rate of devices | (1,2) | Deployment |
| proportion | Proportion by which device deployment rate is reduced due to poor visibility | (0.5,0.55), but depends on conditions | Deployment |
| bins_per_tender | Bins holding devices that can fit in each tender | (4,6) but depends on tender | Deployment |
| deployment_dur | Days over which the deployment occurs | (25, 28) | Deployment |

## Sampling the cost models

The Excel-based cost models give CAPEX and OPEX costs of the production and deployment
stages of outplanting corals, for a particular input deployment volume, number of species,
distance from port to the deployment reef, and other factors, primarily for the ReefMod
Engine model. Further details are found in the [documentation](https://open-aims.github.io/rrap-cost-linker/context/02_EcologicalModels.html#reefmod).

## Cost model samples output file

The output files for the cost sampling include 11 cost codes for each intervention year and
simulation which are used by `CREAM`. These are described in the following table:

| **Cost code** | **Cost component** | **Description** |
|---------------|-------------------|-----------------|
| 1 | CAPEX | Sum of setup (capital expenditure) costs for the production and deployment stages. |
| 2 | Contingency of CAPEX | % of CAPEX, default is 25%. |
| 3 | OPEX | Sum of operational costs for the production and deployment stages. |
| 4 | Sustaining capital OPEX | Set to zero for now (assumed to be included in OPEX through the contract). |
| 5 | Contingency of OPEX | % of OPEX, default is 25%. |
| 6 | Vessel fuel | Only relevant if volunteer vessels are used, set to zero for now. |
| 7 | CAPEX - monitoring | Set to zero (assumed no monitoring cost). |
| 8 | Contingency CAPEX-monitoring | % of CAPEX monitoring. |
| 9 | OPEX - monitoring | Set to zero (assumed no monitoring cost). |
| 10 | Sustaining capital OPEX - monitoring | Set to zero (assumed no monitoring cost). |
| 11 | Contingency OPEX-monitoring | % of OPEX monitoring |

## Calculating costs for interventions over multiple years

For outplanting corals over multiple years, the CAPEX (setup) cost is subject to an
inventory and replacement model implemented in `cost_calculations._apply_outplant_inventory()`,
which is called from `cost_calculations.calculate_costs()`.

The inventory model tracks the value of existing production and deployment infrastructure
across years. At each year, a maintenance fraction (0.2 × current inventory) and a retained
fraction (0.8 × current inventory) are computed. If the current year's raw CAPEX exceeds
the retained inventory, the effective CAPEX for that year is the difference and the inventory
is updated to the raw CAPEX value. If the raw CAPEX does not exceed the retained inventory,
the effective CAPEX for that year is zero and the inventory decays to the retained fraction.
This means that if the scale of intervention stays the same or decreases, no additional
setup cost is incurred beyond the initial year. Operational costs (OPEX) are calculated
independently of this inventory logic and reflect the full year's operational spend each year.

## Calculating distance to port for multiple reefs

The Excel-based cost models take as input a single value for distance to port to calculate
deployment costs, so it is not specified how to deal with intervening on multiple reefs.
For multiple intervention reefs for a single intervention, the total distance to port is
calculated as follows:

1. The intervention reefs are clustered into groups of reefs which are a maximum distance \
   apart (set by parameter `max_dist`).
2. The initial distance to port is set as the maximum distance to port for reefs in the \
   closest cluster to port.
3. For the remaining clusters, distances are calculated between the furthest reefs from \
   port in each cluster.
4. The total distance travelled is then estimated as the initial distance to the closest \
   cluster's furthest reef plus the remaining distances between the furthest reefs from  \
   port in each of the remaining clusters, travelling in order from closest cluster to  \
   port to furthest cluster from port.

<img src="./_static/figs/prod/reef_distances_diagram.png" width="800">

## Larval Maintenance (LM) model

The Larval Maintenance model (`3.9.6 LM Model.xlsx`) costs the ongoing maintenance of
larval seeding operations. It is evaluated alongside the CA Production and Deployment
models when both `"outplant"` and `"lm"` are included in the `active_models` argument
passed to `evaluate()`.

The LM model is evaluated year-by-year, applying an inventory and replacement model
post-hoc across the full time series (in contrast to the per-year inventory logic used for
the outplant models). Configuration is loaded from `3.9.6_LM_config.csv`.

### Distance multiplier

The LM model applies a distance-based OPEX multiplier to account for travel costs when
reef sites are far from port. The multiplier formula is:

```
multiplier = 0.2495 × distance_NM^0.517
```

This formula is calibrated so that a distance of 15 NM gives a multiplier of approximately
1.0 (no adjustment), 30 NM gives approximately 1.5×, and 60 NM gives approximately 2.0×.
The multiplier is computed by `cost_calculations.lm_opex_distance_multiplier()`.

### Entry points

The LM model can be used through the following public functions:

- `cl.evaluate_lm_cost(workbook_path, scenarios_df, **factors)` evaluates the LM model
  over a year-by-year coral scenario DataFrame and applies the inventory model.
- `cl.run_lm_model(cost_model, nsims)` generates Sobol samples for the LM model and
  returns an SALib ProblemSpec with `cost_model_results` populated.
- `cl.sweep_lm(lm_model, sweep_param, search_range, lm_params)` evaluates the LM model
  over a range of values for a single parameter.

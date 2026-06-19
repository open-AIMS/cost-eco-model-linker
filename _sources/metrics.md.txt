# Metrics

The available metrics include the Reef Condition Index (RCI), the Reef Tourism Index (RTI) and
Reef Fishing Index (RFI). There are also several versions of the RCI available, including raw
RCI and RCI, which measures the total reef area in good or very good condition.

## Raw RCI

The raw RCI can be computed via the `rci_threshold` argument to the metric extraction
functions. This metric comprises 5 reef metrics:

- Relative coral cover
- Relative shelter volume
- Relative abundance of juveniles
- Complementary relative crown of thorns population (relative to outbreak levels)
- Complementary rubble cover

These values are categorised as within Very Good, Good, Fair, Poor or Very Poor condition depending on their
values and a set of categories compiled by expert elicitation. The expert elicitation process was carried out at a workshop in October 2021 with 8 coral reef experts elicited,
resulting in the summary table below.
```{csv-table} Expert elicitated reef condition metrics
:header-rows: 1
:file: Heneghan_RCI.csv
```

The raw RCI is calculated by assessing the condition of a reef at each timestep for each metric according
to the above categories. The reef is assigned the condition of the highest category for which 3 or more metrics satisfy
that category's threshold. The category thresholds can be used as means across all 8 experts without uncertainty.
Alternatively, the raw expert condition categories of data collected from the 8 experts can be sampled
when calculating metrics by setting `expert_uncert=1`.

## RTI

The RTI, or Reef Tourism Index, is a continuous version of the RCI condition categories.
Area-weighted RTI can be computed using `process_RME_data.area_weighted_rti()`.

## RFI

The RFI or Reef Fishing Index, estimates the total fish biomass in kg km^2, based on a linear regression of total
cover. This is based on digitisation of Fig 4A and 6B in [Graham and Nash, 2012](https://doi.org/10.1007/s00338-012-0984-y).
The RFI can be calculated using `process_RME_data.rfi()`.

## RCI

The RCI looks at the total area of reef for which the condition is within the Good or Very
Good categories. It is computed using `process_RME_data.rci()`, which accepts an
`rci_threshold` parameter (default 0.6) defining the minimum condition score required for a
reef to be counted as being in good or very good condition.

## Coral area saved

`process_RME_data.coral_area_saved()` returns the total live coral cover in hectares across
all intervention reefs at each timestep. This metric is not a condition index; it provides
an absolute coverage figure useful for comparing intervention scenarios in terms of total
reef area maintained.

## Uncertainty sampling

Uncertainty sampling is controlled by the dictionary returned from
`cl.default_uncertainty_dict()`. The defaults are:

```python
{
    "ecol_uncert": 0,    # use mean over climate replicates
    "shelt_uncert": 0,   # shelter volume uncertainty not yet implemented
    "expert_uncert": 1,  # sample RCI thresholds across experts
    "rti_uncert": 1,     # sample RTI regression parameters
    "rfi_uncert": 1,     # sample RFI regression parameters
}
```

When `ecol_uncert=1`, ecological uncertainty is incorporated by sampling over climate model
replicates for each result set. When `ecol_uncert=0`, the mean across climate replicates is
used instead.

When `expert_uncert=1`, RCI condition thresholds are sampled across the 8 expert opinions
collected at the October 2021 workshop. When `expert_uncert=0`, the mean threshold across
all 8 experts is used instead (see `./datasets/ExpertReefCondition_AllResults.csv`).

When `rti_uncert=1` or `rfi_uncert=1`, the regression parameters used to compute RTI and
RFI respectively are sampled from their uncertainty distributions rather than using point
estimates.

Shelter volume uncertainty (`shelt_uncert`) is not yet implemented. It would require
per-taxa and per-size-class coral counts from `ReefModEngine.jl` result sets, which are
not currently available in standard RME outputs.

### juv_max_years

The `juv_max_years` parameter (default `[0, 18]`) defines the index range within the
simulation time series used to establish the baseline maximum juvenile coral count. This
baseline is used in the RCI juvenile abundance component. The default spans the hindcast
period before the first intervention year. If the hindcast length differs from 18 years in
a given RME run, this parameter should be adjusted to match the correct hindcast window.

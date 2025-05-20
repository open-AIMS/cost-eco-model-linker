Available metrics
=================

The available metrics include Reef Condition Index  (RCI), the RTI, (a continuous version of the RCI) and
Reef Fishing Index (RFI). There are also several versions of the RCI available, including an area weighted
RCI and "area saved" which measures the total reef area in good or very good condition.

RCI
---

The RCI can be calculated using :meth:`process_RME_data.raw_rci`. This metric comprises 5 reef metrics,
    * Relative coral cover
    * Relative shelter volume
    * Relative cover of juveniles
    * Inverse relative crown of thorns population (relative to outbreak levels)
    * Inverse rubble proportion

These values are categorised as within Very Good, Good, Fair, Poor or Very Poor condition depending on their
values and a set of categories compiled by expert elicitation:

.. csv-table:: Expert elicitated reef condition metrics
   :header-rows: 1
   :file: Heneghan_RCI.csv

The raw RCI is calculated by asessing the condition of a reef at each timestep for each metric according
to the above categories. The reef is assigned the condition of the highest category for which 3 or more metrics satisfy
that category's threshold. The raw expert condition categories of data collected from 7 experts can also be sampled
when calculating metrics by setting `expert_uncert=1`.

RTI
---

The RTI is calculated as a linear regression on the RCI condition categories, forming a continuous version of
the RCI. RTI can be calculated using :meth:`process_RME_data.raw_rti`.

RFI
---

The RFI or Reef Fishing Index, estimates the total fish biomas in kg km^2, based on a linear regression of total relative
cover. This is based on digitisation of Fig 4A and 6B in Graham and Nash, 2012 `<https://doi.org/10.1007/s00338-012-0984-y>`_
The RFI can be calculated using :meth:`process_RME_data.rfi`.

Area saved RCI
--------------
The area saved RCI looks at the total area of reef for which the condition is within the Good or Very Good categories.
Area saved RCI can be calculated using :meth:`process_RME_data.area_saved_rci`.

Area weighted RCI
--------------
The area weighted RCI weights the RCI by the total reef area relative to the total area of the set of reefs modelled
(generally the 3000+ GBR reefs modelled in ReefModEngine.jl). Area weighted RCI can be calculated
using :meth:`process_RME_data.area_weighted_rci`.

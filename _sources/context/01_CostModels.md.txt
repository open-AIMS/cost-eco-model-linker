# 1. Cost Models

The cost models are being updated continuously by the T2D Team as more information becomes available.
This documentation captures updates as of:

- 20 March 2025

Updated models:

- Aquaculture (3.7.0 CA-Production; 3.5.5 CA-Deployment)
- Larval slicks (3.9.2 LM)

This documentation links information and assumptions of cost models and ecological models
(ReefMod, ADRIA, ~CScapeC~scape, CoCoNet) and required by the economic models (CREAM).

## 1.1 Aquaculture
### 1.1.1 Changes to previous generation of cost models:

-	Cost model separated into two components: production and deployment models. Production \
and deployment occur in same year.
-	Contingencies are removed from calculation – optional feature in post processing.
-	Cost of aquaculture facilities is factored in as contracted service \
(based in Townsville).
-	2.5% sustaining capex removed from calculation (assumed to be internalised in \
contracted service).
-	No volunteer vessels are deployed.
-	Monitoring cost no longer included.
-	Decommissioning costs still assumed to be zero (devices will not be removed).
-	Deployment model requires choosing a specific reef (4 reefs are currently available), \
which are tied to specific ports (distance-based cost flexibility – vessel travel costs \
are included).
-	Expansion requires additional CAPEX for production and deployment calculated in model. \
CAPEX_scale for production expansion for second batch less than CAPEX for first batch. \
Expansion costs cover tanks required, but don’t include construction costs for buildings to house them.
-	All costs are estimated in A$2024 real value (best approximation).

### 1.1.2 Cost model information:

-	The reefs used to harvest and deploy coral species are in close proximity.
-	Species vs functional groups: each species and regional species variant must have \
separate tank; information required to determine the number of tanks: \
1) How many species?
2) Where was each species harvested and deployed?
-  When producing new devices this is divided over number of spawnings and number of "batches" (one batch is one species in one region)
    - If a new species is introduced, there needs to be a trip out to the reef to do heat tolerance testing on that species
    - This is where corals are sampled in the field and their heat tolerance is tested
    - So if introducing a new species in later years, the testing cost needs to be included
    - There is an input for "New species batches" which includes testing costs

-	Model requires an even distribution of number of devices/corals across all specified species. This might change with further cost model development and needs to be revisited.
-	No additional costs of breeding corals with differentiated heat tolerances.
-	ID setting: number of species (ID step previously completed  cost =0); number of new species (ID step must be completed  cost >0).
-	Each device has 3 settlement units, with 8 settlers per unit; conditional on assumed survival rate, one device yields one 1-YOEC with an assumed survival probability. This probability includes all processes in the first year post deployment, including devices being carried away by currents and corals dying on the device.
-	Assumed survival rate of larval settlers into 1-YOEC: 80% upper estimate; 60% lower estimate. 1YOEC are defined as corals after first year post-deployment – direct input into ecological model.
-	For the deployment model, the Large vessels, such as the Large Tourism vessels, are the best estimate for cost of transport for now.

### 1.1.3 Assumptions:

-	Maintenance cost assumed to be factored into production cost via contracts.
-	Decommissioning cost assumed to be zero (devices will not be removed).
-	Economies of scale assumed to be zero.
-	Capacity limitations and potentially resulting cost changes assumed to be zero.
-	Construction cost of buildings needed to house additional breeding tanks in case of expansions assumed to be zero.
-	All costs are estimated in A$2023 real value (best approximation).
-	Any other assumptions inherent in cost model [outside the scope of this study and not captured here].

## 1.2 Larval slicks
### 1.2.1 Changes to previous cost models:

-	Contingencies are removed from calculation (optional feature during post processing).
-	1.5% sustaining capex removed from calculation (i.e., no maintenance costs included). Model assumes that after 10 spawning events (5 years given assumed 2 spawnings per year) initial CAPEX gets replaced in full.
-	Expansion requires additional CAPEX calculated in model.
-	No volunteer vessels are deployed.
-	All costs are estimated in A$2024 real value (best approximation).

### 1.2.2 Cost model information:

-	Settings ‘new domain’: if set to ‘yes’ assumes 1 months’ worth of research is costed; default would be ‘no’.
-	Larval harvesting and deployment occur in spatial proximity (same reef).
-	Input “Device deployment size” refers to the number of settlement devices
-	Input “Larval release pools” refers to the number of pools for release rather than settlement
-	Current models specify the different ship types for different parts of deployment – the “spawning block mothership” is used the longest and hence may have the most impact on costs.
-	Input “Passive spawn catcher” should be generally left at 0%, the idea is to put out deflated larval pools to catch larvae passively over night with wind, but this is still being looked into.
-	Currently no cost differentiation across different reefs.
-	One device yields one 1-YOEC with an assumed survival probability. Larval survival rate set to 60% (based on recent research). This probability includes all processes in the first year post deployment, including devices being carried away by currents and corals dying on the device.
-	Two methods of larval deployment (separate or joint application possible): 1) device-based method; 2) free-released method – no devices).
-	Three vessel types required: 1) pre-spawning vessel (e.g., set-up, checks); 2) spawning mothership (larval harvesting); 3) spawning support ship (towing pools, moving over reefs to harvest larval). Use currently default settings for vessel types.

-	Costs:
    * Pre-spawning work, modelling
    * Freight equipment to nearest port
    * [NOT COSTED] Travel from port to site
    * Set up pools
    * Spawning – collect larval slicks at midnight, transfer to pools
    * Wait around for the larvae to grow
    * Device settlement, larval release, device deployment
    * Pack up pools
    * [NOT COSTED] Return travel to port
    * Equipment washdown
    * Freight equipment back to storage
    * [NOT COSTED] Storage locker/s


### 1.2.3 Assumptions:
-	Haulage of freight equipment to nearest port and back to storage assumed to be constant independent of location of storage facility and port used.
-	Maintenance cost assumed to be zero during lifetime of equipment (5 years based on 2 spawnings per year).
-	Cost of vessel travel from port to reef site and back assumed to be zero.
-	Storage locker cost assumed to be zero.
-	Economies of scale assumed to be zero.
-	Capacity limitations and potentially resulting cost changes assumed to be zero.
-	Any other assumptions inherent in cost model [outside the scope of this study and not captured here].

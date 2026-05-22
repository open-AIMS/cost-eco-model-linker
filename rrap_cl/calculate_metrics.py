import netCDF4 as nc
import pandas as pd
import numpy as np
import random
import os
from os.path import join as path_join

THIS_DIR = os.path.dirname(__file__)


def default_uncertainty_dict() -> dict:
    """
    Creates a dictionary containing default uncertainty parameter settings. Can be modified to control
    what sources of uncertainty are sampled when calculating metrics.

    Returns
    -------
    uncertainty_dict : dict
        Contains information on what uncertainty types to sample.

        - ecol_uncert : int (0 or 1)
            If 1 includes ecological uncertainty by sampling metrics over climate replicates, if 0 just uses
            mean of metrics over climate replicates.
        - shelt_uncert : int (0 or 1)
            Placeholder to be implemented, will sampling uncertainty in shelter volume parameters.
        - expert_uncert : int (0 or 1)
            If 1 includes expert uncertainty by sampling RCI condition thresholds over several expert opinions,
            if 0 uses RCI condition thresholds averaged over experts considered.
        - rti_uncert : int (0 or 1)
            If 1 includes rti uncertainty by sampling linear regression parameters used to convert RCI to continuous
            form.
        - rfi uncert : (0 or 1)
            If 1 includes RFI uncertainty by sampling linear regression parameters used to calculate RFI.

    """
    # return {
    #     "ecol_uncert": 1,
    #     "shelt_uncert": 0,
    #     "expert_uncert": 1,
    #     "rti_uncert": 1,
    #     "rfi_uncert": 1,
    # }
    return {
        "ecol_uncert": 0,
        "shelt_uncert": 0,
        "expert_uncert": 1,
        "rti_uncert": 1,
        "rfi_uncert": 1,
        "coral_only": 0,
    }


def indicator_params(
    result_set,
    scen_ids,
    uncertainty_dict=None,
    juv_max_years=None,
    max_coral_juv=None,
    seed: int = None,
    nsims: int = 1,
):
    """
    Calculates key parameters for shelter volume and RCI calculations given uncertainty sampling choices.

    Parameters
    ----------
        result_set : dict
            ReefModEngine.jl resultset structure.
        scen_ids : np.array
            List of scenario IDs to consider (e.g. only sample counterfactual/intervention etc.).
        uncertainty_dict : dict
            Contains information of which types of uncertainty to sample when processing metrics.
        juv_max_years : list
            Indices of years to calculate Juveniles max baseline over.
            Interventions cannot start in the first year.
        max_coral_juv : list[float]
            Max juveniles baseline (can be included instead of using hindcasting baseline).
        seed : int, optional
            Random seed for reproducibility.
        nsims : int, default=1
            Number of simulations to generate parameters for.

    Returns
    -------
        max_coral_juv : np.float
            Maximum juveniles baseline.
        sheltervolume_parameters : np.array
            Parameters for sheltervolume regression models.
        rci_crit : np.array
            Array of thresholds describing reef condition categories of size (nsims, 5, 4).
    """
    if seed is not None:
        np.random.seed(seed)
        random.seed(seed)

    if uncertainty_dict is None:
        uncertainty_dict = default_uncertainty_dict()
    if juv_max_years is None:
        juv_max_years = [0, 18]

    # MAXIMUM JUVENILES
    if max_coral_juv is None:
        if juv_max_years[0] == juv_max_years[1]:
            # A baseline for juveniles must be able to be derived from years prior to
            # interventions
            raise ValueError("Interventions cannot start in the first year.")

        # Determine which juvenile variable to use
        if "coral_juv_m2" in result_set.variables:
            juv_var = "coral_juv_m2"
        elif "relative_juveniles" in result_set.variables:
            juv_var = "relative_juveniles"
        else:
            raise ValueError(
                "Neither 'coral_juv_m2' nor 'relative_juveniles' found in results."
            )

        max_coral_juv = np.max(result_set[juv_var][scen_ids, :, juv_max_years[0] : juv_max_years[1]])

    ## SHELTER VOLUME UNCERTAINTY
    if uncertainty_dict["shelt_uncert"] == 0:
        sheltervolume_parameters = np.array(
            [
                [-8.31, 1.47],  # branching from Urbina-Barretto 2021
                [-8.32, 1.50],  # tabular from Urbina-Barretto 2021
                [
                    -7.37,
                    1.34,
                ],  # columnar from Urbina-Barretto 2021, assumed similar for corymbose Acropora
                [
                    -7.37,
                    1.34,
                ],  # columnar from Urbina-Barretto 2021, assumed similar for corymbose non-Acropora
                [
                    -9.69,
                    1.49,
                ],  # massive from Urbina-Barretto 2021, assumed similar for encrusting and small massives
                [-9.69, 1.49],
            ]
        )  # massive from Urbina-Barretto 2021,  assumed similar for large massives
    else:
        sheltervolume_parameters = np.array(
            [
                [
                    -8.31 + np.random.normal(0, 0.514),
                    1.47,
                ],  # branching from Urbina-Barretto 2021
                [
                    -8.32 + np.random.normal(0, 0.388),
                    1.50,
                ],  # tabular from Urbina-Barretto 2021
                [
                    -7.37 + np.random.normal(0, 0.561),
                    1.34,
                ],  # columnar from Urbina-Barretto 2021, assumed similar for corymbose Acropora
                [
                    -7.37 + np.random.normal(0, 0.561),
                    1.34,
                ],  # columnar from Urbina-Barretto 2021, assumed similar for corymbose non-Acropora
                [
                    -9.69 + np.random.normal(0, 0.603),
                    1.49,
                ],  # massive from Urbina-Barretto 2021, assumed similar for encrusting and small massives
                [-9.69 + np.random.normal(0, 0.603), 1.49],
            ]
        )  # massive from Urbina-Barretto 2021,  assumed similar for large massives

    if (
        uncertainty_dict["expert_uncert"] == 0
    ):  # If there is no uncertainty from survey, use median results
        G = pd.read_csv(path_join(THIS_DIR, "datasets", "Heneghan_RCI.csv"))
        rci_crit = np.array(G.loc[:, G.columns[[1, 3, 4, 6, 8]]]).T
        rci_crit = np.tile(rci_crit[np.newaxis, :, :], (nsims, 1, 1))
    else:
        # If there is uncertainty from survey, draw each metric's thresholds randomly from pool of experts
        G_df = pd.read_csv(
            path_join(THIS_DIR, "datasets", "ExpertReefCondition_AllResults.csv")
        )
        G = np.array(
            G_df.loc[:, G_df.columns[[2, 3, 5, 6, 7]]]
        )  # Cut off first two columns, convert to array

        G_len = G.shape[0]
        num_experts = int(G_len / 5)  # 5 conditions per expert

        rci_crit = np.zeros((nsims, 5, 5))
        for i in range(nsims):
            experts = random.sample(range(num_experts), 5)
            rci_crit[i, 0, :] = G[experts[0] : G_len : num_experts, 0]
            rci_crit[i, 1, :] = G[experts[1] : G_len : num_experts, 1]
            rci_crit[i, 2, :] = G[experts[2] : G_len : num_experts, 2]
            rci_crit[i, 3, :] = G[experts[3] : G_len : num_experts, 3]
            rci_crit[i, 4, :] = G[experts[4] : G_len : num_experts, 4]

    ## RTI LINEAR REGRESSION UNCERTAINTY
    rti_intercept = -0.4685314  # Intercept of rci to rti linear equation
    rti_cov_slope = 0.2625856  # Slope of rci to rti linear equation, with coral cover
    rti_shelt_slope = (
        0.6100339  # Slope of rci to rti linear equation, with relative shelter volume
    )
    rti_juv_slope = (
        0.8345460  # Slope of rci to rti linear equation, with relative coral juveniles
    )
    rti_cots_slope = 0.2569332  # Slope of rci to rti linear equation, with complementary of relative COTS abundance
    rti_rubble_slope = 0.3245505  # Slope of rci to rti linear equation, with complementary of rubble cover

    if uncertainty_dict["rti_uncert"] != 0:
        rti_intercept += np.random.normal(
            0, 0.0006761, size=nsims
        )  # Intercept of rci to rti linear equation
    else:
        rti_intercept = np.full(nsims, rti_intercept)

    ## RFI BUILT FROM DIGITISING FIG 4A AND FIG 6B FROM Graham and Nash, 2012 https://doi.org/10.1007/s00338-012-0984-y

    ## RFI LINEAR REGRESSION UNCERTAINTY
    if uncertainty_dict["rfi_uncert"] == 0:
        intercept1 = np.full(nsims, 1.232)  # intercept of coral cover to structural complexity equation
        intercept2 = np.full(nsims, -1623.6)  # intercept of shelter volume to reef fish biomass
    else:
        # Sample intercept from 95% prediction interval
        intercept1 = 1.232 + np.random.normal(
            0, 0.195, size=nsims
        )  # intercept of coral cover to structural complexity equation
        intercept2 = -1623.6 + np.random.normal(
            0, 533, size=nsims
        )  # intercept of shelter volume to reef fish biomass

    slope1 = 0.007476  # slope of coral cover to structural complexity equation
    slope2 = 1883.3  # slope of shelter volume to reef fish biomass

    ## RTI_3 LINEAR REGRESSION UNCERTAINTY
    rti_3_intercept_u = 0
    if uncertainty_dict["rti_uncert"] != 0:
        rti_3_intercept_u = np.random.normal(0, 0.163, size=nsims)
    else:
        rti_3_intercept_u = np.zeros(nsims)

    return (
        max_coral_juv,
        sheltervolume_parameters,
        rci_crit,
        rti_intercept,
        rti_cov_slope,
        rti_shelt_slope,
        rti_juv_slope,
        rti_cots_slope,
        rti_rubble_slope,
        rti_3_intercept_u,
        intercept1,
        intercept2,
        slope1,
        slope2,
    )


def load_indicator_data(results_data, scen_ids, nsims, ecol_uncert, curr_eco_sim_idx=None):
    """
    Helper function to load and normalize ecological indicator data from NetCDF results.
    Handles fallback from total_taxa_cover to total_cover.
    """
    if ecol_uncert == 0:
        # Take mean across all specified scenarios, preserving nsims dimension
        curr_eco_sim = [scen_ids[0]] * nsims
        indices = scen_ids
        reduction_axis = 0
    else:
        curr_eco_sim = (
            curr_eco_sim_idx
            if curr_eco_sim_idx is not None
            else random.choices(scen_ids, k=nsims)
        )
        indices = curr_eco_sim
        reduction_axis = None

    def _load(var_name):
        vals = results_data[var_name][indices, ...]
        if reduction_axis is not None:
            vals = np.mean(vals, axis=reduction_axis, keepdims=True)
        return vals

    # Load base variables
    data = {
        "cots": _load("cots"),
        "rubble": _load("rubble"),
        "relative_shelter_volume": _load("relative_shelter_volume"),
    }

    # Load evenness with fallback
    if "evenness" in results_data.variables:
        data["coral_evenness"] = _load("evenness")
    elif "coral_evenness" in results_data.variables:
        data["coral_evenness"] = _load("coral_evenness")
    else:
        data["coral_evenness"] = None

    # Load juveniles with fallback
    if "coral_juv_m2" in results_data.variables:
        data["coral_juv_m2"] = _load("coral_juv_m2")
    elif "relative_juveniles" in results_data.variables:
        data["coral_juv_m2"] = _load("relative_juveniles")
    else:
        raise ValueError(
            "Neither 'coral_juv_m2' nor 'relative_juveniles' found in results."
        )

    # Load cover data with fallback
    if "total_taxa_cover" in results_data.variables:
        taxa_cover = _load("total_taxa_cover")
        # Handle both (nsims, ntaxa, nreefs, nyrs) and (ntaxa, nreefs, nyrs)
        taxa_axis = 1 if taxa_cover.ndim == 4 else 0
        data["total_cover"] = np.sum(taxa_cover, axis=taxa_axis) / 100
    else:
        total_cover = _load("total_cover")
        # Normalize 0-100 to 0-1
        data["total_cover"] = (
            total_cover / 100 if total_cover.max() > 1.0 else total_cover
        )

    # Ensure all have (nsims, nreefs, nyrs) shape
    if data["coral_evenness"] is None:
        data["coral_evenness"] = np.ones(data["total_cover"].shape)

    for key in data:
        if data[key].shape[0] == 1 and nsims > 1:
            data[key] = np.tile(data[key], (nsims, 1, 1))

    return data, curr_eco_sim


def reef_condition_rme(
    results_data,
    scen_ids,
    ecol_uncert,
    sheltervolume_parameters,
    rci_crit,
    maxcoraljuv,
    nsims,
    curr_eco_sim_idx=None,
):
    """
    Calculates reef condition for a set of scenarios in the provided ReefModEngine.jl results_data.

    Parameters
    ----------
        results_data : dict
            ReefModEngine.jl resultset structure.
        scen_ids : np.array
            List of scenario IDs to consider (e.g. only sample counterfactual/intervention etc.).
        ecol_uncert : int (0 or 1)
            If 1 includes ecological uncertainty by sampling metrics over climate replicates, if 0 just uses
            mean of metrics over climate replicates.
        sheltervolume_parameters : np.array
            Currently unused, but when implemented will allow sampling of uncertainty in shelter volume models
            to calculate shelter volume.
        rci_crit : np.array
            Array of thresholds describing reef condition categories.
        maxcoraljuv : np.float
            Max juveniles baseline (can be included instead of using hindcasting baseline).
        nsims : int
            Number of simulations to sample

    Returns
    -------
        reefcondition : np.array
            Array containing reef condition of size (nsims, nreefs, nyears).
        metrics_dict : np.array
            Structure containing each of the metrics comprising the RCI, each arrays of size (nsims, nreefs, nyears).
    """
    # Settings
    criteria_threshold = 0.6  # threshold for how many criteria need to be met for category to be satisfied.
    cots_outbreak_threshold = 11  # number of CoTS per hectare to classify as outbreak, corresponds to 0.22 cots per mantatow (Moran and De'ath 1992)
    n_metrics = 5  # see below for metrics implemented

    # Load and normalize data using helper
    data, curr_eco_sim = load_indicator_data(
        results_data, scen_ids, nsims, ecol_uncert, curr_eco_sim_idx
    )

    total_cover = data["total_cover"]
    cots = data["cots"]
    coral_juv_m2 = data["coral_juv_m2"]
    rubble = data["rubble"]
    relative_shelter_volume = data["relative_shelter_volume"]

    nsims_actual, nreefs, nyrs = total_cover.shape

    # The following code is for calculating SV from number of corals, which is not currently possible with default
    # metrics saved in ReefModEngine.jl runs

    # juv_sizes = 1
    # adol_sizes = 2
    # adult_sizes = 3

    # if ngroups == 12:
    #     ntaxa = ngroups/2
    # elif ngroups == 6:
    #     ntaxa = ngroups

    # corals[:, :, :, :, juv_sizes] = data.nb_coral_juv
    # corals[:, :, :, :, adol_sizes] = data.nb_coral_adol
    # corals[:, :, :, :, adult_sizes] = data.nb_coral_adult
    # nsizes = 3
    # coral_numbers = np.zeros(nsims, nreefs, nyrs, ntaxa, nsizes)

    # if ngroups == 12:
    #     for tax = 1:6 # Get total numbers of each coral, across unenhanced and enhanced groups
    #         coral_numbers[:, :, :, tax, :] = corals[:, :, :, tax, :] + corals[:,:, :, tax + 6, :]

    # elif ngroups == 6:
    #     for tax = 1:6 # Get total numbers of each coral, across unenhanced and enhanced groups
    #         coral_numbers[:, :, :, tax, :] = corals[:, :, :, tax, :];

    # Coral juveniles
    coraljuv_relative = coral_juv_m2 / (
        maxcoraljuv
    )  # convert absolute juvenile numbers to relative measures

    # use built-in relative shelter volume from reefmod engine, but rescale to match values calculated from reefmod-gbr oututs,
    # which use shelter volume from 95cm diameter (circle) plating acroporid as maximum shelter volume, in contrast with reefmod engine which uses
    # a square 100x100cm plating acroporid's shelter volume as maximum.
    shelterVolume = relative_shelter_volume * 9.33
    shelterVolume[shelterVolume > 1] = 1
    shelterVolume[shelterVolume < 0] = 0

    # COTS abundance above critical threshold for outbreak density and relative to max observed
    COTSrel = cots / cots_outbreak_threshold
    COTSrel[COTSrel < 0] = 0
    COTSrel[COTSrel > 1] = 1

    # Convert COTS and rubble to their complementary values
    COTSrel_complementary = 1 - COTSrel  # complementary of COTS
    rubble_complementary = (100 - rubble) / 100  # complementary of rubble

    # Compare ReefMod data against reef condition criteria provided by expert elicitation process (questionnaire)
    crit_val = [0.9, 0.7, 0.5, 0.3]
    ncrits = len(crit_val)

    reefcondition = np.zeros((nsims, nreefs, nyrs))
    rci_mask = np.zeros((nsims, nreefs, nyrs, n_metrics))

    # Start loop for crieria vs metric comparisons
    for curr_crit in range(ncrits):
        # rci_crit is (nsims, 5, 4)
        rci_mask[:, :, :, 0] = total_cover >= rci_crit[:, 0, curr_crit][:, np.newaxis, np.newaxis]
        rci_mask[:, :, :, 1] = shelterVolume >= rci_crit[:, 1, curr_crit][:, np.newaxis, np.newaxis]
        rci_mask[:, :, :, 2] = coraljuv_relative >= rci_crit[:, 2, curr_crit][:, np.newaxis, np.newaxis]
        rci_mask[:, :, :, 3] = COTSrel_complementary >= rci_crit[:, 3, curr_crit][:, np.newaxis, np.newaxis]
        rci_mask[:, :, :, 4] = rubble_complementary >= rci_crit[:, 4, curr_crit][:, np.newaxis, np.newaxis]

        curr_mask = np.sum(rci_mask, axis=3) / n_metrics
        curr_mask[curr_mask < criteria_threshold] = 0
        curr_mask[curr_mask >= criteria_threshold] = crit_val[curr_crit]

        reefcondition += curr_mask

    reefcondition[reefcondition == sum(crit_val)] = 0.9
    reefcondition[reefcondition == sum(crit_val[1:])] = 0.7
    reefcondition[reefcondition == sum(crit_val[2:])] = 0.5
    reefcondition[reefcondition == sum(crit_val[3:])] = 0.3
    reefcondition[reefcondition == 0.0] = 0.1

    return {
        "total_cover": total_cover,
        "shelter_volume": shelterVolume,
        "relative_shelter_volume": relative_shelter_volume,
        "coraljuv_relative": coraljuv_relative,
        "coral_evenness": data["coral_evenness"],
        "COTSrel_complementary": COTSrel_complementary,
        "rubble_complementary": rubble_complementary,
        "RCI": reefcondition,
    }, curr_eco_sim


def rti_rme(
    ecol_indicators,
    rti_intercept,
    rti_cov_slope,
    rti_shelt_slope,
    rti_juv_slope,
    rti_cots_slope,
    rti_rubble_slope,
):
    # Calculate RTI, which is just the RCI made continuous (coefficients calculated previously,
    # by fitting linear regression of discrete RCI to the 6 ecological indicators underpinning it
    # Intercepts are now (nsims,) arrays
    all_reeftourism = (
        rti_intercept[:, np.newaxis, np.newaxis]
        + rti_cov_slope * ecol_indicators["total_cover"]
        + rti_shelt_slope * ecol_indicators["shelter_volume"]
        + rti_juv_slope * ecol_indicators["coraljuv_relative"]
        + rti_cots_slope * ecol_indicators["COTSrel_complementary"]
        + rti_rubble_slope * ecol_indicators["rubble_complementary"]
    )

    all_reeftourism[all_reeftourism > 0.9] = 0.9
    all_reeftourism[all_reeftourism < 0.1] = 0.1

    return all_reeftourism


def rti_3_rme(ecol_indicators, rti_3_intercept_u):
    # Calculate RTI_3, which is a substitute Reef Tourism Index using only coral-related metrics
    intcp = 0.47947 + rti_3_intercept_u
    rti_3 = (
        intcp[:, np.newaxis, np.newaxis]
        + 0.12764 * ecol_indicators["total_cover"]
        + 0.31946 * ecol_indicators["coral_evenness"]
        + 0.11676 * ecol_indicators["relative_shelter_volume"]
        - 0.0036065 * ecol_indicators["coraljuv_relative"]
    )
    return np.round(np.clip(rti_3, 0.1, 0.9), 2)


def rfi_rme(total_cover, intercept1, slope1, intercept2, slope2):
    # Calculate total fish biomass, kg km2, 0.01 coefficient is to convert from kg ha to kg km2
    # Intercepts are now (nsims,) arrays
    complexity = intercept1[:, np.newaxis, np.newaxis] + slope1 * total_cover * 100
    biomass = intercept2[:, np.newaxis, np.newaxis] + slope2 * complexity
    return 0.01 * biomass


def reef_condition_3_metrics_rme(
    results_data,
    scen_ids,
    ecol_uncert,
    sheltervolume_parameters,
    rci_crit,
    maxcoraljuv,
    nsims,
    curr_eco_sim_idx=None,
):
    """
    Calculates reef condition using only 3 metrics (Coral Cover, Shelter Volume, Juveniles).
    Requires at least 2 out of 3 criteria to be met for a category to be satisfied.
    """
    criteria_threshold = 2 / 3
    n_metrics = 3

    data, _ = load_indicator_data(
        results_data, scen_ids, nsims, ecol_uncert, curr_eco_sim_idx
    )

    total_cover = data["total_cover"]
    coral_juv_m2 = data["coral_juv_m2"]
    relative_shelter_volume = data["relative_shelter_volume"]

    nsims_actual, nreefs, nyrs = total_cover.shape

    coraljuv_relative = coral_juv_m2 / maxcoraljuv
    shelterVolume = np.clip(relative_shelter_volume, 0, 1)

    crit_val = [0.9, 0.7, 0.5, 0.3]
    reefcondition = np.zeros((nsims, nreefs, nyrs))
    rci_mask = np.zeros((nsims, nreefs, nyrs, n_metrics))

    for curr_crit in range(len(crit_val)):
        rci_mask[:, :, :, 0] = total_cover >= rci_crit[:, 0, curr_crit][:, np.newaxis, np.newaxis]
        rci_mask[:, :, :, 1] = shelterVolume >= rci_crit[:, 1, curr_crit][:, np.newaxis, np.newaxis]
        rci_mask[:, :, :, 2] = coraljuv_relative >= rci_crit[:, 2, curr_crit][:, np.newaxis, np.newaxis]

        curr_mask = np.sum(rci_mask, axis=3) / n_metrics
        curr_mask = np.where(curr_mask >= criteria_threshold, crit_val[curr_crit], 0)
        reefcondition += curr_mask

    # Final category mapping using tolerance to avoid floating point errors
    reefcondition[reefcondition == sum(crit_val)] = 0.9
    reefcondition[reefcondition == sum(crit_val[1:])] = 0.7
    reefcondition[reefcondition == sum(crit_val[2:])] = 0.5
    reefcondition[reefcondition == sum(crit_val[3:])] = 0.3
    reefcondition[reefcondition < sum(crit_val[3:])] = 0.1

    return reefcondition


def extract_metrics(
    results_data,
    scen_ids,
    nsims,
    uncertainty_dict=None,
    curr_eco_sim_idx=None,
    indicator_param_dict=None,
):
    """
    Calculates indicator metrics for a set of scenarios in the provided ReefModEngine.jl results_data and
    saves in a summary array of size (nsims, nreefs*nyears), suitable to be saved in the economics dataframe
    format.

    Parameters
    ----------
    result_set : dict
        ReefModEngine.jl resultset structure.
    scen_ids : np.array
        List of scenario IDs to consider (e.g. only sample counterfactual/intervention etc.).
    nsims : int
        Number of simulations to sample
    uncertainty_dict : dict
        Contains information of which types of uncertainty to sample when processing metrics.

    Returns
    -------
    save_metrics : np.array
        Array containing the RCI and each of the metrics comprising the RCI, each arrays of size
        (nsims, nreefs*nyears, nmetrics). The nmetrics dimension indices correspond to:
        0 - RCI
        1 - total_cover
        2 - shelter_volume
        3 - coraljuv_relativecoral
        4 - COTSrel_complementary
        5 - rubble_complementary
    """
    if uncertainty_dict is None:
        uncertainty_dict = default_uncertainty_dict()

    years = results_data["timesteps"][:]
    num_years = len(years)
    num_reefs = len(results_data["locations"][:])
    m = num_reefs * num_years

    if indicator_param_dict is None:
        (
            maxcoraljuv,
            sheltervolume_parameters,
            rci_crit,
            rti_intercept,
            rti_cov_slope,
            rti_shelt_slope,
            rti_juv_slope,
            rti_cots_slope,
            rti_rubble_slope,
            rti_3_intercept_u,
            intercept1,
            intercept2,
            slope1,
            slope2,
        ) = indicator_params(results_data, scen_ids, uncertainty_dict=uncertainty_dict, nsims=nsims)
    else:
        maxcoraljuv = indicator_param_dict["maxcoraljuv"]
        sheltervolume_parameters = indicator_param_dict["sheltervolume_parameters"]
        rci_crit = indicator_param_dict["rci_crit"]
        rti_intercept = indicator_param_dict["rti_intercept"]
        rti_cov_slope = indicator_param_dict["rti_cov_slope"]
        rti_shelt_slope = indicator_param_dict["rti_shelt_slope"]
        rti_juv_slope = indicator_param_dict["rti_juv_slope"]
        rti_cots_slope = indicator_param_dict["rti_cots_slope"]
        rti_rubble_slope = indicator_param_dict["rti_rubble_slope"]
        rti_3_intercept_u = indicator_param_dict["rti_3_intercept_u"]
        intercept1 = indicator_param_dict["intercept1"]
        intercept2 = indicator_param_dict["intercept2"]
        slope1 = indicator_param_dict["slope1"]
        slope2 = indicator_param_dict["slope2"]

    # Calculate RCI and ecological indicators
    ecol_indicators, ecol_sample_ids = reef_condition_rme(
        results_data,
        scen_ids,
        uncertainty_dict["ecol_uncert"],
        sheltervolume_parameters,
        rci_crit,
        maxcoraljuv,
        nsims,
        curr_eco_sim_idx=curr_eco_sim_idx,
    )
    ecol_indicators["RCI_3"] = reef_condition_3_metrics_rme(
        results_data,
        scen_ids,
        uncertainty_dict["ecol_uncert"],
        sheltervolume_parameters,
        rci_crit,
        maxcoraljuv,
        nsims,
        curr_eco_sim_idx=ecol_sample_ids,
    )
    ecol_indicators["RTI"] = rti_rme(
        ecol_indicators,
        rti_intercept,
        rti_cov_slope,
        rti_shelt_slope,
        rti_juv_slope,
        rti_cots_slope,
        rti_rubble_slope,
    )
    ecol_indicators["RFI"] = rfi_rme(
        ecol_indicators["total_cover"], intercept1, slope1, intercept2, slope2
    )

    if uncertainty_dict.get("coral_only", 0):
        ecol_indicators["RTI_3"] = rti_3_rme(ecol_indicators, rti_3_intercept_u)

    # save_metrics = np.zeros((nsims, m, len(ecol_indicators)))
    # Extract outputs and convert to long-form format, then save
    for m_key in ecol_indicators:
        ecol_indicators[m_key] = np.reshape(
            ecol_indicators[m_key][:, :, 0:num_years], (-1, m)
        )

    return ecol_indicators, np.array(ecol_sample_ids)

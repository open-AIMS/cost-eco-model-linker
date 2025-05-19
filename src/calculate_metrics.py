import netCDF4 as nc
import pandas as pd
import numpy as np
import random

def default_uncertainty_dict():
    uncertainty_dict = {"ecol_uncert" : 1,
                        "shelt_uncert" : 0,
                        "expert_uncert" : 1,
                        "rti_uncert" : 1,
                        "rfi_uncert" : 1}
    return uncertainty_dict

def indicator_params(result_set, scen_ids, uncertainty_dict=default_uncertainty_dict(), juv_max_years=[0,18], maxcoraljuv=[]):
    """
    Calculates key parameters for shelter volume and RCI calculations given uncertainty sampling choices.

    Parameters
    ----------
        result_set : dict
            ReefModEngne.jl resultset structure.
        scen_ids : np.array
            List of scenario IDs to consider (e.g. only sample counterfactual/intervention etc.).
        shelt_uncert : int (0/1)
            Include shelter volume uncertainty sampling if 1 (not currently available).
        expert_uncert : int (0/1)
            Include expert opinion uncertainty in sampling RCI thresholds if 1, otherwise use mean expert
            thresholds.
        juv_max_years : list
            Indices of years to calculate Juveniles max baseline over.
        maxcoraljuv : np.float
            Max juveniles baseline (can be included instead of using hindcasting baseline).

    Returns
    -------
        maxcoraljuv : np.float
            Maximum juveniles baseline.
        sheltervolume_parameters : np.array
            Parameters for sheltervolume regression models.
        rci_crit : np.array
            Array of thresholds describing reef condition categories.
    """

    # MAXIMUM JUVENILES
    if maxcoraljuv==[]:
        maxcoraljuv = np.max(result_set["nb_coral_juv"][scen_ids, :, juv_max_years[0]:juv_max_years[1]])

    ## SHELTER VOLUME UNCERTAINTY
    if uncertainty_dict["shelt_uncert"] == 0:
        sheltervolume_parameters = np.array([
            [-8.31, 1.47], # branching from Urbina-Barretto 2021
            [-8.32, 1.50], # tabular from Urbina-Barretto 2021
            [-7.37, 1.34], # columnar from Urbina-Barretto 2021, assumed similar for corymbose Acropora
            [-7.37, 1.34], # columnar from Urbina-Barretto 2021, assumed similar for corymbose non-Acropora
            [-9.69, 1.49], # massive from Urbina-Barretto 2021, assumed similar for encrusting and small massives
            [-9.69, 1.49]])    # massive from Urbina-Barretto 2021,  assumed similar for large massives
    else:
        sheltervolume_parameters = np.array([
            [-8.31 + np.random.normal(0,0.514), 1.47], # branching from Urbina-Barretto 2021
            [-8.32 + np.random.normal(0,0.514), 1.50],  # tabular from Urbina-Barretto 2021
            [-7.37 + np.random.normal(0,0.514), 1.34], # columnar from Urbina-Barretto 2021, assumed similar for corymbose Acropora
            [-7.37 + np.random.normal(0,0.514), 1.34], # columnar from Urbina-Barretto 2021, assumed similar for corymbose non-Acropora
            [-9.69 +  np.random.normal(0,0.514), 1.49], # massive from Urbina-Barretto 2021, assumed similar for encrusting and small massives
            [-9.69 +  np.random.normal(0,0.514), 1.49]])    # massive from Urbina-Barretto 2021,  assumed similar for large massives

    if uncertainty_dict["expert_uncert"] == 0: # If there is no uncertainty from survey, use median results
        G = pd.read_csv('.//datasets//Heneghan_RCI.csv')
        rci_crit = np.array(G.loc[:, G.columns[[1, 3, 4, 6 ,8]]])
    else:
        # If there is uncertainty from survey, draw each metric's thresholds randomly from pool of experts
        G = pd.read_csv('.//datasets//ExpertReefCondition_AllResults.csv')
        G = np.array(G.loc[:, G.columns[2:]]) # Cut off first two columns, convert to array

        num_experts = int(G.shape[0]/5) # How many experts
        experts = random.sample(range(num_experts), 6) # Random sample of 7 experts, for each of the 6 metrics

        # Populate rci
        G_len = G.shape[0]
        rci_crit = np.array([G[experts[0]:G_len:num_experts, 0],
                        G[experts[1]:G_len:num_experts, 1],
                        G[experts[2]:G_len:num_experts, 2],
                        G[experts[3]:G_len:num_experts, 3],
                        G[experts[4]:G_len:num_experts, 4],
                        G[experts[5]:G_len:num_experts, 5]])

    ## RTI LINEAR REGRESSION UNCERTAINTY
    if uncertainty_dict["rti_uncert"] == 0:
        rti_intercept = -0.498 # Intercept of rci to rti linear equation
    else:
        rti_intercept = -0.498 + np.random.normal(0, 0.163) # Intercept of rci to rti linear equation

    ## RFI BUILT FROM DIGITISING FIG 4A AND FIG 6B FROM Graham and Nash, 2012 https://doi.org/10.1007/s00338-012-0984-y

    ## RFI LINEAR REGRESSION UNCERTAINTY
    if uncertainty_dict["rfi_uncert"] == 0:
        intercept1 = 1.232 # intercept of coral cover to structural complexity equation
        intercept2 = -1623.6 # intercept of shelter volume to reef fish biomass
    else:
        # Sample intercept from 95% prediction interval
        intercept1 = 1.232 + np.random.normal(0, 0.195) # intercept of coral cover to structural complexity equation
        intercept2 = -1623.6 + np.random.normal(0, 533) # intercept of shelter volume to reef fish biomass


    slope1 = 0.007476 # slope of coral cover to structural complexity equation
    slope2 = 1883.3 # slope of shelter volume to reef fish biomass

    return maxcoraljuv, sheltervolume_parameters, rci_crit, rti_intercept, intercept1, intercept2, slope1, slope2

def reef_condition_rme(results_data, scen_ids, ecol_uncert, sheltervolume_parameters, rci_crit, maxcoraljuv, nsims):
    """
    Calculates reef condition for a set of scenarios in the provided ReefModEngine.jl results_data.

    Parameters
    ----------
        results_data : dict
            ReefModEngne.jl resultset structure.
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
    criteria_threshold = 0.6 # threshold for how many criteria need to be met for category to be satisfied.
    cots_outbreak_threshold = 0.2 # number of CoTS per manta tow to classify as outbreak
    n_metrics = 5 # see below for metrics implemented

    if ecol_uncert == 0: # If we don't want eco model uncertainty, take mean of nsims
        cots = np.mean(results_data["cots"][scen_ids, :, :], axis=0)
        coral_cover_per_taxa = np.mean(results_data["total_taxa_cover"], axis=0)
        # data.nb_coral_adol = np.mean(F.nb_coral_adol, axis=0)
        # data.nb_coral_adult = np.mean(F.nb_coral_adult, axis=0)
        nb_coral_juv = np.mean(results_data["nb_coral_juv"][scen_ids, :, :], axis=0)
        rubble = np.mean(results_data["rubble"][scen_ids, :, :], axis=0)
        relative_shelter_volume = np.mean(results_data["relative"][scen_ids, :, :], axis=0)

    if ecol_uncert == 1: # If we want eco model uncertainty, sample from 20 reefmod simulations
        curr_eco_sim = random.choices(scen_ids, k=nsims)
        cots = results_data["cots"][curr_eco_sim, :, :]
        coral_cover_per_taxa = results_data["total_taxa_cover"][curr_eco_sim, :, :, :]
        nb_coral_juv = results_data["nb_coral_juv"][curr_eco_sim, :, :]
        rubble = results_data["rubble"][curr_eco_sim, :, :]
        relative_shelter_volume = results_data["relative_shelter_volume"][curr_eco_sim, :, :]

    # Extract constants and variables
    nsims, ngroups, nreefs, nyrs  = coral_cover_per_taxa.shape

    juv_sizes = 1
    adol_sizes = 2
    adult_sizes = 3

    if ngroups == 12:
        ntaxa = ngroups/2
    elif ngroups == 6:
        ntaxa = ngroups

    # corals[:, :, :, :, juv_sizes] = data.nb_coral_juv
    # corals[:, :, :, :, adol_sizes] = data.nb_coral_adol
    # corals[:, :, :, :, adult_sizes] = data.nb_coral_adult
    nsizes = 3
    # coral_numbers = np.zeros(nsims, nreefs, nyrs, ntaxa, nsizes)

    # if ngroups == 12:
    #     for tax = 1:6 # Get total numbers of each coral, across unenhanced and enhanced groups
    #         coral_numbers[:, :, :, tax, :] = corals[:, :, :, tax, :] + corals[:,:, :, tax + 6, :]

    # elif ngroups == 6:
    #     for tax = 1:6 # Get total numbers of each coral, across unenhanced and enhanced groups
    #         coral_numbers[:, :, :, tax, :] = corals[:, :, :, tax, :];

    # Calculate total cover
    total_cover = np.sum(coral_cover_per_taxa, axis=1) / 100 # first calculate total coral cover

    # Coral juveniles
    coraljuv_relative = nb_coral_juv/(maxcoraljuv) #convert absolute juvenile numbers to relative measures

    # USE BUILT-IN RELATIVE SHELTER VOLUME FROM REEFMOD, BUT ADJUST TO APPROXIMATELY OUR 0-1 SCALING
    shelterVolume = relative_shelter_volume*10
    shelterVolume[shelterVolume > 1] = 1
    shelterVolume[shelterVolume < 0] = 0

    # COTS abundance above critical threshold for outbreak density and relative to max observed
    COTSrel = cots / cots_outbreak_threshold
    COTSrel[COTSrel < 0] = 0
    COTSrel[COTSrel > 1] = 1

    # Convert COTS and rubble to their complementary values
    COTSrel_complementary = 1 - COTSrel # complementary of COTS
    rubble_complementary = (100 - rubble) / 100 # complementary of rubble

    # Compare ReefMod data against reef condition criteria provided by expert elicitation process (questionnaire)
    crit_val = [0.9, 0.7, 0.5, 0.3]
    ncrits = len(crit_val)

    reefcondition = np.zeros((nsims, nreefs, nyrs))
    rci_mask = np.zeros((nsims, nreefs, nyrs, n_metrics))

    # Start loop for crieria vs metric comparisons
    for curr_crit in range(ncrits):
        rci_mask[:, :, :, 0] = total_cover >= rci_crit[curr_crit, 0]
        rci_mask[:, :, :, 1] = shelterVolume >= rci_crit[curr_crit, 1]
        rci_mask[:, :, :, 2] = coraljuv_relative >= rci_crit[curr_crit, 2]
        rci_mask[:, :, :, 3] = COTSrel_complementary >= rci_crit[curr_crit, 3]
        rci_mask[:, :, :, 4] = rubble_complementary >= rci_crit[curr_crit, 4]

        curr_mask = np.sum(rci_mask, axis=3)/n_metrics
        curr_mask[curr_mask < criteria_threshold] = 0
        curr_mask[curr_mask >= criteria_threshold] = crit_val[curr_crit]

        reefcondition += curr_mask

    crit_thresh = np.cumsum(crit_val)
    reefcondition[reefcondition >= crit_thresh[-4]] = 0.3
    reefcondition[reefcondition >= crit_thresh[-3]] = 0.5
    reefcondition[reefcondition >= crit_thresh[-2]] = 0.7
    reefcondition[reefcondition >= crit_thresh[-1]] = 0.9
    reefcondition[reefcondition == 0] = 0.1

    return {"total_cover": total_cover, "shelter_volume": shelterVolume, "coraljuv_relative": coraljuv_relative, "COTSrel_complementary": COTSrel_complementary, "rubble_complementary": rubble_complementary, "RCI" : reefcondition}

def rti_rme(ecol_indicators, rti_intercept):
    # Calculate RTI, which is just the RCI made continuous (coefficients calculated previously,
    # by fitting linear regression of discrete RCI to the 6 ecological indicators underpinning it
    all_reeftourism = rti_intercept + 0.291*ecol_indicators["total_cover"]
    + 0.628*ecol_indicators["shelter_volume"] + 1.335*ecol_indicators["coraljuv_relative"]
    + 0.212*ecol_indicators["COTSrel_complementary"] + 0.250*ecol_indicators["rubble_complementary"]

    all_reeftourism[all_reeftourism > 0.9] = 0.9
    all_reeftourism[all_reeftourism < 0.1] = 0.1
    return all_reeftourism

def rfi_rme(total_cover, intercept1, slope1, intercept2, slope2):
    # Calculate total fish biomass, kg km2, 0.01 coefficient is to convert from kg ha to kg km2
    return 0.01*(intercept2 + slope2*(intercept1 + slope1*total_cover*100))

def indicator_master(result_set, scen_ids, nsims, uncertainty_dict=default_uncertainty_dict()):
    """
    Calculates indicator metrics for a set of scenarios in the provided ReefModEngine.jl results_data.

    Parameters
    ----------
        result_set : dict
            ReefModEngne.jl resultset structure.
        scen_ids : np.array
            List of scenario IDs to consider (e.g. only sample counterfactual/intervention etc.).
        ecol_uncert : int (0 or 1)
            If 1 includes ecological uncertainty by sampling metrics over climate replicates, if 0 just uses
            mean of metrics over climate replicates.
        shelt_uncert : int (0/1)
            Include shelter volume uncertainty sampling if 1 (not currently available).
        expert_uncert : int (0/1)
            Include expert opinion uncertainty in sampling RCI thresholds if 1, otherwise use mean expert
            thresholds.
        nsims : int
            Number of simulations to sample

    Returns
    -------
        reefcondition : np.array
            Array containing reef condition of size (nsims, nreefs, nyears).
        metrics_dict : np.array
            Structure containing each of the metrics comprising the RCI, each arrays of size (nsims, nreefs, nyears).
    """
    maxcoraljuv, sheltervolume_parameters, rci_crit, rti_intercept, intercept1, intercept2, slope1, slope2 = indicator_params(result_set, scen_ids, uncertainty_dict=uncertainty_dict)

    # Calculate RCI and ecological indicators
    ecol_indicators = reef_condition_rme(result_set, scen_ids, uncertainty_dict["ecol_uncert"], sheltervolume_parameters, rci_crit, maxcoraljuv, nsims)
    ecol_indicators["RTI"] = rti_rme(ecol_indicators, rti_intercept)
    ecol_indicators["RFI"] = rfi_rme(ecol_indicators["total_cover"], intercept1, slope1, intercept2, slope2)

    return ecol_indicators

def extract_metrics(results_data, scen_ids, nsims, uncertainty_dict=default_uncertainty_dict()):
    """
    Calculates indicator metrics for a set of scenarios in the provided ReefModEngine.jl results_data and
    saves in a summary array of size (nsims, nreefs*nyears), suitable to be saved in the economics dataframe
    format.

    Parameters
    ----------
        result_set : dict
            ReefModEngne.jl resultset structure.
        scen_ids : np.array
            List of scenario IDs to consider (e.g. only sample counterfactual/intervention etc.).
        nsims : int
            Number of simulations to sample
        ecol_uncert : int (0 or 1)
            If 1 includes ecological uncertainty by sampling metrics over climate replicates, if 0 just uses
            mean of metrics over climate replicates.
        shelt_uncert : int (0/1)
            Include shelter volume uncertainty sampling if 1 (not currently available).
        expert_uncert : int (0/1)
            Include expert opinion uncertainty in sampling RCI thresholds if 1, otherwise use mean expert
            thresholds.

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
    years = results_data['timesteps'][:]
    num_years = len(years)
    num_reefs = len(results_data['locations'][:])
    m = num_reefs*num_years

    ecol_indicators = indicator_master(results_data, scen_ids, nsims, uncertainty_dict=uncertainty_dict)

    #save_metrics = np.zeros((nsims, m, len(ecol_indicators)))
    # Extract outputs and convert to long-form format, then save
    for m_key in ecol_indicators:
        ecol_indicators[m_key] = np.reshape(ecol_indicators[m_key][:, :, 0:num_years], (nsims, m))

    return ecol_indicators

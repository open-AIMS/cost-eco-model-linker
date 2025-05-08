import netCDF4 as nc
import pandas as pd
import numpy as np
import random

def indicator_params(result_set, scen_ids, shelt_uncert, expert_uncert, juv_max_years=[0,18], maxcoraljuv=[]): #, n_sims
    # MAXIMUM JUVENILES
    if maxcoraljuv==[]:
        maxcoraljuv = np.max(result_set["nb_coral_juv"][scen_ids, :, juv_max_years[0]:juv_max_years[1]])

    ## SHELTER VOLUME UNCERTAINTY
    if shelt_uncert == 0:
        sheltervolume_parameters = np.array([
            [-8.31, 1.47], # branching from Urbina-Barretto 2021
            [-8.32, 1.50], # tabular from Urbina-Barretto 2021
            [-7.37, 1.34], # columnar from Urbina-Barretto 2021, assumed similar for corymbose Acropora
            [-7.37, 1.34], # columnar from Urbina-Barretto 2021, assumed similar for corymbose non-Acropora
            [-9.69, 1.49], # massive from Urbina-Barretto 2021, assumed similar for encrusting and small massives
            [-9.69, 1.49]])    # massive from Urbina-Barretto 2021,  assumed similar for large massives

    if shelt_uncert == 1:
        sheltervolume_parameters = np.array([
            [-8.31 + np.random.normal(0,0.514), 1.47], # branching from Urbina-Barretto 2021
            [-8.32 + np.random.normal(0,0.514), 1.50],  # tabular from Urbina-Barretto 2021
            [-7.37 + np.random.normal(0,0.514), 1.34], # columnar from Urbina-Barretto 2021, assumed similar for corymbose Acropora
            [-7.37 + np.random.normal(0,0.514), 1.34], # columnar from Urbina-Barretto 2021, assumed similar for corymbose non-Acropora
            [-9.69 +  np.random.normal(0,0.514), 1.49], # massive from Urbina-Barretto 2021, assumed similar for encrusting and small massives
            [-9.69 +  np.random.normal(0,0.514), 1.49]])    # massive from Urbina-Barretto 2021,  assumed similar for large massives

    if expert_uncert == 0: # If there is no uncertainty from survey, use median results
        G = pd.read_csv('.//datasets//Heneghan_RCI.csv')
        rci_crit = np.array(G.loc[:, G.columns[[1, 3, 4, 6 ,8]]])

    if expert_uncert == 1: # If there is uncertainty from survey, draw each metric's thresholds randomly from pool of experts
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

    return maxcoraljuv, sheltervolume_parameters, rci_crit

def reef_condition_rme(results_data, scen_ids, ecol_uncert, sheltervolume_parameters, rci_crit, maxcoraljuv, nsims):
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

    return reefcondition, {"total_cover": total_cover, "shelter_volume": shelterVolume, "coraljuv_relative": coraljuv_relative, "COTSrel_complementary": COTSrel_complementary, "rubble_complementary": rubble_complementary}

def indicator_master(result_set, scen_ids, ecol_uncert, shelt_uncert, expert_uncert, nsims):
    maxcoraljuv, sheltervolume_parameters, rci_crit = indicator_params(result_set, scen_ids, shelt_uncert, expert_uncert)

    # Calculate RCI and ecological indicators
    return reef_condition_rme(result_set, scen_ids, ecol_uncert, sheltervolume_parameters, rci_crit, maxcoraljuv, nsims)

def extract_metrics(results_data, scen_ids, nsims, ecol_uncert, shelt_uncert, expert_uncert):
    years = results_data['timesteps'][:]
    num_years = len(years)
    num_reefs = len(results_data['locations'][:])
    m = num_reefs*num_years

    save_metrics = np.zeros((nsims, m, 6))

    [all_reefcondition, ecol_indicators] = indicator_master(results_data, scen_ids, ecol_uncert, shelt_uncert, expert_uncert, nsims)

    # Extract outputs and convert to long-form format, then save
    save_metrics[:, :, 0] = np.reshape(all_reefcondition[:, :, 0:num_years],  (nsims, m))

    for (i_key, m_key) in enumerate(ecol_indicators):
        save_metrics[:, :, i_key+1] = np.reshape(ecol_indicators[m_key][:, :, 0:num_years], (nsims, m))

    return save_metrics

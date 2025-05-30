import netCDF4 as nc
import pandas as pd
import geopandas as gp
import numpy as np
import json

# from reef_distances import find_representative_reefs
from calculate_metrics import extract_metrics, default_uncertainty_dict
from reef_distances import find_max_reef_distance

def load_reef_data():
    """
    Loads key reef spatial data.
    """
    return gp.read_file(".\\datasets\\reefmod_gbr.gpkg")

def load_regions_data(economics_spatial_filepath):
    """
    Loads key economics spatial data.

    Parameters
    ----------
        economics_spatial_filepath : string
            String giving the path to economics spatial data.

    Returns
    -------
        regions_data : dataframe
    """
    regions_data = pd.read_csv(economics_spatial_filepath) # Economic spatial data key
    regions_data["reef_uniqueid"] = [str(i) for i in regions_data["reef_uniqueid"]]
    regions_data = regions_data.rename(columns={'reef_uniqueid':'UNIQUE_ID'})

    return regions_data

def load_result_files(rme_files_path):
    """
    Loads results files generated from running scenarios in ReefModEngine.jl.

    Parameters
    ----------
        rme_files_path : string
            String giving the path to resultset folder.

    Returns
    -------
        results_data : dict
            Dict containing numpy arrays of results data from running ReefModEngine.jl.
        scens_df : dataframe
            Describes scenario parameters year-by-year, including rep, year and intervention levels.
        iv_dict : dict
            Contains other key scenario info, such as whether the scenario is counterfactual or intervention.
    """
    scens_df = pd.read_csv(rme_files_path+"\\iv_yearly_scenarios.csv") # intervention scenarios table
    results_data = nc.Dataset(rme_files_path+"\\results.nc") # Metric results

    # Load struct with interventions data
    with open(rme_files_path+'\\scenario_info.json', 'r') as file:
        iv_dict = json.load(file)

    return results_data, scens_df, iv_dict

def create_base_economics_dataframe(regions_data, reef_spatial_data, years):
    """
    Creates base structure for metrics summary files input to economics modelling.

    Parameters
    ----------
        regions_data : dataframe
            A dataframe with key spatial and economics data for each reef in the GBR (loaded from econ_spatial.csv).
        reef_spatial_data : dataframe
            A dataframe from the RME specified key reef IDs and spatial information (loaded from reefmod_gbr.gpkg).
        years : list
            Years to be included in the economics output file from the ecological modelling.

    Returns
    -------
        data_store: dataframe
            Basic economics file structure to save for each intervention/counterfactual scenario.
    """
    reef_names = regions_data.reef_name
    unique_ids = regions_data.UNIQUE_ID
    n_reefs = len(regions_data.reef_name)

    # Setup base dataframe structure
    columns = ["year_absolute", "year_relative", "Reef_ID"]
    data_store = pd.DataFrame(np.zeros((n_reefs*len(years), len(columns))),  columns=columns)
    data_store["year_absolute"] = np.array(list(years)*n_reefs) # Run year
    data_store["Reef_ID"] = np.repeat(list(range(1, n_reefs+1)), len(years))
    data_store["reef_name"] = np.repeat(np.array(reef_names), len(years))
    data_store["UNIQUE_ID"] = np.repeat(np.array(unique_ids), len(years)) # Unique ID for data join
    data_store["reef_gbrmpa_id"] = np.repeat("", n_reefs*len(years))

    # Add GBRMPA IDs by matching UNIQUE_ID (needed to match up for inner join with economics spatial data)
    for id in unique_ids:
        data_store.loc[data_store["UNIQUE_ID"]==id, "reef_gbrmpa_id"] = reef_spatial_data.loc[reef_spatial_data["UNIQUE_ID"]==id, "RME_GBRMPA_ID"].iloc[0]

    # Add management and other key economics region information and distance to nearest port
    data_store = data_store.merge(regions_data, on='UNIQUE_ID', how = 'inner')

    return data_store

def area_weighted_rti(metrics_dict, metrics_df):
    """
    Processes metrics dict into continuous reef condition weighted by reef area.

    Parameters
    ----------
        metrics_dict : dict
            Dict containing key sampled metrics and the RCI
        metrics_df : dataframe
            Dataframe containing scenario summary dataframe
    """
    return np.transpose(metrics_dict["RTI"]*np.array(metrics_df["total_area_nine_zones"]/np.sum(metrics_df["total_area_nine_zones"])),(1, 0))

def rci(metrics_dict, metrics_df, rci_threshold=0.6):
    """
    Processes metrics dict into area at threshold RCI and above.

    Parameters
    ----------
        metrics_dict : dict
            Dict containing key sampled metrics and the RCI
        metrics_df : dataframe
            Dataframe containing scenario summary dataframe
        rci_threshold : RCI threshold above which to calculate area saved for.
    """
    rci = metrics_dict["RCI"]
    rci[rci >= rci_threshold] = 1
    rci[rci < rci_threshold]  = 0

    return np.transpose(rci*np.array(metrics_df["total_area_nine_zones"]),(1, 0))

def coral_area_saved(metrics_dict, metrics_df):
    """
    Processes metrics dict into total area of coral cover in hectares.

    Parameters
    ----------
        metrics_dict : dict
            Dict containing key sampled metrics and the RCI
        metrics_df : dataframe
            Dataframe containing scenario summary dataframe
    """
    # Convert to hectares by dividaing by 100
    return np.transpose(metrics_dict["total_cover"]*np.array(metrics_df["total_area_nine_zones"]/100),(1, 0))

def rfi(metrics_dict, metrics_df, rfi_thresholds=[0.74, 29.91]):
    """
    Processes metrics dict into area at threshold RFI and above.
    Minimum fish biomass is 0.74 kg km2. This was the minimum observation in the Graham and Nash,
    2012 dataset. Similarly, max fish biomass is 29.91kg km2.

    Parameters
    ----------
        metrics_dict : dict
            Dict containing key sampled metrics and the RFI
        metrics_df : dataframe
            Dataframe containing scenario summary dataframe
        rfi_thresholds : RFI thresholds (min and max fish biomass)
    """
    rfi = metrics_dict["RFI"]
    rfi[rfi < rfi_thresholds[1]] = rfi_thresholds[1]
    rfi[rfi > rfi_thresholds[2]]  = rfi_thresholds[1]

    return np.transpose(rfi, (1, 0))

def raw_rci(metrics_dict, metrics_df):
    """
    Processes metrics dict into raw RCI for table storage.

    Parameters
    ----------
        metrics_dict : dict
            Array containing key sampled metrics and the RCI
        metrics_df : dataframe
            Dataframe containing scenario summary dataframe
    """
    return np.transpose(metrics_dict["RCI"], (1, 0))

def raw_rti(metrics_dict, metrics_df):
    """
    Processes metrics dict into raw RTI for table storage.

    Parameters
    ----------
        metrics_dict : dict
            Array containing key sampled metrics and the RTI
        metrics_df : dataframe
            Dataframe containing scenario summary dataframe
    """

    return np.transpose(metrics_dict["RTI"], (1, 0))

def create_economics_metric_files(rme_files_path, nsims, uncertainty_dict=default_uncertainty_dict(),
                                  metrics = [rci, area_weighted_rti, raw_rci],
                                  max_dist = 25.0,
                                  economics_spatial_filepath='.//datasets//econ_spatial.csv',
                                  econ_storage_path=".//econ_outputs//"):
    """
    Main function for creating metric file summarys for input to economics modelling.

    Parameters
    ----------
        rme_files_path : string
            String giving the path to resultset folder.
        nsims : int
            Number of simulations to sampling (including uncertainty types as specified)
        uncertainty_dict : dict
            Contains information on what uncertainty types to sample.
        max_dist : float
            Maximum distance between reefs within a "cluster". Total distance to port is calculated as distance
            to port for closest reef cluster + distance between each additional further cluster where distance between
            clusters is calculated as distance between the reefs furthest from port in each cluster.
        economics_spatial_filepath : string
            Filepath for economics spatial data (econ_spatial.csv)
        econ_storage_path : string
            Where to store output economics metrics files.

    Returns
    -------
        id_filename : string
            Filename for ID key file, which links economics metrics
    """
    # Load all relevant data
    regions_data = load_regions_data(economics_spatial_filepath)

    # Scenario dataframe and metric results from RME runs
    results_data, scens_df, iv_dict = load_result_files(rme_files_path)

    # Load reef spatial data to cross check reef UNIQUE_ID with GBRMPA_ID
    reef_spatial_data = load_reef_data()

    # Create base dataframe for storing metric results for economics model
    years = results_data["timesteps"][:]
    start_year = years[0]
    end_year = years[-1]

    data_store = create_base_economics_dataframe(regions_data, reef_spatial_data, years)

    # Get unique intervention IDs from result set (unique intervention and climate model)
    intervention_ids = np.unique(scens_df["intervention id"])

    # Extract ids for cf and intervention runs
    unique_iv_scens = np.where(~np.array(iv_dict["counterfactual"]).astype(bool))[0]
    unique_cf_scens = np.where(np.array(iv_dict["counterfactual"]).astype(bool))[0]

    # Setup key table structure used by economics modelling
    id_key_df_store = pd.DataFrame(columns=['ID', 'results_filename', 'intervention_years', 'number_of_1YO_corals', 'port_id', 'distance_to_port_NM', 'intervention_reef_id', 'number_of_species', 'start_year', 'end_year'])

    # Save a csv for each unique intervention, one for cf and one for iv runs
    for iv_idx in intervention_ids:
        # Get scenario table for intervention
        scens_idx = scens_df["intervention id"]==iv_idx
        scens_df_iv = scens_df[scens_idx]
        n_reps = max(scens_df_iv["rep"])

        reefset_names = np.unique(scens_df_iv["reefset"])
        iv_reefs = sum([iv_dict[reefset_name] for reefset_name in reefset_names], [])

        # Year relative starts at 0 on the first year of intervention
        data_store["year_relative"] = data_store["year_absolute"] - min(scens_df_iv["year"])

        # Scenario ids for CF and counterfactual
        iv_scens = unique_iv_scens[(iv_idx*n_reps-2):(iv_idx*n_reps-2)+n_reps]
        cf_scens = unique_cf_scens[(iv_idx*n_reps-2):(iv_idx*n_reps-2)+n_reps]

        new_cols = ["sim_{0}".format(i) for i in range(1,nsims+1)]
        data_store[new_cols] = np.zeros((data_store.shape[0], len(new_cols)))

        # Setup structure for intervention key - links intervention ID and filename to cost model data
        id_key_df = scens_df_iv[["intervention id", "year", "rep", "number of corals"]]
        n_scens_id = id_key_df.shape[0]
        id_key_df["distance_to_port_NM"] = np.zeros((n_scens_id,))
        id_key_df["furthest_representative_reef"] = np.repeat("", (n_scens_id,))
        id_key_df["closest_representative_reef"] = np.repeat("", (n_scens_id,))


        # Add distance to port data to save in intervention key
        [rep_reefs_sort, total_dist] = find_max_reef_distance(reef_spatial_data, regions_data, iv_reefs, max_dist = max_dist)

        # Store furthest and closest reefs in representative clsuters
        id_key_df["furthest_representative_reef"] = rep_reefs_sort[-1]
        id_key_df["closest_representative_reef"] = rep_reefs_sort[0]
        id_key_df["distance_to_port_NM"] = total_dist

        # Extract metrics for intervention and counterfactual scenarios
        metrics_data_iv = extract_metrics(results_data, iv_scens, nsims, uncertainty_dict=uncertainty_dict)
        metrics_data_cf = extract_metrics(results_data, cf_scens, nsims, uncertainty_dict=uncertainty_dict)
        breakpoint()
        for met_func in metrics:
            data_store[new_cols] = met_func(metrics_data_iv, data_store)
            iv_filename = str(iv_idx)+'_intervention_var_'+met_func.__name__+'_ecol0_intervention.csv'
            data_store.to_csv(econ_storage_path+iv_filename)
            data_store[new_cols] = met_func(metrics_data_cf, data_store)
            cf_filename = str(iv_idx)+'_counterfactual_var_'+met_func.__name__+'_ecol0_intervention.csv'
            data_store.to_csv(econ_storage_path+cf_filename)

        # Drop data columns to allow those for next intervention to be added
        data_store = data_store.drop(new_cols, axis=1)

        # Add to record key data for cost modelling
        id_key_df["results_filename"] = 'intervention'+str(iv_idx)+'_metric_name_ecol0_intervention.csv'
        id_key_df["port_id"] = 1 # Doesn't matter because we have distance to port
        id_key_df["number_of_species"] = 6 # Set at 6 as RME
        id_key_df["start_year"] = start_year
        id_key_df["end_year"] = end_year
        id_key_df = id_key_df.rename(columns={'number of corals':'number_of_1YO_corals','intervention id':'ID', 'year':'intervention_years'})
        id_key_df_store = pd.concat([id_key_df_store, id_key_df])

    # Save intervention key for generating cost data file for saved intervention and cf files
    id_filename = ".\\intervention_keys\\intervention_ID_key_"+rme_files_path.split("\\")[-1]+".csv"
    id_key_df_store.to_csv(id_filename)

    return id_filename

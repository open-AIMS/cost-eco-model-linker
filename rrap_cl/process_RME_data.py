import os
from os.path import join as path_join

from itertools import batched

import netCDF4 as nc
import pandas as pd
import geopandas as gp
import numpy as np
import json

from .calculate_metrics import (
    extract_metrics,
    default_uncertainty_dict,
    indicator_params,
)
from .reef_distances import find_representative_port, find_representative_reef

THIS_DIR = os.path.dirname(__file__)


def load_reef_data():
    """
    Loads key reef spatial data.
    """
    return gp.read_file(path_join(THIS_DIR, "datasets", "reefmod_gbr.gpkg"))


def load_regions_data(economics_spatial_filepath: str):
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
    regions_data = pd.read_csv(economics_spatial_filepath)  # Economic spatial data key

    return regions_data


def load_result_files(rme_files_path: str):
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

    # intervention scenarios table
    scens_df = pd.read_csv(path_join(rme_files_path, "iv_yearly_scenarios.csv"))
    results_data = nc.Dataset(path_join(rme_files_path, "results.nc"))  # Metric results

    # Load struct with interventions data
    with open(path_join(rme_files_path, "scenario_info.json"), "r") as file:
        iv_dict = json.load(file)

    return results_data, scens_df, iv_dict


def aggregate_replicates(scens_df: pd.DataFrame):
    """
    Aggregates yearly scenario data across climate replicates (reps).

    This consolidates the scenario dataframe so that there is only one row per
    unique (intervention, GCM, type, year, reefset) combination, with data
    columns averaged across all reps.

    Parameters
    ----------
    scens_df : pd.DataFrame
        Dataframe containing yearly scenario parameters (typically loaded from
        iv_yearly_scenarios.csv).

    Returns
    -------
    aggregated_df : pd.DataFrame
        Dataframe with data columns averaged across reps.
    """
    group_cols = ["intervention id", "GCM name", "type", "year", "reefset"]
    data_cols = ["number of corals", "corals per m2", "intervention area km2"]

    # Ensure columns exist before attempting to aggregate
    available_data_cols = [col for col in data_cols if col in scens_df.columns]

    return scens_df.groupby(group_cols, as_index=False)[available_data_cols].mean()


def create_base_economics_dataframe(
    regions_data: pd.DataFrame, reef_spatial_data: pd.DataFrame, years: list
):
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
    regions_data = regions_data.sort_values(by="Reef_ID", ignore_index=True).copy()

    # Add UNIQUE_ID cross-reference for estimating reef distance to port
    regions_data["UNIQUE_ID"] = reef_spatial_data["UNIQUE_ID"].values

    # Create year dataframe
    years_df = pd.DataFrame(
        {
            "year_absolute": years,
            "year_relative": 0,  # Placeholder if needed later
        }
    )

    # Cross join: each reef with each year
    data_store = regions_data.merge(years_df, how="cross")

    return data_store, regions_data


def area_weighted_rti(metrics_dict: dict, metrics_df: pd.DataFrame):
    """
    Processes metrics dict into continuous reef condition weighted by reef area.

    Parameters
    ----------
    metrics_dict : dict
        Dict containing key sampled metrics and the RCI
    metrics_df : dataframe
        Dataframe containing scenario summary dataframe
    """
    return np.transpose(
        metrics_dict["RTI"]
        * np.array(
            metrics_df["total_area_nine_zones"]
            / np.sum(metrics_df["total_area_nine_zones"])
        ),
        (1, 0),
    )


def rci(metrics_dict: dict, metrics_df: pd.DataFrame, rci_threshold=0.6):
    """
    Processes metrics dict into area at threshold RCI and above.

    Parameters
    ----------
    metrics_dict : dict
        Dict containing key sampled metrics and the RCI
    metrics_df : dataframe
        Dataframe containing scenario summary dataframe
    rci_threshold : float
        RCI threshold (in (0.0, 1.0)) above which to calculate area saved for.
    """
    rci = metrics_dict["RCI"]
    rci[rci >= rci_threshold] = 1
    rci[rci < rci_threshold] = 0

    return np.transpose(rci * np.array(metrics_df["total_area_nine_zones"]), (1, 0))


def rci_3(metrics_dict: dict, metrics_df: pd.DataFrame, rci_threshold=0.6):
    """
    Processes metrics dict into area at threshold RCI_3 and above.

    Parameters
    ----------
    metrics_dict : dict
        Dict containing key sampled metrics and the RCI_3
    metrics_df : dataframe
        Dataframe containing scenario summary dataframe
    rci_threshold : float
        RCI threshold (in (0.0, 1.0)) above which to calculate area saved for.
    """
    rci_3 = metrics_dict["RCI_3"]
    rci_3[rci_3 >= rci_threshold] = 1
    rci_3[rci_3 < rci_threshold] = 0

    return np.transpose(rci_3 * np.array(metrics_df["total_area_nine_zones"]), (1, 0))


def coral_area_saved(metrics_dict: dict, metrics_df: pd.DataFrame):
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
    return np.transpose(
        metrics_dict["total_cover"]
        * np.array(metrics_df["total_area_nine_zones"] / 100),
        (1, 0),
    )


def rfi(metrics_dict: dict, metrics_df: pd.DataFrame, rfi_thresholds=[0.74, 29.91]):
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
    rfi_thresholds : float
        RFI thresholds (min and max fish biomass)
    """
    rfi = metrics_dict["RFI"]
    rfi[rfi < rfi_thresholds[0]] = rfi_thresholds[0]
    rfi[rfi > rfi_thresholds[1]] = rfi_thresholds[1]

    return np.transpose(rfi, (1, 0))


def raw_reefcond(metrics_dict: dict, metrics_df: pd.DataFrame):
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


def raw_reefcond_3(metrics_dict: dict, metrics_df: pd.DataFrame):
    """
    Processes metrics dict into raw RCI_3 for table storage.

    Parameters
    ----------
    metrics_dict : dict
        Array containing key sampled metrics and the RCI_3
    metrics_df : dataframe
        Dataframe containing scenario summary dataframe
    """
    return np.transpose(metrics_dict["RCI_3"], (1, 0))


def raw_rti_3(metrics_dict: dict, metrics_df: pd.DataFrame):
    """
    Processes metrics dict into raw RTI_3 for table storage.

    Parameters
    ----------
    metrics_dict : dict
        Array containing key sampled metrics and the RTI_3
    metrics_df : dataframe
        Dataframe containing scenario summary dataframe
    """
    return np.transpose(metrics_dict["RTI_3"], (1, 0))


def raw_rti(metrics_dict: dict, metrics_df: pd.DataFrame):
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


def coral_cover(metrics_dict: dict, metrics_df: pd.DataFrame):
    """
    Processes metrics dict coral cover for table storage.

    Parameters
    ----------
    metrics_dict : dict
        Array containing key sampled metrics and the RTI
    metrics_df : dataframe
        Dataframe containing scenario summary dataframe
    """
    return np.transpose(metrics_dict["total_cover"], (1, 0))


def shelter_volume(metrics_dict: dict, metrics_df: pd.DataFrame):
    """
    Processes metrics dict into shelter volume for table storage.

    Parameters
    ----------
    metrics_dict : dict
        Array containing key sampled metrics and the RTI
    metrics_df : dataframe
        Dataframe containing scenario summary dataframe
    """
    return np.transpose(metrics_dict["shelter_volume"], (1, 0))


def coraljuv_relative(metrics_dict: dict, metrics_df: pd.DataFrame):
    """
    Processes metrics dict into relative juvenile coral cover for table storage.

    Parameters
    ----------
    metrics_dict : dict
        Array containing key sampled metrics and the RTI
    metrics_df : dataframe
        Dataframe containing scenario summary dataframe
    """
    return np.transpose(metrics_dict["coraljuv_relative"], (1, 0))


def COTSrel_complementary(metrics_dict: dict, metrics_df: pd.DataFrame):
    """
    Processes metrics dict into relative COTS complementary cover for table storage.

    Parameters
    ----------
    metrics_dict : dict
        Array containing key sampled metrics and the RTI
    metrics_df : dataframe
        Dataframe containing scenario summary dataframe
    """
    return np.transpose(metrics_dict["COTSrel_complementary"], (1, 0))


def rubble_complementary(metrics_dict: dict, metrics_df: pd.DataFrame):
    """
    Processes metrics dict into rubble complementary cover for table storage.

    Parameters
    ----------
    metrics_dict : dict
        Array containing key sampled metrics and the RTI
    metrics_df : dataframe
        Dataframe containing scenario summary dataframe
    """
    return np.transpose(metrics_dict["rubble_complementary"], (1, 0))


def create_economics_metric_files(
    rme_files_path: str,
    nsims: int,
    stores,
    nbatches=None,
    uncertainty_dict: dict = None,
    ncores: int = 1,
    metrics=None,
    economics_spatial_filepath=None,
    costs_only=False,
    distance_override_NM: float = None,
    seed: int = None,
    intervention_year_offset: int = 1,
) -> tuple[str, list[str]]:
    """
    Main function for creating metric file summaries for input to economics modelling.

    Parameters
    ----------
    rme_files_path : str
        Path to resultset folder.
    nsims : int
        Number of simulations to sample (including uncertainty types as specified).
    stores : object
        Storage paths object with cost_dir and intervention_keys_dir attributes.
    nbatches : int, optional
        Number of batches. If None, defaults to nsims (single batch).
    uncertainty_dict : dict, optional
        Information on uncertainty types to sample.
    ncores : int, default=1
        Number of cores for output file generation.
    metrics : list, optional
        List of metric functions to calculate. Defaults to [rci, raw_rti, rfi].
    economics_spatial_filepath : str, optional
        Path to economics spatial data (econ_spatial.csv).
    costs_only : boolean, optional
        If True, does not produce indicator metrics (RCI, RFI, RTI). Default is False.
    distance_override_NM : float, optional
        When set, replaces the geographic port-distance calculation for all reefsets with
        this fixed value (in nautical miles). Use this for best-guess explorer runs where
        the config best-point distance should take precedence over computed reef distances.
    seed : int, optional
        Random seed for reproducibility.
    intervention_year_offset : int, default=1
        Number of years to subtract from the RME intervention year when labelling
        costs in the output ID key.  ReefMod Engine records the year in which
        outplanted corals first become *detectable*; the actual production and
        deployment work takes place one year earlier.  The default of 1 therefore
        produces cost outputs whose ``intervention_years`` column reflects the
        calendar year in which costs were actually incurred.  Pass 0 to keep the
        raw RME year unchanged.

    Returns
    -------
    run_id : str
        Base filename identifier for this run.
    metric_filepaths : list
        List of generated metric file paths for each intervention.
    """
    # Set defaults
    if economics_spatial_filepath is None:
        economics_spatial_filepath = path_join(THIS_DIR, "datasets", "econ_spatial.csv")
    if uncertainty_dict is None:
        uncertainty_dict = default_uncertainty_dict()
    if metrics is None:
        metrics = [rci, raw_rti, rfi]

    nbatches = nsims if nbatches is None else nbatches

    # Load all required data
    regions_data = load_regions_data(economics_spatial_filepath)
    results_data, scens_df, iv_dict = load_result_files(rme_files_path)
    try:
        reef_spatial_data = load_reef_data()

        # Extract time information
        years = results_data["timesteps"][:]
        start_year, end_year = years[0], years[-1]

        # Get unique intervention IDs
        intervention_ids = np.unique(scens_df["intervention id"])

        # Separate intervention and counterfactual scenario indices
        is_counterfactual = np.array(iv_dict["counterfactual"], dtype=bool)
        unique_iv_scens = np.where(~is_counterfactual)[0]
        unique_cf_scens = np.where(is_counterfactual)[0]

        # Determine which juvenile variable to use
        if "coral_juv_m2" in results_data.variables:
            juv_var = "coral_juv_m2"
        elif "relative_juveniles" in results_data.variables:
            juv_var = "relative_juveniles"
        else:
            raise ValueError(
                "Neither 'coral_juv_m2' nor 'relative_juveniles' found in results."
            )

        # Calculate a global juvenile baseline using all scenarios over the first few years.
        # This ensures that all management scenarios use the exact same denominator for scaling.
        # We use Year 0 to Year 10 as a safe baseline window.
        global_max_coral_juv = np.max(results_data[juv_var][:, :, 0:10])

        # Create base dataframe structure (without sim columns yet)
        base_data_store, regions_data = create_base_economics_dataframe(
            regions_data, reef_spatial_data, years
        )

        # Setup storage for ecological sample IDs
        store_ecol_ids = np.zeros((nsims, len(intervention_ids)), dtype=int)

        # Generate batch indices
        if nsims != nbatches:
            nmembers = int(np.ceil(nsims / nbatches))
            batch_chunks = list(batched(range(nsims), nmembers))
        else:
            batch_chunks = [
                list(range(nsims)),
            ]

        # Setup filenames for outputs
        ecol_uncert = uncertainty_dict["ecol_uncert"]
        expert_uncert = uncertainty_dict["expert_uncert"]
        base_met_filename = (
            f"_uncertainty_ecol{ecol_uncert}_indicator{expert_uncert}_var_"
        )

        run_id = os.path.basename(rme_files_path) + "_run"
        id_filename = path_join(
            stores.intervention_keys_dir, f"intervention_ID_key_{run_id}"
        )
        ecol_id_filename = path_join(
            stores.intervention_keys_dir, f"intervention_rep_idx_{run_id}"
        )

        # Storage for results
        metric_filepaths = []
        id_key_dfs = []
        from tqdm import tqdm

        # Process each intervention
        for iv_idx, iv_id in enumerate(
            tqdm(intervention_ids, desc="Calculating metrics")
        ):
            # Filter scenarios for this intervention
            scens_df_iv = scens_df[scens_df["intervention id"] == iv_id].copy()
            original_n_reps = scens_df_iv["rep"].max()

            # If ecological uncertainty is disabled, aggregate across reps to provide
            # expected value intervention levels for the cost model (Plan B)
            if ecol_uncert == 0:
                scens_df_iv_costs = aggregate_replicates(scens_df_iv)
                scens_df_iv_costs["rep"] = 1
                n_reps = 1
            else:
                scens_df_iv_costs = scens_df_iv
                n_reps = original_n_reps

            # Get intervention reefs
            reefset_names = scens_df_iv_costs["reefset"].unique()
            iv_reefs = sum(
                [iv_dict[reefset_name] for reefset_name in reefset_names], []
            )

            # Calculate relative year (0 on first intervention year)
            intervention_start = scens_df_iv_costs["year"].min()
            intervention_start_idx = np.where(years == intervention_start)[0][0]
            data_store = base_data_store.copy()
            data_store["year_relative"] = (
                data_store["year_absolute"] - intervention_start
            )

            # Get scenario indices for this intervention and its counterfactual
            scen_id_start = iv_idx * original_n_reps
            scen_id_end = scen_id_start + original_n_reps
            iv_scens = unique_iv_scens[scen_id_start:scen_id_end]
            if "counterfactual_mapping" in iv_dict:
                # Map intervention scenarios to counterfactuals using the provided mapping
                cf_scens = np.array(iv_dict["counterfactual_mapping"])[iv_scens] - 1
            else:
                cf_scens = unique_cf_scens[scen_id_start:scen_id_end]

            # Create intervention key dataframe
            scen_cols = [
                "intervention id",
                "year",
                "rep",
                "number of corals",
                "type",
                "reefset",
            ]
            id_key_df = scens_df_iv_costs[scen_cols].assign(
                port_name="", distance_to_port_NM=0.0, reef=""
            )

            # Determine distance to nearest port and representative reef per reefset
            for rs_name in reefset_names:
                rs_reefs = iv_dict[rs_name]
                rs_port_name, rs_distance_NM = find_representative_port(
                    reef_spatial_data, rs_reefs
                )
                rs_reef_name = find_representative_reef(reef_spatial_data, rs_reefs)
                rs_mask = id_key_df["reefset"] == rs_name
                id_key_df.loc[rs_mask, "port_name"] = rs_port_name
                id_key_df.loc[rs_mask, "reef"] = rs_reef_name
                id_key_df.loc[rs_mask, "distance_to_port_NM"] = (
                    distance_override_NM
                    if distance_override_NM is not None
                    else rs_distance_NM
                )

            # Process batches
            batch_files = []

            # Shared template for all simulations
            data_store.to_parquet(
                path_join(stores.cost_dir, "sim_template.parq"), index=False
            )

            for batch_idx, batch_sel in enumerate(batch_chunks):
                ds = pd.DataFrame()

                # Derive unique seed per batch for reproducibility, synchronized across interventions.
                # Use a default seed if none is provided to ensure synchronization across IDs.
                _seed = seed if seed is not None else 42
                _batch_seed = _seed + batch_idx

                # Extract metrics for intervention and counterfactual
                (
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
                ) = indicator_params(
                    results_data,
                    iv_scens,
                    uncertainty_dict=uncertainty_dict,
                    max_coral_juv=global_max_coral_juv,
                    seed=_batch_seed,
                    nsims=len(batch_sel),
                )

                indicator_params_dict = {
                    "maxcoraljuv": max_coral_juv,
                    "sheltervolume_parameters": sheltervolume_parameters,
                    "rci_crit": rci_crit,
                    "rti_intercept": rti_intercept,
                    "rti_cov_slope": rti_cov_slope,
                    "rti_shelt_slope": rti_shelt_slope,
                    "rti_juv_slope": rti_juv_slope,
                    "rti_cots_slope": rti_cots_slope,
                    "rti_rubble_slope": rti_rubble_slope,
                    "rti_3_intercept_u": rti_3_intercept_u,
                    "intercept1": intercept1,
                    "intercept2": intercept2,
                    "slope1": slope1,
                    "slope2": slope2,
                }

                if not costs_only:
                    metrics_data_iv, ecol_ids = extract_metrics(
                        results_data,
                        iv_scens,
                        len(batch_sel),
                        uncertainty_dict=uncertainty_dict,
                        curr_eco_sim_idx=None,
                        indicator_param_dict=indicator_params_dict,
                    )

                    metrics_data_cf, _ = extract_metrics(
                        results_data,
                        cf_scens,
                        len(batch_sel),
                        uncertainty_dict=uncertainty_dict,
                        curr_eco_sim_idx=ecol_ids
                        - original_n_reps,  # Use same ecological sample for counterfactual as for intervention, adjusting for scenario index shift
                        indicator_param_dict=indicator_params_dict,  # Use same indicator params for counterfactual as for intervention
                    )

                    # Adjust ecological IDs to ignore counterfactuals in cost sampling
                    # We map the global NetCDF scenario index back to a local replicate index (0 to original_n_reps-1)
                    store_ecol_ids[batch_sel, iv_idx] = ecol_ids % original_n_reps

                    # Prepare simulation columns for this batch
                    sim_cols = [f"sim_{b + 1}" for b in batch_sel]

                    # Calculate and save metrics for each metric function
                    for met_func in metrics:
                        fn_suffix = (
                            f"{base_met_filename}{met_func.__name__}_batch{batch_idx}"
                        )

                        # Intervention results
                        iv_results = met_func(metrics_data_iv, data_store)
                        iv_filename = f"ID{iv_id}_intervention{fn_suffix}.parq"
                        ds[sim_cols] = iv_results

                        ds.to_parquet(
                            path_join(stores.cost_dir, iv_filename),
                            index=False,
                            compression=None,
                        )
                        batch_files.append(iv_filename)

                        # Counterfactual results (reuse the same dataframe)
                        cf_results = met_func(metrics_data_cf, data_store)
                        cf_filename = f"ID{iv_id}_counterfactual{fn_suffix}.parq"
                        ds[sim_cols] = cf_results

                        ds.to_parquet(
                            path_join(stores.cost_dir, cf_filename),
                            index=False,
                            compression=None,
                        )
                        batch_files.append(cf_filename)

            # Finalize intervention key with metadata
            id_key_df = id_key_df.assign(
                results_filename=f"ID{iv_id}_{base_met_filename}",
                number_of_groups=6,
                start_year=start_year,
                end_year=end_year,
                climate_model=scens_df_iv_costs["GCM name"].values,
            ).rename(
                columns={
                    "number of corals": "number_of_1YO_corals",
                    "intervention id": "ID",
                    "year": "intervention_years",
                }
            )

            # Shift intervention years back by offset: RME records the year corals
            # become detectable, but production/deployment occurs one year earlier.
            # The ecological indexing (intervention_start) is left on the original
            # RME year so that results.nc lookups remain correct.
            id_key_df["intervention_years"] -= intervention_year_offset

            id_key_dfs.append(id_key_df)
            metric_filepaths.append(batch_files)

        # Combine all intervention keys
        id_key_df_all = pd.concat(id_key_dfs, ignore_index=True)

        # Save intervention key and ecological ID files (one per core)
        for core_idx in range(ncores):
            id_key_df_all.to_csv(f"{id_filename}{core_idx}.csv", index=False)

            pd.DataFrame(
                store_ecol_ids[nbatches * core_idx : nbatches * (core_idx + 1), :] + 1,
                columns=[str(id_val) for id_val in intervention_ids],
            ).to_csv(f"{ecol_id_filename}{core_idx}.csv", index=False)

        # Write a single combined file covering all sims across all cores, for use by CREAM
        pd.DataFrame(
            store_ecol_ids + 1,
            columns=[str(id_val) for id_val in intervention_ids],
        ).to_csv(f"{ecol_id_filename}_combined.csv", index=False)
    finally:
        results_data.close()

    return os.path.basename(rme_files_path), metric_filepaths

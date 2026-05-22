import os
from os.path import join as path_join

import math

from .setup_results import OutputStores
from . import process_RME_data as prd
from . import cost_calculations as cc
import pandas as pd
import numpy as np

THIS_DIR = os.path.dirname(__file__)


def para_sample_econ(
    rme_files_path: str,
    nsims: int,
    stores: OutputStores,
    ncores=5,
    uncertainty_dict=None,
    metrics=None,
    coral_only=False,
):
    """
    Run economics metrics data creation files so that corresponding cost data can be sampled in parallel.
    Saves ID key files so that these are available for all cores while sampling cost models in parallel.
    Also saves scenario references so that parallel samples process the correct scenario sims.

    Parameters
    ----------
    rme_files_path : string
        String giving the path to resultset folder.
    nsims : int
        Number of simulations to sampling (including uncertainty types as specified)
    stores : OutputStores
        Data class holding output file paths where economic metric files will be stored.
    ncores : int
        Number of cores to sample cost models over.
    uncertainty_dict : dict
        Contains information on what uncertainty types to sample.
    coral_only : bool, default=False
        If True, only use coral-related metrics (RCI_3 and RFI), excluding COTS and Rubble.
    """
    nbatches = math.ceil(nsims / ncores)

    economics_spatial_filepath = path_join(THIS_DIR, "datasets", "econ_spatial.csv")

    if metrics is None:
        if coral_only:
            print("Using 3-metric RCI and RTI_3 (coral-only mode).")

            # Wrapper to use rci_3 logic but keep 'rci' name for filename mapping
            def rci(metrics_dict, metrics_df):
                return prd.rci_3(metrics_dict, metrics_df)

            # Wrapper to use rti_3 logic but keep 'raw_rti' name for filename mapping
            def raw_rti(metrics_dict, metrics_df):
                return prd.raw_rti_3(metrics_dict, metrics_df)

            metrics = [rci, raw_rti, prd.rfi]
        else:
            metrics = [prd.rci, prd.raw_rti, prd.rfi]

    if uncertainty_dict is None:
        uncertainty_dict = prd.default_uncertainty_dict()
    uncertainty_dict["coral_only"] = 1 if coral_only else 0

    # Create metric datafiles for economics modelling and extract filename for intervention key
    # Files are created separately for each core (as a large number of runs can cause memory issues
    # when calculating metrics due to handling large metrics datacubes)
    int_keys_fn, metric_filepaths = prd.create_economics_metric_files(
        rme_files_path,
        nsims,
        stores,
        nbatches=nbatches,
        ncores=ncores,
        metrics=metrics,
        uncertainty_dict=uncertainty_dict,
        economics_spatial_filepath=economics_spatial_filepath,
    )

    # Post process metrics to be in single file
    for filepaths in metric_filepaths:
        for sim_type in ["intervention", "counterfactual"]:
            file_list = [fn for fn in filepaths if sim_type in fn]
            post_process_metrics(stores, file_list, metrics, nsims)

    os.remove(path_join(stores.cost_dir, "sim_template.parq"))

    return int_keys_fn, nbatches


def post_process_metrics(
    stores: OutputStores, metric_filepaths: list[str], metrics: list, nsims: int
):
    """
    When running multiple cores for cost sampling, metrics calculations are also broken into batches
    to avoid memory issues when creating large metrics datacubes (have shape nsims*nyears*nreefs)

    Writes metric results NOT as a "large metrics datacubes" but as a flat CSV.

    Parameters
    ----------
    stores : OutputStore
        Data class defining output directories
    metric_filepaths : list{string}
        List of all filepaths where metrics are saved.
    metrics : list{function}
        List of metric functions which were calculated.
    nsims : int
        Total number of simulations runs

    Returns
    -------
    None
    """
    _metric_dir_map = {
        "rci": stores.rci_dir,
        "rci_3": stores.rci_dir,
        "rfi": stores.rfi_dir,
        "raw_rti": stores.rti_dir,
        "raw_rti_3": stores.rti_dir,
    }

    cost_dir = stores.cost_dir
    init_metric_df = pd.read_parquet(path_join(cost_dir, "sim_template.parq"))
    sim_cols = [f"sim_{i}" for i in range(1, nsims + 1)]
    metric_df = pd.DataFrame(
        np.zeros((init_metric_df.shape[0], nsims), dtype=np.float64), columns=sim_cols
    )

    for metric_f in metrics:
        file_list = [fn for fn in metric_filepaths if f"_{metric_f.__name__}_" in fn]
        for metrics_file in file_list:
            met_file = path_join(cost_dir, metrics_file)
            met_temp = pd.read_parquet(met_file)

            metric_data_cols = met_temp.columns
            metric_df[metric_data_cols] = met_temp[metric_data_cols]

        metric_out = pd.concat((init_metric_df, metric_df), axis=1)

        # Extract common part of filename and route to metric-specific directory
        fn = f"{file_list[0].split('_batch')[0]}.parq"
        out_dir = _metric_dir_map.get(metric_f.__name__)
        if out_dir is None:
            raise ValueError(
                f"No output directory configured for metric '{metric_f.__name__}'. "
                f"Known metrics: {list(_metric_dir_map)}"
            )
        out_file = path_join(out_dir, fn)
        metric_out.to_parquet(out_file, index=False)

        for fn in file_list:
            os.remove(path_join(cost_dir, fn))

def get_draw_id(p_id, rep_id, draw_id, nsims, ndraws_per_worker):

    return int((rep_id - 1) * nsims + p_id * ndraws_per_worker + (draw_id))

def post_process_costs(result, nsims):
    """
    Combine parallel cost outputs into a single file per rep,
    with draw numbering global across reps and processes.

    Global ordering:
        rep0: proc0 → proc1 → ...
        rep1: proc0 → proc1 → ...
    """

    n_procs = len(result)
    n_iv = len(result[0])

    ndraws_per_worker = math.ceil(nsims / n_procs)

    global_draw = 0  # NEVER resets

    for iv_id in range(n_iv):

        # Reference frame
        base_df = pd.read_csv(result[0][iv_id])
        save_fn = result[0][iv_id].split("id")[0][:-6] + ".csv"

        base_cols = base_df[["year", "component"]]
        chunks = [base_cols]

        draws_in_rep = 0

        for proc_id in range(n_procs):
            fn = result[proc_id][iv_id]
            df = pd.read_csv(fn)

            # Enforce identical rows
            if not df[["year", "component"]].equals(base_cols):
                raise ValueError(
                    f"Row mismatch in proc {proc_id}, rep {iv_id}"
                )

            draw_df = df.iloc[:, 2:]
            cols = draw_df.columns
            extracted = cols.str.extract(r"draw(\d+)_rep(\d+)").astype(int)

            # convert to lists
            draws = extracted[0].tolist()
            reps  = extracted[1].tolist()
            draw_names = [f"draw{get_draw_id(
                proc_id, r, d, nsims, ndraws_per_worker
            )}" for (d, r) in zip(draws, reps)]

            n_local = draw_df.shape[1]

            draw_df = draw_df.copy()
            draw_df.columns = draw_names

            global_draw += n_local
            draws_in_rep += n_local
            chunks.append(draw_df)

            os.remove(fn)

        out_df = pd.concat(chunks, axis=1)

        fixed_cols = ["year", "component"]

        draw_cols = (
            out_df
            .columns
            .drop(fixed_cols)
            .to_series()
            .str.extract(r"draw(\d+)")
            .astype(int)[0]
            .sort_values()
        )

        ordered_cols = fixed_cols + [f"draw{i}" for i in draw_cols]

        out_df = out_df[ordered_cols]
        out_df.to_csv(save_fn, index=False)

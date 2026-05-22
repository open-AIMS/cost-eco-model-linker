import os
from functools import partial
import shutil
import tempfile

import re
from packaging.version import Version

import numpy as np
import pandas as pd
from SALib import ProblemSpec

import multiprocess as mp

from . import process_RME_data as prd
from .parallel_cost_sampling import post_process_metrics, post_process_costs
from .calculate_metrics import default_uncertainty_dict
from . import (
    setup_dirs,
    create_economics_metric_files,
    calculate_costs,
)

from .handlers import open_excel, close_excel, reset_workbook

from .sampling import (
    load_internal_config,
    calculate_production_cost,
    calculate_deployment_cost,
    calculate_lm_cost,
    convert_factor_types,
    apply_discrete_mapping,
    problem_spec,
    get_NK,
    _pool_initializer,
    _fix_main_spec,
    _apply_lm_inventory_model,
    THIS_DIR,
)

SEMVER_RE = re.compile(r"^(?:.*[/\\])?(\d+\.\d+\.\d+)\b")

_KNOWN_MODEL_TYPES = {
    "production": "prod",
    "deployment": "deploy",
    "lm": "LM",
}


def _parse_model_info(
    workbook_path: str,
    model_type: str | None = None,
) -> tuple[str, str]:
    """
    Extract model type and version from a workbook filename.

    The expected filename pattern is ``'<version> <type> <name>'``,
    e.g. ``'3.9.1 CA Production Model.xlsx'``.

    Parameters
    ----------
    workbook_path : str
        Path to the workbook (only the filename stem is inspected).
    model_type : str, optional
        If provided, skips type inference and only extracts the version.

    Returns
    -------
    model_type : str
        ``"production"`` or ``"deployment"``.
    version : str
        Semantic version string, e.g. ``"3.9.1"``.

    Raises
    ------
    ValueError
        If the version cannot be extracted, or if ``model_type`` is ``None``
        and the type cannot be inferred from the filename.
    """
    stem = os.path.splitext(os.path.basename(workbook_path))[0]

    m = re.search(r"\d+\.\d+\.\d+", stem)
    if not m:
        raise ValueError(
            f"Could not extract a version number from filename {stem!r}. "
            "Expected pattern: '<version> <type> <name>', "
            "e.g. '3.9.1 CA Production Model.xlsx'."
        )
    version = m.group(0)

    if model_type is None:
        stem_lower = stem.lower()
        for name in _KNOWN_MODEL_TYPES:
            if name in stem_lower:
                model_type = name
                break
        else:
            raise ValueError(
                f"Could not infer model type from filename {stem!r}. "
                f"Expected one of {list(_KNOWN_MODEL_TYPES)} in the filename, "
                "or pass model_type explicitly."
            )
    elif model_type not in _KNOWN_MODEL_TYPES:
        raise ValueError(
            f"Unknown model_type {model_type!r}. "
            f"Must be one of {list(_KNOWN_MODEL_TYPES)}."
        )

    return model_type, version


def evaluate(
    rme_files_path: str,
    nsims: int,
    deploy_model_fn: str,
    prod_model_fn: str,
    lm_model_fn: str,
    results_dir: str,
    metrics: list = None,
    uncertainty_dict: dict = None,
    active_models: set = None,
    nprocs: int = 1,
    costs_only: bool = False,
    sample_scale: bool = False,
    distance_override_NM: float = None,
    coral_only: bool = False,
    seed: int = None,
) -> list[str]:
    """
    Evaluate costs of intervention scenarios.

    Parameters
    ----------
    rme_files_path : str
        Path to ReefMod Engine results.
    nsims : int
        Number of simulations to evaluate.
    deploy_model_fn : str
        Path to deployment spreadsheet model, including filename but excluding file extension.
    prod_model_fn : str
        Path to production spreadsheet model, including filename but excluding file extension.
    results_dir : str
        Path to directory for storing results.
    lm_model_fn : str
        Path to LM spreadsheet model, including filename but excluding file extension.
    metrics : list, optional
        List of metrics to calculate. Default is None.
    uncertainty_dict : dict, optional
        Dictionary specifying uncertainty parameters. Default is None.
    active_models : set, optional
        Intervention types to include in cost calculations.  Valid values are
        ``"outplant"`` and ``"lm"``.  Defaults to both when ``None``.
        Pass ``{"outplant"}`` for CA-only or ``{"lm"}`` for LM-only scenarios.
    nprocs : int, optional
        Number of parallel worker processes for cost sampling.  Each worker
        receives ``ceil(nsims / nprocs)`` draws.  Defaults to 1 (serial).
    costs_only : bool, optional
        When ``True``, skips post-processing of ecological metric files.
        Use this when only cost outputs are needed (e.g. in
        ``run_cost_exploration``).  Defaults to ``False``.
    distance_override_NM : float, optional
        When set, replaces the geographic port-distance calculation with this
        fixed value (nautical miles) for all reefsets.  Useful for best-guess
        explorer runs where the config best-point distance (e.g. 27 NM North,
        54 NM Centre) should be used instead of the computed reef distance.
    coral_only : bool, default=False
        If True, only use coral-related metrics (RCI_3 and RFI), excluding COTS and Rubble.
    seed : int, optional
        Random seed for reproducibility.

    Returns
    -------
    list[str]
        Paths to result files.
    """
    if nsims % nprocs != 0:
        raise ValueError(
            f"nsims ({nsims}) must be exactly divisible by nprocs ({nprocs})"
        )

    # On Windows, multiprocessing's spawn start method re-imports __main__ in
    # each worker during bootstrap. If the user calls evaluate() at the top
    # level of their script without a __name__ == '__main__' guard, this
    # function would be re-entered from every worker. Python sets _inheriting=True
    # on the current process exactly during this bootstrapping phase, so we
    # detect that and return early before any Pool() call is attempted.
    if getattr(mp.current_process(), "_inheriting", False):
        return []

    if not isinstance(nsims, int) or nsims < 1:
        raise ValueError(f"nsims must be a positive non-zero integer, got {nsims!r}")

    # Check available config aligns with model versions
    m = SEMVER_RE.match(deploy_model_fn)
    deploy_m_ver = Version(m.group(1)) if m else None
    if deploy_m_ver is None:
        raise ValueError(f"No version info found in filename {deploy_model_fn}")

    m = SEMVER_RE.match(prod_model_fn)
    prod_m_ver = Version(m.group(1)) if m else None
    if prod_m_ver is None:
        raise ValueError(f"No version info found in filename {prod_model_fn}")

    # Check that relevant config files exist
    try:
        load_internal_config(f"{str(deploy_m_ver)}_deploy_config.csv")
    except FileNotFoundError:
        raise ValueError(
            f"No config available for deployment model {str(deploy_m_ver)}"
        )

    try:
        load_internal_config(f"{str(prod_m_ver)}_prod_config.csv")
    except FileNotFoundError:
        raise ValueError(f"No config available for production model {str(prod_m_ver)}")

    m = SEMVER_RE.match(lm_model_fn)
    lm_m_ver = Version(m.group(1)) if m else None
    if lm_m_ver is None:
        raise ValueError(f"No version info found in filename {lm_model_fn}")
    try:
        load_internal_config(f"{str(lm_m_ver)}_LM_config.csv")
    except FileNotFoundError:
        raise ValueError(f"No config available for LM model {str(lm_m_ver)}")

    # Setup data stores
    stores = setup_dirs(results_dir)

    # Set defaults
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
        uncertainty_dict = default_uncertainty_dict()
    uncertainty_dict["coral_only"] = 1 if coral_only else 0

    if nprocs > 1:
        # Parallel path: split nsims across nprocs workers; each worker gets
        # nbatches_per_core draws and reads its own ID key file (p_iter_id).
        nbatches_per_core = int(np.ceil(nsims / nprocs))

        int_keys_fn, metric_fps = create_economics_metric_files(
            rme_files_path,
            nsims,
            stores,
            nbatches=nbatches_per_core,
            ncores=nprocs,
            metrics=metrics,
            distance_override_NM=distance_override_NM,
            uncertainty_dict=uncertainty_dict,
            costs_only=costs_only,
            seed=seed,
        )
        from tqdm import tqdm

        if not costs_only:
            for filepaths in tqdm(metric_fps, desc="Post-processing metrics"):
                for filetype in ["intervention", "counterfactual"]:
                    file_list = [fn for fn in filepaths if filetype in fn]
                    post_process_metrics(stores, file_list, metrics, nsims)

        os.remove(os.path.join(stores.cost_dir, "sim_template.parq"))

        _fix_main_spec()
        wrapper = partial(
            calculate_costs,
            stores,
            int_keys_fn,
            nbatches_per_core,
            deploy_model_fn,
            prod_model_fn,
            lm_model_fn,
            0.25,  # cont_p
            active_models=active_models,
            sample_scale=sample_scale,
            seed=seed,
        )
        with mp.Pool(nprocs, initializer=_pool_initializer) as pool:
            result = pool.map(wrapper, range(nprocs))
        post_process_costs(result, nsims)

        scen_ids = [
            int(re.search(r"ID(\d+)_", os.path.basename(fp)).group(1))
            for fp in result[0]
        ]
        _combine_parallel_outputs(stores.cost_dir, scen_ids, nprocs, nbatches_per_core)

        return [path for worker_paths in result for path in worker_paths]
    else:
        # Serial path
        int_keys_fn, metric_fps = create_economics_metric_files(
            rme_files_path,
            nsims,
            stores,
            metrics=metrics,
            uncertainty_dict=uncertainty_dict,
            costs_only=costs_only,
            distance_override_NM=distance_override_NM,
            seed=seed,
        )
        from tqdm import tqdm

        if not costs_only:
            for filepaths in tqdm(metric_fps, desc="Post-processing metrics"):
                for filetype in ["intervention", "counterfactual"]:
                    file_list = [fn for fn in filepaths if filetype in fn]
                    post_process_metrics(stores, file_list, metrics, nsims)

        os.remove(os.path.join(stores.cost_dir, "sim_template.parq"))

        result_paths = calculate_costs(
            stores,
            int_keys_fn,
            nsims,
            deploy_model_fn,
            prod_model_fn,
            lm_model_fn,
            active_models=active_models,
            sample_scale=sample_scale,
            seed=seed,
        )
        post_process_costs([result_paths], nsims)
        scen_ids = [
            int(re.search(r"ID(\d+)_", os.path.basename(fp)).group(1))
            for fp in result_paths
        ]

        _combine_parallel_outputs(
            stores.cost_dir, scen_ids, nprocs=1, nbatches_per_core=nsims
        )
        return result_paths


def parallel_evaluate(
    rme_files_path: str,
    nsims: int,
    ncores: int,
    deploy_model_fn: str,
    prod_model_fn: str,
    lm_model_fn: str,
    results_dir: str,
    metrics: list = None,
    uncertainty_dict: dict = None,
    coral_only: bool = False,
):
    stores = setup_dirs(results_dir)

    # Set defaults
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
        uncertainty_dict = default_uncertainty_dict()

    # Create economics metrics input files, get number of batches needed to complete nsims over ncores
    from .parallel_cost_sampling import para_sample_econ

    int_keys_fn, nbatches = para_sample_econ(
        rme_files_path,
        nsims,
        stores,
        ncores=ncores,
        metrics=metrics,
        uncertainty_dict=uncertainty_dict,
        coral_only=coral_only,
    )

    nbatches_per_core = int(np.ceil(nsims / ncores))

    # Run cost sampling in parallel on ncores
    _fix_main_spec()
    wrapper = partial(
        calculate_costs,
        stores,
        int_keys_fn,
        nbatches_per_core,
        deploy_model_fn,
        prod_model_fn,
        lm_model_fn,
        0.25,  # cont_p
        active_models=None,
        sample_scale=False,
    )
    with mp.Pool(ncores, initializer=_pool_initializer) as pool:
        result = pool.map(wrapper, range(ncores))

    post_process_costs(result, nsims)

    scen_ids = [
        int(re.search(r"ID(\d+)_", os.path.basename(fp)).group(1)) for fp in result[0]
    ]
    _combine_parallel_outputs(stores.cost_dir, scen_ids, ncores, nbatches_per_core)

    return [path for worker_paths in result for path in worker_paths]


def _combine_parallel_outputs(cost_dir, scen_ids, nprocs, nbatches_per_core):
    """Combine per-worker cost overview and EIA files after a parallel run.

    Overview files are concatenated with draw IDs renumbered sequentially
    across workers.  EIA files (raw, proportional, scaled) are averaged across
    workers — worker 0 writes the clean filename; workers 1+ append a
    ``_pid{i}`` suffix.  All per-worker files are deleted after combination.

    Parameters
    ----------
    cost_dir : str
        Directory containing per-worker output files.
    scen_ids : list[int]
        Scenario IDs whose files should be combined.
    nprocs : int
        Number of workers used.
    nbatches_per_core : int
        Draws per worker (used to compute per-worker draw offsets).
    """
    _meta_cols = ["iteration", "year", "intervention", "location", "type"]

    for scen_id in scen_ids:
        # --- Overview: concat all workers, renumber draws sequentially ---
        overview_dfs = []
        for i in range(nprocs):
            fp = os.path.join(cost_dir, f"ID{scen_id}_cost_overview_iter_pid{i}.csv")
            if os.path.exists(fp):
                df = pd.read_csv(fp)
                df["draw"] += i * nbatches_per_core
                overview_dfs.append(df)
                os.remove(fp)
        if overview_dfs:
            combined_ov = pd.concat(overview_dfs, ignore_index=True).sort_values(
                ["intervention_id", "rep_id", "year", "draw"]
            )
            combined_ov.to_csv(
                os.path.join(cost_dir, f"ID{scen_id}_cost_overview.csv"),
                index=False,
            )

        # --- EIA: average numeric columns across all workers then write clean names ---
        for file_type in ["raw", "proportional", "scaled"]:
            for label in ["production", "deployment", "lm"]:
                worker_fps = [
                    os.path.join(
                        cost_dir, f"EIA_{file_type}_ID{scen_id}_{label}_pid{i}.csv"
                    )
                    for i in range(nprocs)
                ]
                present = [fp for fp in worker_fps if os.path.exists(fp)]
                if not present:
                    continue

                dfs = [pd.read_csv(fp) for fp in present]
                numeric_cols = [c for c in dfs[0].columns if c not in _meta_cols]
                avg = sum(df[numeric_cols].fillna(0.0).to_numpy() for df in dfs) / len(
                    dfs
                )
                combined_eia = dfs[0][_meta_cols].copy()
                combined_eia[numeric_cols] = avg
                combined_eia.to_csv(
                    os.path.join(cost_dir, f"EIA_{file_type}_ID{scen_id}_{label}.csv"),
                    index=False,
                )

                for fp in present:
                    os.remove(fp)

        # --- Cost params: concat draws across workers, renumber draw index ---
        import glob as _glob

        for label in ["production", "deployment", "lm"]:
            # Discover which reps exist by looking at worker 0's files.
            pid0_files = _glob.glob(
                os.path.join(cost_dir, f"ID{scen_id}_rep*_cost_params_{label}_pid0.csv")
            )
            for pid0_fp in pid0_files:
                rep_m = re.search(r"_rep(\w+)_cost_params_", os.path.basename(pid0_fp))
                if not rep_m:
                    continue
                rep = rep_m.group(1)

                dfs = []
                for i in range(nprocs):
                    fp = os.path.join(
                        cost_dir,
                        f"ID{scen_id}_rep{rep}_cost_params_{label}_pid{i}.csv",
                    )
                    if not os.path.exists(fp):
                        continue
                    df = pd.read_csv(fp, index_col=0)
                    df.index = df.index + i * nbatches_per_core
                    dfs.append(df)
                    os.remove(fp)

                if dfs:
                    pd.concat(dfs).sort_index().to_csv(
                        os.path.join(
                            cost_dir,
                            f"ID{scen_id}_rep{rep}_cost_params_{label}.csv",
                        ),
                        index_label="draw",
                    )


def _prepare_mc_scenario(
    rme_template_path: str,
    assessment_year: int,
    reefset_CA: list = None,
    reefset_LM: list = None,
) -> str:
    """
    Prepare a temporary RME scenario directory for MC cost evaluation.

    Copies the template RME directory, filters ``iv_yearly_scenarios.csv`` to
    year 1 (the first deployment year) through ``assessment_year`` inclusive,
    and optionally overwrites reef assignments in ``scenario_info.json``.

    Parameters
    ----------
    rme_template_path : str
        Path to the template RME output directory.
    assessment_year : int
        The second (target) deployment year.  Rows in
        ``iv_yearly_scenarios.csv`` beyond this year are dropped.
    reefset_CA : list, optional
        Reef IDs to assign to ``reefset_CA`` (e.g. ``["18-096"]``).
        If ``None``, the template value is kept unchanged.
    reefset_LM : list, optional
        Reef IDs to assign to ``reefset_LM`` (e.g. ``["16-071"]``).
        If ``None``, the template value is kept unchanged.

    Returns
    -------
    str
        Path to the prepared temporary directory.  Caller is responsible
        for deleting it when done.
    """
    import json

    tmp_dir = tempfile.mkdtemp(prefix="ceml_")
    shutil.copytree(rme_template_path, tmp_dir, dirs_exist_ok=True)

    # Filter iv_yearly_scenarios.csv to year 1 + assessment_year
    scens_path = os.path.join(tmp_dir, "iv_yearly_scenarios.csv")
    scens_df = pd.read_csv(scens_path)
    scens_df = scens_df[scens_df["year"] <= assessment_year].reset_index(drop=True)
    scens_df.to_csv(scens_path, index=False)

    # Optionally update reef assignments in scenario_info.json
    if reefset_CA is not None or reefset_LM is not None:
        info_path = os.path.join(tmp_dir, "scenario_info.json")
        with open(info_path, "r") as f:
            iv_dict = json.load(f)
        if reefset_CA is not None:
            iv_dict["reefset_CA"] = (
                reefset_CA if isinstance(reefset_CA, list) else [reefset_CA]
            )
        if reefset_LM is not None:
            iv_dict["reefset_LM"] = (
                reefset_LM if isinstance(reefset_LM, list) else [reefset_LM]
            )
        with open(info_path, "w") as f:
            json.dump(iv_dict, f)

    return tmp_dir


_RPC_UNAVAILABLE = -2147023174  # 0x800706BA — Excel process gone


def _evaluate_with_retry(
    rme_files_path,
    nsims,
    deploy_model_fn,
    prod_model_fn,
    lm_model_fn,
    results_dir,
    retries=3,
    retry_delay=10.0,
    **kwargs,
):
    """Call ``evaluate()`` retrying on fatal Excel COM crashes (RPC unavailable).

    Each retry restarts from scratch — ``evaluate`` opens its own fresh Excel
    instance at the top of every call, so no stale COM handles survive.
    """
    import time
    import pywintypes

    for attempt in range(retries):
        try:
            return evaluate(
                rme_files_path,
                nsims,
                deploy_model_fn,
                prod_model_fn,
                lm_model_fn,
                results_dir,
                **kwargs,
            )
        except pywintypes.com_error as exc:
            if exc.hresult != _RPC_UNAVAILABLE or attempt == retries - 1:
                raise
            wait = retry_delay * (attempt + 1)
            print(
                f"Excel COM server unavailable (attempt {attempt + 1}/{retries}), "
                f"retrying in {wait:.0f}s…"
            )
            time.sleep(wait)


def run_cost_exploration(
    rme_template_path: str,
    nsims: int,
    deploy_model_fn: str,
    prod_model_fn: str,
    lm_model_fn: str,
    results_dir: str,
    assessment_year: int,
    reefset_CA: list = None,
    reefset_LM: list = None,
    metrics: list = None,
    uncertainty_dict: dict = None,
    nprocs=1,
) -> dict:
    """
    Explore cost uncertainty across intervention scenarios.

    Prepares a scenario directory from the RME template (filtering to year 1
    through ``assessment_year`` and optionally updating reef assignments), then
    calls ``evaluate()`` three times — combined (CA + LM), CA-only, and
    LM-only, writing each run's cost outputs into a labelled subdirectory
    under ``results_dir``.  Ecological metric post-processing is skipped as
    only cost outputs are of interest here.

    Parameters
    ----------
    rme_template_path : str
        Path to the RME output directory to use as a template.
    nsims : int
        Number of Monte Carlo draws for cost uncertainty sampling.
    deploy_model_fn : str
        Path to deployment spreadsheet model (excluding extension).
    prod_model_fn : str
        Path to production spreadsheet model (excluding extension).
    lm_model_fn : str
        Path to LM spreadsheet model (excluding extension).
    results_dir : str
        Root directory for outputs.  Three subdirectories are created:
        ``combined/``, ``ca_only/``, and ``lm_only/``.
    assessment_year : int
        The second (target) deployment year.  Year 1 (first deployment year)
        is always included; rows beyond ``assessment_year`` are dropped from
        the scenario before running.
    reefset_CA : list, optional
        Reef IDs to assign to ``reefset_CA`` (e.g. ``["18-096"]``).
        If ``None``, the template value from ``scenario_info.json`` is used.
    reefset_LM : list, optional
        Reef IDs to assign to ``reefset_LM`` (e.g. ``["16-071"]``).
        If ``None``, the template value from ``scenario_info.json`` is used.
    metrics : list, optional
        Passed to ``evaluate()`` for ID key generation.  Ecological metric
        files are not post-processed.  Defaults to ``[rci, raw_rti, rfi]``.
    uncertainty_dict : dict, optional
        Uncertainty parameters for ecological metrics. Defaults to
        ``default_uncertainty_dict()``.
    nprocs : int, optional
        Number of cores to use.

    Returns
    -------
    dict
        Mapping of scenario label to list of result file paths:
        ``{"combined": [...], "ca_only": [...], "lm_only": [...]}``.
    """
    # Same bootstrap guard as in evaluate() — see comment there.
    if getattr(mp.current_process(), "_inheriting", False):
        return {}

    scenarios = [
        ("combined", {"outplant", "lm"}),
        ("ca_only", {"outplant"}),
        ("lm_only", {"lm"}),
    ]

    prepared_dir = _prepare_mc_scenario(
        rme_template_path, assessment_year, reefset_CA, reefset_LM
    )
    try:
        results = {}
        for label, active_models in scenarios:
            scenario_dir = os.path.join(results_dir, label)
            result_paths = _evaluate_with_retry(
                prepared_dir,
                nsims,
                deploy_model_fn,
                prod_model_fn,
                lm_model_fn,
                scenario_dir,
                metrics=metrics,
                uncertainty_dict=uncertainty_dict,
                active_models=active_models,
                costs_only=True,
                nprocs=nprocs,
                sample_scale=True,
            )
            results[label] = result_paths
    finally:
        shutil.rmtree(prepared_dir, ignore_errors=True)

    return results


def summarise_mc_results(
    results_dir: str,
    quantiles: list = None,
    scenario_id: int = 1,
) -> dict:
    """
    Summarise Monte Carlo cost exploration results produced by ``run_cost_exploration``.

    Reads the combined cost overview CSV from each scenario subdirectory
    (``combined/``, ``ca_only/``, ``lm_only/``), computes per-year quantiles
    across draws for each cost column, and returns a nested dict suitable for
    JSON serialisation and HTML visualisation.

    Parameters
    ----------
    results_dir : str
        Root directory passed to ``run_cost_exploration`` (parent of the
        ``combined/``, ``ca_only/``, and ``lm_only/`` subdirectories).
    quantiles : list, optional
        Quantile levels to compute, e.g. ``[0.05, 0.25, 0.5, 0.75, 0.95]``.
        Defaults to ``[0.05, 0.25, 0.5, 0.75, 0.95]``.
    scenario_id : int, optional
        Scenario ID used in the cost overview filename.  Defaults to ``1``.

    Returns
    -------
    dict
        Structure: ``{scenario: {cost_col: {year: {quantile_label: value}}}}``.
        Also written to ``<results_dir>/mc_summary.json``.
    """
    if quantiles is None:
        quantiles = [0.05, 0.25, 0.5, 0.75, 0.95]

    cost_cols = [
        "production_capex",
        "production_opex",
        "deployment_capex",
        "deployment_opex",
        "lm_capex",
        "lm_opex",
        "total_capex_CA_P_D_LM",
        "total_opex_CA_P_D_LM",
    ]
    scenarios = ["combined", "ca_only", "lm_only"]

    summary = {}
    for scenario in scenarios:
        overview_path = os.path.join(
            results_dir,
            scenario,
            "Costs",
            f"ID{scenario_id}_cost_overview.csv",
        )
        if not os.path.exists(overview_path):
            continue

        df = pd.read_csv(overview_path)
        present_cols = [c for c in cost_cols if c in df.columns]

        scenario_summary = {}
        for col in present_cols:
            col_by_year = {}
            for year, grp in df.groupby("year"):
                col_by_year[int(year)] = {
                    f"q{int(q * 100)}": float(grp[col].quantile(q)) for q in quantiles
                }
            scenario_summary[col] = col_by_year

        summary[scenario] = scenario_summary

    import json

    out_path = os.path.join(results_dir, "mc_summary.json")
    with open(out_path, "w") as f:
        json.dump(summary, f, indent=2)

    return summary


def evaluate_production_cost(
    workbook_path: str,
    **factors,
) -> tuple[float, float]:
    """
    Wrapper that converts factor kwargs into the format expected by
    calculate_production_cost and returns operational and setup costs.
    Only the factors to be overridden need to be provided; all others
    default to their current values in the workbook.

    Parameters
    ----------
    workbook_path : str
        Absolute path to the Excel workbook including extension (.xlsx).
    **factors
        Factor name-value pairs keyed by factor_name from the config CSV.
        Only factors to be overridden need to be supplied.

    Returns
    -------
    op_cost : float
        Operational cost.
    setup_cost : float
        Setup cost.
    """
    _, version = _parse_model_info(workbook_path, "production")
    factor_spec = pd.read_csv(os.path.join(THIS_DIR, f"{version}_prod_config.csv"))

    unknown_factors = set(factors) - set(factor_spec["factor_names"])
    if unknown_factors:
        raise ValueError(f"Unrecognised factor(s): {unknown_factors}")

    xlapp, wb = open_excel(workbook_path)
    try:
        # Seed from config best-point values, then apply user overrides
        spreadsheet_vals = dict(
            zip(factor_spec["factor_names"], factor_spec["best_point_value"])
        )
        spreadsheet_vals.update(factors)

        factors_row = pd.Series(spreadsheet_vals)
        capex, opex = calculate_production_cost(wb, factor_spec, factors_row)
    finally:
        close_excel(xlapp, wb)

    return capex, opex


def evaluate_deployment_cost(
    workbook_path: str,
    **factors,
) -> tuple[float, float]:
    """
    Wrapper that converts factor kwargs into the format expected by
    calculate_deployment_cost and returns operational and setup costs.
    Only the factors to be overridden need to be provided; all others
    default to their current values in the workbook.

    Parameters
    ----------
    workbook_path : str
        Absolute path to the Excel workbook.
    **factors
        Factor name-value pairs keyed by factor_name from the config CSV.
        Only factors to be overridden need to be supplied.
        due to the leading digit making it an invalid Python identifier.

    Returns
    -------
    op_cost : float
        Operational cost.
    setup_cost : float
        Setup cost.
    """
    _, version = _parse_model_info(workbook_path, "deployment")
    model_spec = pd.read_csv(os.path.join(THIS_DIR, f"{version}_deploy_config.csv"))

    unknown_factors = set(factors) - set(model_spec["factor_names"])
    if unknown_factors:
        raise ValueError(f"Unrecognised factor(s): {unknown_factors}")

    xlapp, wb = open_excel(workbook_path)
    try:
        # Seed from config best-point values, then apply user overrides
        spreadsheet_vals = dict(
            zip(model_spec["factor_names"], model_spec["best_point_value"])
        )
        spreadsheet_vals.update(factors)

        factors_row = pd.Series(spreadsheet_vals)

        # Note: Number of deployed devices get adjusted to obtain the required production
        # volume/yield inside `calculate_deployment_cost()`
        # e.g., to deploy 1M devices with at least 1 coral each, and a yield of 40%,
        # 2.5M corals are needed to be produced.
        capex, opex = calculate_deployment_cost(wb, model_spec, factors_row)
    finally:
        close_excel(xlapp, wb)

    return capex, opex


def evaluate_lm_cost(
    workbook_path: str,
    scenarios_df: pd.DataFrame,
    **factors,
) -> pd.DataFrame:
    """
    Evaluate the LM cost model over a year-by-year coral scenario and apply
    the inventory/replacement model to produce a cost schedule.

    For each year in ``scenarios_df``, the required pool count is derived as
    ``clip(ceil(number of corals / yield_per_pool), range_lower, range_upper)``
    using the bounds from the LM config. Capex/opex are read back from the
    spreadsheet each year, and the inventory model is applied across the full
    time series.

    Parameters
    ----------
    workbook_path : str
        Absolute path to the LM Excel workbook including extension (.xlsx).
    scenarios_df : pd.DataFrame
        Year-by-year coral scenario table with columns ``year`` and
        ``number of corals``. Typically a single rep/intervention slice of
        ``iv_yearly_scenarios.csv``. Will be sorted by year internally.
    **factors
        Factor name-value pairs to override from config best-point values.
        Do not pass ``larval release pools`` — it is derived from
        ``scenarios_df`` each year.

    Returns
    -------
    pd.DataFrame
        ``scenarios_df`` (sorted by year) extended with columns: ``capex``,
        ``opex``, ``inventory``, ``new_capex_scale``,
        ``new_capex_replacement``, ``total_capex``, ``total_capex_opex``,
        ``ratio``, ``average``.
    """
    if "larval release pools" in factors:
        raise ValueError(
            "'larval release pools' must not be passed as a factor — it is "
            "derived from 'number of corals' in scenarios_df each year."
        )

    _, version = _parse_model_info(workbook_path, "lm")
    model_spec = pd.read_csv(os.path.join(THIS_DIR, f"{version}_LM_config.csv"))

    unknown_factors = set(factors) - set(model_spec["factor_names"])
    if unknown_factors:
        raise ValueError(f"Unrecognised factor(s): {unknown_factors}")

    scenarios_df = scenarios_df.sort_values("year").reset_index(drop=True)

    # Resolve static factor values (best-point defaults + caller overrides).
    defaults = dict(zip(model_spec["factor_names"], model_spec["best_point_value"]))
    defaults.update(factors)
    yield_per_pool = float(defaults["yield_per_pool"])

    # Pool count range from the config.
    pool_spec = model_spec.loc[
        model_spec["factor_names"] == "larval release pools"
    ].iloc[0]
    pool_min = int(pool_spec["range_lower"])
    pool_max = int(pool_spec["range_upper"])

    base, ext = os.path.splitext(workbook_path)
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=ext, prefix=os.path.basename(base) + "_")
    os.close(tmp_fd)

    capex_vec = np.zeros(len(scenarios_df))
    opex_vec = np.zeros(len(scenarios_df))

    try:
        shutil.copy(workbook_path, tmp_path)
        xlapp, wb = open_excel(tmp_path)
        try:
            for i, row in scenarios_df.iterrows():
                n_corals = float(row["number of corals"])
                pools = int(
                    np.clip(np.ceil(n_corals / yield_per_pool), pool_min, pool_max)
                )
                params = pd.Series({**defaults, "larval release pools": pools})
                capex_vec[i], opex_vec[i] = calculate_lm_cost(wb, model_spec, params)
                if i < len(scenarios_df) - 1:
                    wb = reset_workbook(xlapp, wb, tmp_path)
        finally:
            close_excel(xlapp, wb)
    finally:
        os.remove(tmp_path)

    year_table = scenarios_df.copy()
    year_table["capex"] = capex_vec
    year_table["opex"] = opex_vec

    return _apply_lm_inventory_model(year_table)


# --------------------------------------------------------------------------
# Outer function: temp copy + batch evaluation
# --------------------------------------------------------------------------


def run_cost_model(
    workbook_path: str,
    params_df: pd.DataFrame,
    *,
    model_type: str | None = None,
    nprocs: int | None = None,
) -> pd.DataFrame:
    """
    Evaluate a cost model over a set of parameter combinations.

    The model type (production or deployment) and config version are inferred
    from the workbook filename — e.g. ``'3.9.1 CA Production Model.xlsx'``.
    Pass ``model_type`` explicitly only when the filename does not follow the
    standard naming convention.

    When ``nprocs`` is greater than 1, ``params_df`` is split into chunks and
    evaluated in parallel — each worker opens its own temporary workbook copy.
    Otherwise the whole DataFrame is evaluated serially in a single workbook
    session.

    Columns not present in ``params_df`` default to the values currently in the
    workbook.

    Parameters
    ----------
    workbook_path : str
        Absolute path to the Excel workbook including extension (.xlsx).
    params_df : pd.DataFrame
        Each row is one model evaluation. Column names must be factor names from
        the relevant config CSV. Only factors to be overridden from workbook
        defaults need to be included.
    model_type : str, optional
        ``"production"`` or ``"deployment"``. Inferred from the filename if
        not provided.
    nprocs : int, optional
        Number of parallel worker processes. Values <= 1 run serially.
        Defaults to serial (``None``).

    Returns
    -------
    pd.DataFrame
        Input DataFrame with three appended columns: ``capex``, ``opex``,
        and ``total_cost``.
    """
    model_type, version = _parse_model_info(workbook_path, model_type)

    if nprocs is not None and nprocs > 1:
        idx_chunks = np.array_split(np.arange(len(params_df)), nprocs)
        chunks = [params_df.iloc[idx] for idx in idx_chunks if len(idx) > 0]
        worker = partial(run_cost_model, workbook_path, model_type=model_type)
        _fix_main_spec()
        with mp.Pool(processes=nprocs, initializer=_pool_initializer) as pool:
            results = pool.map(worker, chunks)
        return pd.concat(results, ignore_index=True)

    config_suffix = _KNOWN_MODEL_TYPES[model_type]
    _evaluate_fn_map = {
        "production": calculate_production_cost,
        "deployment": calculate_deployment_cost,
        "lm": calculate_lm_cost,
    }
    evaluate_fn = _evaluate_fn_map[model_type]
    model_spec = pd.read_csv(
        os.path.join(THIS_DIR, f"{version}_{config_suffix}_config.csv")
    )

    unknown = set(params_df.columns) - set(model_spec["factor_names"])
    if unknown:
        raise ValueError(f"Unrecognised factor(s): {unknown}")

    # Apply categorical floor trick and discrete value mapping to match the
    # sampling convention used by problem_spec / run_production_model etc.
    input_spec = model_spec[model_spec["factor_names"].isin(params_df.columns)]
    params_df = convert_factor_types(params_df.copy(), input_spec.is_cat.values)
    params_df = apply_discrete_mapping(params_df, input_spec)

    # Convert coral count to number of devices required to achieve the target,
    # accounting for production yield. Both models expect device counts as input.
    if "num_1yoec" in params_df.columns and "coral_yield_1YOEC" in params_df.columns:
        params_df["num_1yoec"] = np.ceil(
            params_df["num_1yoec"] / params_df["coral_yield_1YOEC"]
        )

    base, ext = os.path.splitext(workbook_path)
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=ext, prefix=os.path.basename(base) + "_")
    os.close(tmp_fd)

    try:
        shutil.copy(workbook_path, tmp_path)
        xlapp, wb = open_excel(tmp_path)
        try:
            # Seed from config best-point values — guarantees a known-good baseline
            # regardless of whatever state the spreadsheet was saved in
            defaults = dict(
                zip(model_spec["factor_names"], model_spec["best_point_value"])
            )

            results = np.zeros((len(params_df), 2))
            for i, (_, row) in enumerate(params_df.iterrows()):
                params = pd.Series({**defaults, **row.to_dict()})
                results[i] = evaluate_fn(wb, model_spec, params)
                if i < len(params_df) - 1:
                    wb = reset_workbook(xlapp, wb, tmp_path)
        finally:
            close_excel(xlapp, wb)
    finally:
        try:
            os.remove(tmp_path)
        except PermissionError:
            pass  # Excel may still hold the file lock after a COM error; temp file will be cleaned up by the OS

    return params_df.assign(
        capex=results[:, 0],
        opex=results[:, 1],
        total_cost=results[:, 0] + results[:, 1],
    )


def sample_joint_factors(
    nsims: int, seed=None
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, ProblemSpec]:
    """
    Generate Sobol' samples over the combined factor space of all three models
    (production, deployment, and LM), then slice back into per-model DataFrames.

    Building one combined ProblemSpec ensures every row corresponds to the same
    sample point and that any shared factors are identical across models by
    construction.

    Categorical flooring and discrete value mapping are intentionally *not*
    applied here — ``run_cost_model`` handles those transformations to avoid
    double-application.

    Parameters
    ----------
    nsims : int
        Desired total number of model evaluations. The base Sobol' sample count
        N is derived via ``get_NK(nsims, n_combined_factors)``.
    seed : int or None
        Random seed for reproducibility.

    Returns
    -------
    prod_samples : pd.DataFrame
        Raw Sobol' samples for the production model.
    deploy_samples : pd.DataFrame
        Raw Sobol' samples for the deployment model.
    lm_samples : pd.DataFrame
        Raw Sobol' samples for the LM model.
    combined_sp : ProblemSpec
        The combined ProblemSpec used for sampling.
    """
    sp_prod, _ = problem_spec("production")
    sp_dep, _ = problem_spec("deployment")
    sp_lm, _ = problem_spec("lm")

    prod_names = sp_prod["names"]
    dep_names = sp_dep["names"]
    lm_names = sp_lm["names"]

    # Union of all factor names; production definitions take precedence for any shared factors.
    seen = set()
    combined_names = []
    for n in list(prod_names) + list(dep_names) + list(lm_names):
        if n not in seen:
            combined_names.append(n)
            seen.add(n)

    bounds_map = {
        **dict(zip(lm_names, sp_lm["bounds"])),
        **dict(zip(dep_names, sp_dep["bounds"])),
        **dict(zip(prod_names, sp_prod["bounds"])),
    }
    dists_map = {
        **dict(zip(lm_names, sp_lm["dists"])),
        **dict(zip(dep_names, sp_dep["dists"])),
        **dict(zip(prod_names, sp_prod["dists"])),
    }

    combined_sp = ProblemSpec(
        {
            "num_vars": len(combined_names),
            "names": combined_names,
            "bounds": [bounds_map[n] for n in combined_names],
            "dists": [dists_map[n] for n in combined_names],
        }
    )
    N, _ = get_NK(nsims, len(combined_names))
    combined_sp.sample_sobol(N, calc_second_order=False, seed=seed)
    combined_samples = pd.DataFrame(data=combined_sp.samples, columns=combined_names)

    prod_df = combined_samples[list(prod_names)].copy()
    dep_df = combined_samples[list(dep_names)].copy()
    lm_df = combined_samples[list(lm_names)].copy()

    return prod_df, dep_df, lm_df, combined_sp


def run_joint_cost_models(
    prod_workbook: str,
    deploy_workbook: str,
    lm_workbook: str,
    prod_samples: pd.DataFrame,
    deploy_samples: pd.DataFrame,
    lm_samples: pd.DataFrame,
    *,
    nprocs: int = 1,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Run production, deployment, and LM cost models against jointly-sampled parameters.

    Parameters
    ----------
    prod_workbook : str
        Path to the production model workbook.
    deploy_workbook : str
        Path to the deployment model workbook.
    lm_workbook : str
        Path to the LM model workbook.
    prod_samples, deploy_samples, lm_samples : pd.DataFrame
        Raw sample DataFrames as returned by ``sample_joint_factors``.
    nprocs : int, optional
        Number of parallel workers. Default 1.

    Returns
    -------
    prod_results, deploy_results, lm_results : pd.DataFrame
        Sample DataFrames with ``capex``, ``opex``, and ``total_cost`` columns added.
    """
    prod_results = run_cost_model(prod_workbook, prod_samples, nprocs=nprocs)
    deploy_results = run_cost_model(deploy_workbook, deploy_samples, nprocs=nprocs)
    lm_results = run_cost_model(lm_workbook, lm_samples, nprocs=nprocs)
    return prod_results, deploy_results, lm_results


def _factor_names(workbook_path: str, model_type: str | None = None) -> set:
    """Return the set of valid factor names for the given workbook."""
    model_type, version = _parse_model_info(workbook_path, model_type)
    config_suffix = _KNOWN_MODEL_TYPES[model_type]
    spec = pd.read_csv(os.path.join(THIS_DIR, f"{version}_{config_suffix}_config.csv"))
    return set(spec["factor_names"])


def _build_sweep(sweep_param, search_range, fixed_params, valid_factors):
    """
    Build a sweep DataFrame, including sweep_param only if it is a valid factor
    for the target model. Unknown keys in fixed_params are also silently dropped.
    """
    row_base = {k: v for k, v in fixed_params.items() if k in valid_factors}
    if sweep_param in valid_factors:
        return pd.DataFrame([{sweep_param: val, **row_base} for val in search_range])
    return pd.DataFrame([row_base] * len(search_range))


def sweep_ca(
    prod_model: str,
    deploy_model: str,
    sweep_param: str,
    search_range,
    prod_params: dict = None,
    dep_params: dict = None,
) -> pd.DataFrame:
    """
    Sweep a single parameter across the CA production and deployment models.

    Parameters
    ----------
    prod_model : str
        Path to production cost model workbook.
    deploy_model : str
        Path to deployment cost model workbook.
    sweep_param : str
        Name of the parameter to sweep over.
    search_range : iterable
        Values to sweep over.
    prod_params : dict, optional
        Fixed factor overrides for the production model.
    dep_params : dict, optional
        Fixed factor overrides for the deployment model. Any key also present
        in ``prod_params`` is overwritten by the production value, keeping
        shared factors (e.g. ``num_1yoec``, ``coral_yield_1YOEC``) consistent.

    Returns
    -------
    pd.DataFrame
        One row per sweep value with columns: ``search_range``,
        ``prod_capex``, ``prod_opex``, ``dep_capex``, ``dep_opex``,
        ``total_cost``.
    """
    prod_params = prod_params or {}
    dep_params = dep_params or {}
    search_range = list(search_range)

    # num_1yoec is expressed as a coral count by the caller (consistent with evaluate()).
    # The spreadsheet cell accepts device count, so convert: devices = ceil(corals / yield).
    # Yield comes from the caller's params if provided, otherwise from the config best-point.
    device_range = None
    if sweep_param == "num_1yoec":
        _, prod_ver = _parse_model_info(prod_model, "production")
        prod_cfg = pd.read_csv(os.path.join(THIS_DIR, f"{prod_ver}_prod_config.csv"))
        default_yield = float(
            prod_cfg.loc[
                prod_cfg["factor_names"] == "coral_yield_1YOEC", "best_point_value"
            ].iloc[0]
        )
        yield_val = float(
            prod_params.get(
                "coral_yield_1YOEC", dep_params.get("coral_yield_1YOEC", default_yield)
            )
        )
        device_range = list(np.ceil(np.array(search_range) / yield_val).astype(int))
        search_range = device_range

    prod_factors = _factor_names(prod_model)
    dep_factors = _factor_names(deploy_model)

    prod_sweep = _build_sweep(sweep_param, search_range, prod_params, prod_factors)

    # Production values overwrite deployment values for any shared factors.
    effective_dep_params = {**dep_params, **prod_params}
    dep_sweep = _build_sweep(
        sweep_param, search_range, effective_dep_params, dep_factors
    )

    prod_results = run_cost_model(prod_model, prod_sweep)
    dep_results = run_cost_model(deploy_model, dep_sweep)

    return pd.DataFrame(
        {
            "search_range": search_range,
            "prod_capex": prod_results["capex"].values,
            "prod_opex": prod_results["opex"].values,
            "dep_capex": dep_results["capex"].values,
            "dep_opex": dep_results["opex"].values,
            "total_cost": prod_results["total_cost"].values
            + dep_results["total_cost"].values,
        }
    )


def sweep_lm(
    lm_model: str,
    sweep_param: str,
    search_range,
    lm_params: dict = None,
) -> pd.DataFrame:
    """
    Sweep a single parameter across the LM cost model.

    Parameters
    ----------
    lm_model : str
        Path to LM cost model workbook.
    sweep_param : str
        Name of the parameter to sweep over.
    search_range : iterable
        Values to sweep over.
    lm_params : dict, optional
        Fixed factor overrides for the LM model.

    Returns
    -------
    pd.DataFrame
        One row per sweep value with columns: ``search_range``,
        ``capex``, ``opex``, ``total_cost``.
    """
    lm_params = lm_params or {}
    search_range = list(search_range)

    lm_factors = _factor_names(lm_model)
    lm_sweep = _build_sweep(sweep_param, search_range, lm_params, lm_factors)
    lm_results = run_cost_model(lm_model, lm_sweep)

    return pd.DataFrame(
        {
            "search_range": search_range,
            "capex": lm_results["capex"].values,
            "opex": lm_results["opex"].values,
            "total_cost": lm_results["total_cost"].values,
        }
    )

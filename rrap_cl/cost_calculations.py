import atexit
import dataclasses
import os
from os.path import join as path_join

import shutil
import tempfile

import numpy as np
import pandas as pd

from .setup_results import OutputStores
from .sampling import (
    problem_spec,
    get_NK,
    convert_factor_types,
    apply_discrete_mapping,
    collect_deployment_costs,
    collect_production_costs,
    collect_lm_costs,
    _apply_lm_inventory_model,
)

from .handlers import (
    reset_workbook,
    WorkbookSession,
    create_eia_template,
    fill_EIA_info,
    create_lm_eia_template,
    fill_lm_EIA_info,
)

THIS_DIR = os.path.dirname(__file__)

# ---------------------------------------------------------------------------
# Per-process persistent Excel session
# ---------------------------------------------------------------------------

_RESET_INTERVAL = 50  # reset the WorkbookSession every N calculate_costs calls

# Keyed by (deploy_src, prod_src, lm_src) — original absolute paths including .xlsx.
# Each worker process starts with an empty dict; entries are created on first use.
_process_sessions: dict = {}


class _ProcessSession:
    """Per-process persistent Excel session for a fixed set of three model files.

    On creation, the three xlsx files are copied to a private temp directory so
    that concurrent worker processes never open the same physical file (which
    would cause Windows file-lock "File now available" dialogs when a sibling
    worker releases its lock).

    A single WorkbookSession is held open across calls, eliminating the
    open/close Excel overhead on every evaluate() call.  Every
    ``reset_interval`` calls the WorkbookSession is recycled to keep Excel's
    memory footprint bounded.
    """

    def __init__(self, deploy_src, prod_src, lm_src, reset_interval):
        self._reset_interval = reset_interval
        self._call_count = 0

        self._tmp_dir = tempfile.mkdtemp(prefix="ceml_worker_")
        try:
            self.deploy_fp = self._copy(deploy_src)
            self.prod_fp = self._copy(prod_src)
            self.lm_fp = self._copy(lm_src)
            self.wb_session = WorkbookSession()
        except Exception:
            shutil.rmtree(self._tmp_dir, ignore_errors=True)
            raise
        atexit.register(self._cleanup)

    def _copy(self, src):
        dst = os.path.join(self._tmp_dir, os.path.basename(src))
        shutil.copy(src, dst)
        return dst

    def tick(self):
        """Increment call counter; recycle the WorkbookSession if the interval is reached."""
        self._call_count += 1
        if self._call_count >= self._reset_interval:
            # Don't uninitialize COM here — the apartment must stay alive for the
            # new WorkbookSession() that immediately follows.
            self.wb_session.cleanup(uninitialize_com=False)
            self.wb_session = WorkbookSession()
            self._call_count = 0

    def _cleanup(self, uninitialize_com=True):
        # Guard against double-cleanup: atexit may fire after an eager cleanup
        # that already ran (e.g. at the end of a worker task).
        if getattr(self, "_cleaned", False):
            return
        self._cleaned = True
        if hasattr(self, "wb_session"):
            # uninitialize_com=False when called mid-task (worker finally block):
            # CoUninitialize() must NOT be called before the worker process exits,
            # because win32com.client's module-level COM caches (CLSIDToClass,
            # _win32com_module_cache) still hold COM wrapper objects that need a
            # live apartment to release cleanly during Python's module shutdown.
            # Calling CoUninitialize() mid-task causes the worker to hang/crash
            # during module teardown, leaving 300–500 MB of worker memory alive
            # indefinitely.  The atexit path (uninitialize_com=True) is safe
            # because Python is already past module cleanup at that point.
            self.wb_session.cleanup(uninitialize_com=uninitialize_com)
        if hasattr(self, "_tmp_dir") and os.path.exists(self._tmp_dir):
            shutil.rmtree(self._tmp_dir, ignore_errors=True)


def _get_process_session(deploy_src, prod_src, lm_src, reset_interval):
    """Return (creating if necessary) the process-local session for these model files.

    If a session already exists for a *different* set of model paths (e.g. the
    worker is processing a new task whose per-task temp copies live under a
    different directory), the old session is closed eagerly before creating the
    new one.  Without this, each task in the explorer's per-task-temp-dir flow
    would accumulate a separate open Excel instance for the entire worker
    lifetime, resulting in up to maxtasksperchild simultaneous Excel processes
    per worker.
    """
    key = (deploy_src, prod_src, lm_src)
    if key not in _process_sessions:
        # Eagerly close any stale sessions for old model paths so we never hold
        # more than one open Excel instance per worker at a time.
        for old_session in list(_process_sessions.values()):
            old_session._cleanup()
        _process_sessions.clear()

        _process_sessions[key] = _ProcessSession(
            deploy_src, prod_src, lm_src, reset_interval
        )
    return _process_sessions[key]


# ---------------------------------------------------------------------------
# Cost component helpers
# ---------------------------------------------------------------------------


def cost_types(cost, contingency, nsims):
    """
    Calculate key cost codes:
    - 1 : CAPEX,  sum of production and deployment cost
    - 2 : Contingency CAPEX,  % of CAPEX
    - 3 : OPEX,  sum of production and deployment cost
    - 4 : Sustaining capital OPEX, set to zero for now (assumed to be included in OPEX through contract)
    - 5 : Contingency OPEX, % of OPEX
    - 6 : Vessel fuel, only relevant if volunteer vessels are used - set to zero for now
    - 7 : CAPEX-monitoring, set to zero (assumed no monitoring cost)
    - 8 : Contingency CAPEX-monitoring, % of CAPEX-monitoring
    - 9 : OPEX-monitoring, set to zero (assumed no monitoring cost)
    - 10 : Sustaining capital OPEX-monitoring, set to zero (assumed no monitoring cost)
    - 11 : Contingency OPEX-monitoring, % of OPEX-monitoring

    Parameters
    ----------
    cost : dataframe
        Dataframe containing 'capex' and 'opex'
    contingency : float
        Contingency proportion.
    nsims : int
        Total number of simulations (from metrics sampling)
    """
    return np.vstack(
        (
            np.vstack(
                (
                    cost[:, 0],
                    cost[:, 0] * contingency,
                    cost[:, 1],
                    np.zeros(nsims),
                    cost[:, 1] * contingency,
                )
            ),
            np.zeros((6, nsims)),
        )
    )


def initialize_cost_df(years, nsims):
    """
    Initialize dataframe for storing sampled cost data.

    Components:
    - 1 : CAPEX, sum of production and deployment cost
    - 2 : Contingency CAPEX, % of CAPEX
    - 3 : OPEX, sum of production and deployment cost
    - 4 : Sustaining capital OPEX, set to zero for now (assumed to be included in OPEX through contract)
    - 5 : Contingency OPEX, % of OPEX
    - 6 : Vessel fuel, only relevant if volunteer vessels are used - set to zero for now
    - 7 : CAPEX-monitoring, set to zero (assumed no monitoring cost)
    - 8 : Contingency CAPEX-monitoring, % of CAPEX-monitoring
    - 9 : OPEX-monitoring, set to zero (assumed no monitoring cost)
    - 10 : Sustaining capital OPEX-monitoring, set to zero (assumed no monitoring cost)
    - 11 : Contingency OPEX-monitoring, % of OPEX-monitoring

    Parameters
    ----------
    years : np.array
        All simulation years (non-intervention years will have zero costs)
    nsims : int
        Total number of simulations (from metrics sampling)

    Returns
    -------
    cost_df : dataframe
    """
    # Dataframe for saving cost data to
    n_years = len(years)
    cols = ["year", "component"] + ["draw" + str(n) for n in range(1, nsims + 1)]
    cost_df = pd.DataFrame(np.zeros((n_years * 11, 2 + nsims)), columns=cols)
    cost_df.loc[:, "year"] = np.array(np.repeat(years, 11)).astype(int)
    cost_df.loc[:, "component"] = np.tile(np.array(range(1, 12)), n_years).astype(int)

    return cost_df


def _fill_eia_missing_years(eia_template, all_years):
    """Add zero-cost rows for simulation years absent from the EIA template.

    For each unique (iteration, intervention, location, type) combination already
    in the template, a zero row is inserted for every year in ``all_years`` that
    has no entry, so the output covers the full simulation time series.
    """
    if eia_template.empty:
        return eia_template

    key_cols = ["iteration", "intervention", "location", "type"]
    cost_cols = [c for c in eia_template.columns if c not in key_cols + ["year"]]
    groups = eia_template[key_cols].drop_duplicates()

    new_rows = []
    for _, grp in groups.iterrows():
        mask = (eia_template[key_cols] == grp.values).all(axis=1)
        present = set(eia_template.loc[mask, "year"])
        for yr in all_years:
            if yr not in present:
                row = grp.to_dict()
                row["year"] = int(yr)
                row.update({c: 0.0 for c in cost_cols})
                new_rows.append(row)

    if new_rows:
        eia_template = pd.concat(
            [eia_template, pd.DataFrame(new_rows, columns=eia_template.columns)],
            ignore_index=True,
        )

    return eia_template


def sample_cost_model(nsims, seed: int = None):
    """
    Sample cost model parameters for production, deployment, and LM models.

    Parameters
    ----------
    nsims : int or None
        Total number of simulations (from metrics sampling).  When ``None``
        or ``1`` Sobol' sampling is skipped and a single row of
        ``best_point_value`` entries is returned instead.
    seed : int, optional
        Random seed for reproducibility.

    Returns
    -------
    factor_specs_prod : DataFrame
    factors_df_prod : DataFrame
    factor_specs_dep : DataFrame
    factors_df_dep : DataFrame
    factor_specs_lm : DataFrame
    factors_df_lm : DataFrame
    """
    sp_dep, factor_specs_dep = problem_spec("deployment")
    sp_prod, factor_specs_prod = problem_spec("production")
    sp_lm, factor_specs_lm = problem_spec("lm")

    specs_prod = factor_specs_prod.loc[
        ~factor_specs_prod.factor_names.isin(["capex", "opex"])
    ]
    specs_dep = factor_specs_dep.loc[
        ~factor_specs_dep.factor_names.isin(["capex", "opex"])
    ]
    specs_lm = factor_specs_lm.loc[
        ~factor_specs_lm.factor_names.isin(["capex", "opex"])
    ]

    if nsims is None or nsims == 1:
        # Best-point / deterministic run — use best_point_value directly.
        # Apply convert_factor_types so categorical (integer) parameters are
        # stored as ints rather than floats, matching the Sobol path.
        factors_df_prod = convert_factor_types(
            pd.DataFrame(
                [dict(zip(specs_prod.factor_names, specs_prod.best_point_value))]
            ),
            specs_prod.is_cat,
        )
        factors_df_dep = convert_factor_types(
            pd.DataFrame(
                [dict(zip(specs_dep.factor_names, specs_dep.best_point_value))]
            ),
            specs_dep.is_cat,
        )
        factors_df_lm = convert_factor_types(
            pd.DataFrame([dict(zip(specs_lm.factor_names, specs_lm.best_point_value))]),
            specs_lm.is_cat,
        )
    else:
        # Sample production model factors using Latin Hypercube Sampling
        # This provides exactly `nsims` unique, space-filling samples without the 
        # identical-row artifacts caused by truncating Saltelli/Sobol matrices.
        sp_prod.sample_latin(nsims, seed=seed)
        factors_df_prod = pd.DataFrame(
            data=sp_prod.samples, columns=specs_prod.factor_names
        )

        # Sample deployment model factors
        sp_dep.sample_latin(nsims, seed=seed)
        factors_df_dep = pd.DataFrame(
            data=sp_dep.samples, columns=specs_dep.factor_names
        )

        # Sample LM model factors
        sp_lm.sample_latin(nsims, seed=seed)
        factors_df_lm = pd.DataFrame(
            data=sp_lm.samples, columns=specs_lm.factor_names
        )

        # Convert factor types to suitable format for cost model sampling
        factors_df_dep = convert_factor_types(factors_df_dep, specs_dep.is_cat)
        factors_df_prod = convert_factor_types(factors_df_prod, specs_prod.is_cat)
        factors_df_lm = convert_factor_types(factors_df_lm, specs_lm.is_cat)
        factors_df_dep = apply_discrete_mapping(factors_df_dep, factor_specs_dep)
        factors_df_prod = apply_discrete_mapping(factors_df_prod, factor_specs_prod)
        factors_df_lm = apply_discrete_mapping(factors_df_lm, factor_specs_lm)

    return (
        factor_specs_prod,
        factors_df_prod,
        factor_specs_dep,
        factors_df_dep,
        factor_specs_lm,
        factors_df_lm,
    )


def lm_opex_distance_multiplier(distance_nm: float) -> float:
    """
    Return the OPEX multiplier for LM interventions based on deployment distance.

    Derived from the regression 0.2495 * x^0.517, where x is distance in
    nautical miles. The relationship is calibrated so that 15 NM → ×1.0 (100%),
    30 NM → ×1.5, 60 NM → ×2.0, 120 NM → ×3.0.

    Parameters
    ----------
    distance_nm : float
        Distance from port in nautical miles.

    Returns
    -------
    float
        Multiplier to apply to the raw LM OPEX value.
    """
    return 0.2495 * (distance_nm**0.517)


def update_lm_factors(lm_factors, iv_spec, pool_min, pool_max, sample_scale=False):
    """
    Update sampled LM factor dataframe with the year-specific larval release
    pool count derived from coral counts in the intervention specification.

    Parameters
    ----------
    lm_factors : DataFrame
        Factors dataframe for the LM cost model.
    iv_spec : DataFrame
        Intervention specification for the current rep and year (one row per
        reefset); must contain ``number_of_1YO_corals``.
    pool_min : int
        Minimum valid pool count (``range_lower`` from LM config).
    pool_max : int
        Maximum valid pool count (``range_upper`` from LM config).
    sample_scale : bool, optional
        When ``True`` (cost exploration mode), the larval pool count is already
        sampled from the Sobol sequence and must not be overwritten.  The RME
        template coral counts are ignored.  Defaults to ``False``.

    Returns
    -------
    lm_factors : DataFrame
    """
    if sample_scale:
        # Pool count already sampled — leave it untouched.
        return lm_factors

    n_corals = iv_spec["number_of_1YO_corals"].sum()
    pools = np.clip(
        np.ceil(n_corals / lm_factors["yield_per_pool"].values),
        pool_min,
        pool_max,
    ).astype(int)
    lm_factors = lm_factors.copy()
    lm_factors["larval release pools"] = pools
    return lm_factors


def update_factors(prod_factors, deploy_factors, iv_spec, sample_scale=False):
    """
    Update sampled cost model parameter dataframes with intervention specific parameters
    for a single ecological repeat.

    Parameters
    ----------
    prod_factors : dataframe
        Factors dataframe for the production cost model.
    deploy_factors : dataframe
        Factors dataframe for the deployment cost model.
    iv_spec : dataframe
        Intervention specification dataframe for the current rep and year,
        containing one row per reefset.
    sample_scale : bool, optional
        When ``True`` (cost exploration mode), ``num_1yoec`` is already sampled
        from the Sobol sequence and must not be overwritten with the fixed coral
        count from the RME template.  ``coral_yield_1YOEC`` is still synced
        between models and ``distance_from_port`` is still set from ``iv_spec``.
        Defaults to ``False``.
    """
    if not sample_scale:
        # Sum coral count over reefsets for this rep and year.
        # Convert to device count — the spreadsheet expects devices at the num_1yoec cell.
        n_1yo_corals = iv_spec["number_of_1YO_corals"].sum()
        yield_1yo = deploy_factors["coral_yield_1YOEC"]
        n_devices = np.ceil(n_1yo_corals / yield_1yo)

        deploy_factors.loc[:, "num_1yoec"] = n_devices
        prod_factors.loc[:, "num_1yoec"] = n_devices
    else:
        # Device count already sampled — sync prod to deploy so both models
        # receive the same scale.
        prod_factors.loc[:, "num_1yoec"] = deploy_factors["num_1yoec"].values

    # Always sync coral_yield_1YOEC between models.
    prod_factors.loc[:, "coral_yield_1YOEC"] = deploy_factors[
        "coral_yield_1YOEC"
    ].values

    # In exploration mode distance_from_port is already sampled; in normal mode
    # use the actual reef distance from the intervention spec.
    if not sample_scale:
        deploy_factors.loc[:, "distance_from_port"] = iv_spec[
            "distance_to_port_NM"
        ].iloc[0]

    # Propagate representative reef name so the deployment model can set D6,
    # which drives the port lookup (land haulage cost per truck-trip).
    if "reef" in iv_spec.columns:
        deploy_factors.loc[:, "reef"] = iv_spec["reef"].iloc[0]

    return prod_factors, deploy_factors


def calc_production_requirement(deploy_factors, prev_iv_spec):
    """
    Calculate the number of devices required in the previous intervention year
    for a single ecological repeat. Used to determine whether additional
    production capacity is needed in the current year.

    Parameters
    ----------
    deploy_factors : dataframe
        Factors dataframe for the deployment cost model (used for yield values).
    prev_iv_spec : dataframe
        Intervention specification dataframe for the previous intervention year
        and rep, containing one row per reefset.

    Returns
    -------
    prev_devices : np.ndarray
        Number of devices required in the previous intervention year, per simulation.
    """
    n_1yo_corals = prev_iv_spec["number_of_1YO_corals"].sum()
    return np.ceil(n_1yo_corals / deploy_factors["coral_yield_1YOEC"])


# ---------------------------------------------------------------------------
# Overview accumulator
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class _OverviewAccumulator:
    """Per-rep arrays that accumulate costs and metadata for the overview CSV."""

    n_years: int
    nsims: int

    def __post_init__(self):
        n, s = self.n_years, self.nsims
        self.prod_raw_capex = np.zeros((n, s))
        self.prod_opex = np.zeros((n, s))
        self.deploy_raw_capex = np.zeros((n, s))
        self.deploy_opex = np.zeros((n, s))
        self.prod_total_capex = np.zeros((n, s))
        self.deploy_total_capex = np.zeros((n, s))
        self.num_devices = np.zeros((n, s))
        self.num_1yoec = np.zeros((n, s))
        self.lm_num_larval_pool = np.zeros((n, s))
        self.lm_yield_per_pool = np.zeros((n, s))
        self.lm_num_1yoec = np.zeros(
            (n, s)
        )  # pools * yield, summed correctly across reefsets
        self.lm_raw_capex = np.zeros((n, s))
        self.lm_raw_opex = np.zeros((n, s))
        self.lm_opex_pre_mult = np.zeros((n, s))  # raw opex before distance multiplier
        self.lm_opex_mult = np.zeros((n, s))  # distance multiplier (per draw)


# ---------------------------------------------------------------------------
# Private helpers extracted from calculate_costs()
# ---------------------------------------------------------------------------


def _copy_workbooks(tmp_dir, deploy_filepath, prod_filepath, lm_filepath, iter_id):
    """Copy the three Excel workbooks to a temporary directory for an isolated run.

    Returns
    -------
    deploy_fp, prod_fp, lm_fp : str
        Paths to the copied workbooks.
    """
    deploy_fp = path_join(tmp_dir, f"{deploy_filepath}{iter_id}.xlsx")
    os.makedirs(os.path.dirname(deploy_fp), exist_ok=True)
    shutil.copy(deploy_filepath + ".xlsx", deploy_fp)

    prod_fp = path_join(tmp_dir, f"{prod_filepath}{iter_id}.xlsx")
    os.makedirs(os.path.dirname(prod_fp), exist_ok=True)
    shutil.copy(prod_filepath + ".xlsx", prod_fp)

    lm_fp = path_join(tmp_dir, f"{lm_filepath}{iter_id}.xlsx")
    os.makedirs(os.path.dirname(lm_fp), exist_ok=True)
    shutil.copy(lm_filepath + ".xlsx", lm_fp)

    return deploy_fp, prod_fp, lm_fp


def _save_cost_params(
    cost_dir,
    scen_id,
    rep,
    p_iter_id,
    prod_factors,
    deploy_factors,
    lm_factors,
    active_models=None,
):
    """Save sampled cost parameters to CSV for manual cross-checking.

    Only models that were actually run (i.e. present in ``active_models``) are
    saved — writing params for a model whose spreadsheet was never executed
    would produce a file with no cost output columns, which is misleading.

    Called after the year loop so the factors reflect the second (assessment)
    intervention year.  For production and deployment a ``num_devices`` column
    derived from ``num_1yoec / coral_yield_1YOEC`` is inserted alongside
    ``num_1yoec`` for convenient cross-checking against the cost_overview.
    """
    if active_models is None:
        active_models = {"outplant", "lm"}

    _model_gate = {
        "production": "outplant",
        "deployment": "outplant",
        "lm": "lm",
    }

    for _label, _df in [
        ("production", prod_factors),
        ("deployment", deploy_factors),
        ("lm", lm_factors),
    ]:
        if _model_gate[_label] not in active_models:
            continue
        _params_fp = path_join(
            cost_dir,
            f"ID{scen_id}_rep{rep}_cost_params_{_label}_pid{p_iter_id}.csv",
        )
        _save_df = _df.copy()
        if "num_1yoec" in _save_df.columns and "coral_yield_1YOEC" in _save_df.columns:
            # update_factors() stores device count in the num_1yoec column
            # (n_devices = ceil(n_corals / yield) then assigned to num_1yoec).
            # Recover the actual coral count by multiplying back, then rename
            # the column so the output shows num_devices first, num_1yoec second.
            n_devices = _save_df["num_1yoec"].values.copy()
            n_corals = np.round(
                n_devices * _save_df["coral_yield_1YOEC"].values
            ).astype(int)
            _save_df = _save_df.rename(columns={"num_1yoec": "num_devices"})
            _save_df.insert(
                _save_df.columns.get_loc("num_devices") + 1,
                "num_1yoec",
                n_corals,
            )
        _save_df.index = np.arange(1, len(_save_df) + 1)
        _save_df.to_csv(_params_fp, index_label="draw")


def _process_outplant_reefset(
    xlapp,
    deploy_wb,
    prod_wb,
    deploy_model_fp,
    prod_model_fp,
    deploy_factors,
    deploy_spec,
    prod_factors,
    prod_spec,
    rs_spec,
    rs_num_1yoec,
    departure_port,
    rs_years,
    iv_yr,
    rep,
    nsims,
    yr_prod_capex,
    yr_deploy_capex,
    yr_opex,
    acc,
    yr_idx,
    eia_template_prod,
    eia_template_deploy,
    sample_scale=False,
    workbook_session=None,
):
    """Run spreadsheet models for one outplant reefset and accumulate results.

    ``yr_prod_capex``, ``yr_deploy_capex``, ``yr_opex``, and ``acc`` are updated in-place.

    Parameters
    ----------
    rs_spec : DataFrame
        Rows from ID_key for this reefset/year/rep (columns: number_of_1YO_corals,
        distance_to_port_NM, number_of_groups, rep).
    rs_num_1yoec : float
        Sum of number_of_1YO_corals for this reefset (pre-computed by caller).
    yr_prod_capex, yr_deploy_capex, yr_opex : np.ndarray shape (nsims,)
        Running totals across reefsets; modified in-place.
    acc : _OverviewAccumulator
        Overview tracking arrays; modified in-place.
    workbook_session : WorkbookSession, optional
        A session object for caching and seeding optimization.

    Returns
    -------
    deploy_wb, prod_wb, deploy_factors, prod_factors, eia_template_prod, eia_template_deploy
    """
    prod_factors, deploy_factors = update_factors(
        prod_factors, deploy_factors, rs_spec, sample_scale=sample_scale
    )
    deploy_wb, deploy_factors = collect_deployment_costs(
        xlapp,
        deploy_wb,
        deploy_model_fp,
        deploy_factors,
        deploy_spec,
        workbook_session=workbook_session,
    )
    prod_wb, prod_factors = collect_production_costs(
        xlapp,
        prod_wb,
        prod_model_fp,
        prod_factors,
        prod_spec,
        workbook_session=workbook_session,
    )

    # Capture raw values before the CAPEX comparison block
    # may modify them (raw = full spreadsheet run for this year).
    raw_prod_capex = prod_factors["capex"].values[0:nsims].copy()
    raw_deploy_capex = deploy_factors["capex"].values[0:nsims].copy()

    yr_prod_capex += raw_prod_capex
    yr_deploy_capex += raw_deploy_capex
    yr_opex += (deploy_factors["opex"] + prod_factors["opex"]).values[0:nsims]

    acc.prod_raw_capex[yr_idx] += raw_prod_capex
    acc.prod_opex[yr_idx] += prod_factors["opex"].values[0:nsims]
    acc.deploy_raw_capex[yr_idx] += raw_deploy_capex
    acc.deploy_opex[yr_idx] += deploy_factors["opex"].values[0:nsims]
    n_devices_this_rs = deploy_factors["num_1yoec"].values[0:nsims]
    acc.num_devices[yr_idx] += n_devices_this_rs
    if sample_scale:
        # Recover per-draw coral count from sampled device count × yield.
        acc.num_1yoec[yr_idx] += np.round(
            n_devices_this_rs * deploy_factors["coral_yield_1YOEC"].values[0:nsims]
        ).astype(int)
    else:
        acc.num_1yoec[yr_idx] += rs_num_1yoec  # scalar broadcasts across draws

    min_rs_yr = rs_years[0]
    eia_template_prod = fill_EIA_info(
        prod_wb, "CA_P", rep, min_rs_yr, iv_yr, "Townsville", eia_template_prod
    )
    eia_template_deploy = fill_EIA_info(
        deploy_wb, "CA_D", rep, min_rs_yr, iv_yr, departure_port, eia_template_deploy
    )

    # DEBUG HERE
    # Copies current state of excel sheets for each worker into current working dir.
    # import ipdb

    # ipdb.set_trace()

    # import shutil

    # shutil.copy(prod_model_fp, path_join(f"debug_prod_IDx.xlsx"))
    # shutil.copy(deploy_model_fp, path_join(f"debug_deploy_IDx.xlsx"))
    # shutil.copy(lm_model_fp, path_join(f"debug_lm_IDx.xlsx"))

    # Reset workbooks, discarding any changes
    deploy_wb = reset_workbook(xlapp, deploy_wb, deploy_model_fp)
    prod_wb = reset_workbook(xlapp, prod_wb, prod_model_fp)

    return (
        deploy_wb,
        prod_wb,
        deploy_factors,
        prod_factors,
        eia_template_prod,
        eia_template_deploy,
    )


def _process_lm_reefset(
    xlapp,
    lm_wb,
    lm_model_fp,
    lm_factors,
    lm_spec,
    lm_pool_min,
    lm_pool_max,
    rs_spec_corals,
    rs_distance,
    departure_port,
    rs_years,
    iv_yr,
    rep,
    nsims,
    acc,
    yr_idx,
    eia_template_lm,
    sample_scale=False,
    workbook_session=None,
):
    """Run the LM spreadsheet model for one reefset and accumulate results.

    ``acc`` is updated in-place.

    Parameters
    ----------
    rs_spec_corals : DataFrame
        Rows from ID_key for this reefset/year/rep (columns: number_of_1YO_corals, rep).
    rs_distance : float
        Distance from port in nautical miles for this reefset.
    acc : _OverviewAccumulator
        Overview tracking arrays; modified in-place.
    sample_scale : bool, optional
        When ``True`` (cost exploration mode), the larval pool count is already
        sampled and the template coral counts are ignored.  Defaults to ``False``.
    workbook_session : WorkbookSession, optional
        A session object for caching and seeding optimization.

    Returns
    -------
    lm_wb, lm_factors, eia_template_lm
    """
    lm_factors = update_lm_factors(
        lm_factors, rs_spec_corals, lm_pool_min, lm_pool_max, sample_scale=sample_scale
    )
    lm_wb, lm_factors = collect_lm_costs(
        xlapp,
        lm_wb,
        lm_model_fp,
        lm_factors,
        lm_spec,
        workbook_session=workbook_session,
    )

    if sample_scale:
        # Per-draw distances sampled from deploy_factors; compute a per-draw multiplier.
        opex_mult = np.array([lm_opex_distance_multiplier(d) for d in rs_distance])
    else:
        opex_mult = lm_opex_distance_multiplier(rs_distance)

    raw_opex = lm_factors["opex"].values[0:nsims]
    acc.lm_raw_capex[yr_idx] += lm_factors["capex"].values[0:nsims]
    acc.lm_raw_opex[yr_idx] += raw_opex * opex_mult
    acc.lm_opex_pre_mult[yr_idx] += raw_opex
    acc.lm_opex_mult[yr_idx] += opex_mult  # broadcast scalar or add per-draw array
    acc.lm_num_larval_pool[yr_idx] += lm_factors["larval release pools"].values[0:nsims]
    acc.lm_yield_per_pool[yr_idx] += lm_factors["yield_per_pool"].values[0:nsims]
    acc.lm_num_1yoec[yr_idx] += (
        lm_factors["larval release pools"].values[0:nsims]
        * lm_factors["yield_per_pool"].values[0:nsims]
    )

    eia_template_lm = fill_lm_EIA_info(
        lm_wb, "LM", rep, rs_years[0], iv_yr, departure_port, eia_template_lm
    )
    lm_wb = reset_workbook(xlapp, lm_wb, lm_model_fp)

    return lm_wb, lm_factors, eia_template_lm


# TODO: the core inventory logic here is identical to _apply_lm_inventory_model in
# sampling.py — the only difference is that this version is vectorised over draws
# (numpy arrays) while the LM version loops over years scalarly. Both could call a
# shared single-step helper to eliminate the duplication.
def _apply_outplant_inventory(yr_capex, outplant_inventory):
    """Apply the inventory/replacement model for outplant CAPEX.

    maintenance = 0.2 × inventory
    retained    = inventory − maintenance  (= 0.8 × inventory)

    * **Sufficient** (yr_capex ≤ retained): capex = 0, inventory decays to retained.
    * **Scaling up** (yr_capex > retained):
        capex     = (yr_capex − inventory) + maintenance  =  yr_capex − retained
        inventory = yr_capex

    Parameters
    ----------
    yr_capex : np.ndarray shape (nsims,)
        Full CAPEX requirement for this year across all reefsets.
    outplant_inventory : np.ndarray shape (nsims,)
        Accumulated inventory from prior years.

    Returns
    -------
    total_yr_capex : np.ndarray shape (nsims,)
    new_inventory : np.ndarray shape (nsims,)
    """
    maintenance = 0.2 * outplant_inventory
    retained = outplant_inventory - maintenance  # 0.8 × inventory
    needs_additional = yr_capex > retained
    total_yr_capex = np.where(
        needs_additional,
        (yr_capex - outplant_inventory) + maintenance,  # = yr_capex - retained
        0.0,
    )
    new_inventory = np.where(needs_additional, yr_capex, retained)
    return total_yr_capex, new_inventory


def _build_overview_rows_and_update_costs(
    cost_df, iv_years, nsims, acc, cont_p, scen_id, rep
):
    """Single pass over draws: apply LM inventory model, update cost_df, build overview rows.

    Combining these into one loop avoids calling ``_apply_lm_inventory_model`` twice
    per rep (once to fold into cost_df and once for the overview CSV).

    Parameters
    ----------
    cost_df : DataFrame
        Per-year cost dataframe for this rep; CAPEX/OPEX draw columns are updated in-place.
    iv_years : array-like
        Ordered intervention years for this scenario.
    acc : _OverviewAccumulator
        Accumulated raw values from the year loop.

    Returns
    -------
    overview_rows : list[dict]
    """
    overview_rows = []

    for draw_i in range(nsims):
        lm_inv = _apply_lm_inventory_model(
            pd.DataFrame(
                {
                    "capex": acc.lm_raw_capex[:, draw_i],
                    "opex": acc.lm_raw_opex[:, draw_i],
                    "corals": np.ones(len(iv_years)),
                }
            )
        )

        for yr_idx, iv_yr in enumerate(iv_years):
            # Fold LM inventory-adjusted costs into cost_df
            lm_cost_sum = np.array(
                [
                    [
                        float(lm_inv["total_capex"].iloc[yr_idx]),
                        float(lm_inv["opex"].iloc[yr_idx]),
                    ]
                ]
            )
            lm_components = cost_types(lm_cost_sum, cont_p, 1)
            draw_col = f"draw{draw_i + 1}"
            cost_df.loc[cost_df.year == iv_yr, draw_col] += lm_components[:, 0]

            # Build overview row — post-inventory CAPEX tracked independently per model.
            post_prod_capex = float(acc.prod_total_capex[yr_idx, draw_i])
            post_deploy_capex = float(acc.deploy_total_capex[yr_idx, draw_i])

            lm_capex = float(lm_inv["total_capex"].iloc[yr_idx])
            lm_opex_pre_distance = float(acc.lm_opex_pre_mult[yr_idx, draw_i])
            lm_opex_mult = float(acc.lm_opex_mult[yr_idx, draw_i])
            lm_opex = float(lm_inv["opex"].iloc[yr_idx])
            subtotal_capex = post_prod_capex + post_deploy_capex
            subtotal_opex = (
                acc.prod_opex[yr_idx, draw_i] + acc.deploy_opex[yr_idx, draw_i]
            )

            overview_rows.append(
                {
                    "intervention_id": scen_id,
                    "rep_id": rep,
                    "draw": draw_i + 1,
                    "year": int(iv_yr),
                    "num_devices": acc.num_devices[yr_idx, draw_i],
                    "num_1yoec": acc.num_1yoec[yr_idx, draw_i]
                    + acc.lm_num_1yoec[yr_idx, draw_i],
                    "num_larval_pool": acc.lm_num_larval_pool[yr_idx, draw_i],
                    "yield_per_pool": acc.lm_yield_per_pool[yr_idx, draw_i],
                    "production_capex": post_prod_capex,
                    "production_opex": acc.prod_opex[yr_idx, draw_i],
                    "deployment_capex": post_deploy_capex,
                    "deployment_opex": acc.deploy_opex[yr_idx, draw_i],
                    "lm_capex": lm_capex,
                    "lm_opex_pre_distance": lm_opex_pre_distance,
                    "lm_opex_multiplier": lm_opex_mult,
                    "lm_opex": lm_opex,
                    "subtotal_capex_CA_P_D": subtotal_capex,
                    "total_capex_CA_P_D_LM": subtotal_capex + lm_capex,
                    "subtotal_opex_CA_P_D": subtotal_opex,
                    "total_opex_CA_P_D_LM": subtotal_opex + lm_opex,
                }
            )

    return overview_rows


def _write_eia_outputs(
    cost_dir,
    scen_id,
    eia_template_prod,
    eia_template_deploy,
    eia_template_lm,
    all_sim_years,
    model_totals,
    lm_opex_mult_by_year=None,
    p_iter_id=0,
):
    """Fill missing years, compute row totals, and write EIA CSVs.

    Parameters
    ----------
    model_totals : dict
        ``{label: {"capex": Series, "opex": Series}}`` where each Series is
        indexed by ``(year, iteration)`` and gives the post-inventory total for
        that model.  Labels are ``"production"``, ``"deployment"``, ``"lm"``.
    p_iter_id : int, optional
        Worker index in a parallel run.  When non-zero, a ``_pid{p_iter_id}``
        suffix is appended to EIA filenames so workers do not overwrite each
        other.  Defaults to 0 (serial / worker-0 writes clean names).
    """
    eia_template_prod = _fill_eia_missing_years(eia_template_prod, all_sim_years)
    eia_template_deploy = _fill_eia_missing_years(eia_template_deploy, all_sim_years)
    eia_template_lm = _fill_eia_missing_years(eia_template_lm, all_sim_years)

    _meta_cols = ["iteration", "year", "intervention", "location", "type"]

    # If a template is still empty (its intervention type was not active in this
    # scenario), populate it with zero-cost rows mirroring the structure of the
    # first non-empty template so downstream processing always receives a valid file.
    _reference = next(
        (t for t in [eia_template_prod, eia_template_deploy, eia_template_lm] if not t.empty),
        None,
    )
    if _reference is not None:
        _ref_meta = _reference[_meta_cols].drop_duplicates()

        def _zero_fill(tmpl):
            if not tmpl.empty:
                return tmpl
            cost_cols = [c for c in tmpl.columns if c not in _meta_cols]
            rows = [
                {**row.to_dict(), **{c: 0.0 for c in cost_cols}}
                for _, row in _ref_meta.iterrows()
            ]
            return pd.DataFrame(rows, columns=tmpl.columns)

        eia_template_prod = _zero_fill(eia_template_prod)
        eia_template_deploy = _zero_fill(eia_template_deploy)
        eia_template_lm = _zero_fill(eia_template_lm)

    _pid = f"_pid{p_iter_id}"

    def _total_last(df):
        """Reorder columns so 'total' is always the final column."""
        cols = [c for c in df.columns if c != "total"] + ["total"]
        return df[cols]

    # --- Raw outputs (all three types) ---
    raw_templates = {}
    for eia_template, label in [
        (eia_template_prod, "production"),
        (eia_template_deploy, "deployment"),
        (eia_template_lm, "lm"),
    ]:
        cost_cols = [c for c in eia_template.columns if c not in _meta_cols]
        eia_template["total"] = (
            eia_template[cost_cols].apply(pd.to_numeric, errors="coerce").sum(axis=1)
        )
        eia_template.sort_values(_meta_cols, inplace=True)
        _total_last(eia_template).to_csv(
            path_join(cost_dir, f"EIA_raw_ID{scen_id}_{label}{_pid}.csv"), index=False
        )
        raw_templates[label] = eia_template

    # --- Proportional and scaled outputs (production, deployment, and lm) ---
    # Proportions are computed independently within each model: industry-code values
    # for each row divided by the sum of all industry codes in that model's Capex rows.
    # This means proportions sum to 1.0 within each EIA proportional file.
    # Scaled Capex = proportions × that model's own post-inventory Capex total.
    _non_cost = set(_meta_cols) | {"total", "labour"}

    for eia_template, label in [
        (raw_templates["production"], "production"),
        (raw_templates["deployment"], "deployment"),
        (raw_templates["lm"], "lm"),
    ]:
        cost_cols = [c for c in eia_template.columns if c not in _non_cost]
        numeric_cols = cost_cols + ["total"]

        # Per-model denominator: sum of all industry-code columns in Capex rows.
        capex_rows = eia_template[eia_template["type"].str.lower() == "capex"]
        ind_cols = [
            c for c in capex_rows.columns if c not in _non_cost
        ]  # TODO: same as cost_cols above; could reuse directly
        row_denom = (
            capex_rows[ind_cols]
            .apply(pd.to_numeric, errors="coerce")
            .sum(axis=1)
            .to_numpy()
        )

        shares = capex_rows[_meta_cols].copy()
        shares[numeric_cols] = (
            capex_rows[numeric_cols].apply(pd.to_numeric, errors="coerce").to_numpy()
            / np.where(row_denom == 0, np.nan, row_denom)[:, None]
        )
        shares[numeric_cols] = shares[numeric_cols].fillna(0.0)
        _total_last(shares).to_csv(
            path_join(cost_dir, f"EIA_proportional_ID{scen_id}_{label}{_pid}.csv"),
            index=False,
        )

        # Scaled Capex: proportional shares × this model's own post-inventory Capex total.
        scaled_capex = shares[_meta_cols].copy()
        scaled_capex[numeric_cols] = 0.0
        cap_idx = pd.MultiIndex.from_frame(shares[["year", "iteration"]])
        cap_scalars = (
            model_totals[label]["capex"].reindex(cap_idx).fillna(0.0).to_numpy()
        )
        scaled_capex[numeric_cols] = (
            shares[numeric_cols].to_numpy() * cap_scalars[:, None]
        )

        # Scaled Opex: raw values passed through, with labour included in total.
        # For LM, apply the distance multiplier to all cost values.
        opex_rows = eia_template[eia_template["type"].str.lower() == "opex"].copy()
        raw_ind = opex_rows[cost_cols].apply(pd.to_numeric, errors="coerce").fillna(0.0)
        raw_labour = pd.to_numeric(opex_rows["labour"], errors="coerce").fillna(0.0)
        if label == "lm" and lm_opex_mult_by_year:
            mult_vec = (
                opex_rows["year"].map(lm_opex_mult_by_year).fillna(1.0).to_numpy()
            )
            raw_ind = raw_ind.multiply(mult_vec, axis=0)
            raw_labour = raw_labour * mult_vec
        opex_rows[cost_cols] = raw_ind.to_numpy()
        opex_rows["labour"] = raw_labour.to_numpy()
        opex_rows["total"] = raw_ind.sum(axis=1).to_numpy() + raw_labour.to_numpy()
        scaled_opex = opex_rows[_meta_cols + cost_cols + ["labour", "total"]].copy()

        scaled = pd.concat([scaled_capex, scaled_opex]).sort_values(_meta_cols)
        _total_last(scaled).to_csv(
            path_join(cost_dir, f"EIA_scaled_ID{scen_id}_{label}{_pid}.csv"),
            index=False,
        )



def _merge_rep_costs(rep_cost_dfs):
    """Merge per-rep cost dataframes with draw/rep paired column names."""

    combined = None
    all_overview_rows = []
    rep_index = 0  # counts reps, NOT draws

    for _, cost_df, overview_rows in rep_cost_dfs:
        rep_index += 1
        all_overview_rows.extend(overview_rows)

        rep_draw_cols = [c for c in cost_df.columns if c.startswith("draw")]

        rename_map = {
            c: f"draw{i + 1}_rep{rep_index}"
            for i, c in enumerate(rep_draw_cols)
        }

        renamed = cost_df.rename(columns=rename_map)

        if combined is None:
            combined = renamed
        else:
            new_draw_cols = list(rename_map.values())
            combined = combined.merge(
                renamed[["year", "component"] + new_draw_cols],
                on=["year", "component"],
            )

    return combined, all_overview_rows

# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def calculate_costs(
    stores: OutputStores,
    ID_key_fn: str,
    nsims: int,
    deploy_model_filepath: str,
    prod_model_filepath: str,
    lm_model_filepath: str,
    cont_p: float = 0.25,
    p_iter_id: int = 0,
    active_models: set = None,
    sample_scale: bool = False,
    session_reset_interval: int = _RESET_INTERVAL,
    seed: int = None,
):
    """
    Sample costs for a set of interventions specified in ID_key, sampling nsims.

    Parameters
    ----------
    stores : OutputStores
        Data class holding output directory locations
    ID_key_fn : str
        Target filename for output.
    nsims : int
        Total number of draws to sample cost models, should match ecological metrics sampling.
    deploy_model_filepath : string
        Path to deployment cost model.
    prod_model_filepath : string
        Path to production cost model.
    lm_model_filepath : string
        Path to LM cost model.
    cont_p : float
        Contingency cost proportion.
    p_iter_id : int
        ID used for parallel sampling to keep track of batches for ordered recombination.
    active_models : set, optional
        Set of intervention types to compute costs for.  Valid values are
        ``"outplant"`` and ``"lm"``.  Defaults to both when ``None``.
        Pass ``{"outplant"}`` for CA-only or ``{"lm"}`` for LM-only scenarios.
    sample_scale : bool, optional
        When ``True`` (cost exploration mode), intervention scale (``num_1yoec``
        for outplant, larval pool count for LM) is drawn from the Sobol samples
        rather than derived from the RME template coral counts.  Defaults to
        ``False``.
    session_reset_interval : int, optional
        Recycle the process-local Excel session every N calls to bound memory
        growth.  Defaults to ``_RESET_INTERVAL`` (50).
    seed : int, optional
        Random seed for reproducibility.
    """
    # Derive unique seed per worker for reproducibility.
    # Use a default base seed if none is provided.
    base_seed = seed if seed is not None else 42
    worker_seed = base_seed + p_iter_id

    if active_models is None:
        active_models = {"outplant", "lm"}
    iv_keys_dir = stores.intervention_keys_dir
    cost_dir = stores.cost_dir

    iv_ID_key = f"ID_key_{ID_key_fn}"
    _iter_id = str(p_iter_id)
    run_id = f"run{_iter_id}"

    ID_key = pd.read_csv(
        path_join(iv_keys_dir, f"intervention_{iv_ID_key}_{run_id}.csv")
    )

    # Acquire (or create) the per-process persistent session for these model files.
    # The session owns private temp copies of the xlsx files so concurrent worker
    # processes never open the same physical file.
    proc_session = _get_process_session(
        deploy_model_filepath + ".xlsx",
        prod_model_filepath + ".xlsx",
        lm_model_filepath + ".xlsx",
        session_reset_interval,
    )
    proc_session.tick()
    deploy_model_fp = proc_session.deploy_fp
    prod_model_fp = proc_session.prod_fp
    lm_model_fp = proc_session.lm_fp
    workbook_session = proc_session.wb_session

    import multiprocess as _mp

    _in_worker = _mp.current_process().name != "MainProcess"

    try:
        # Read pool bounds once from config so update_lm_factors can clamp each year
        from .sampling import DEFAULT_LM_VER

        _lm_cfg = pd.read_csv(os.path.join(THIS_DIR, f"{DEFAULT_LM_VER}_LM_config.csv"))
        _pool_row = _lm_cfg.loc[_lm_cfg["factor_names"] == "larval release pools"].iloc[0]
        lm_pool_min = int(_pool_row["range_lower"])
        lm_pool_max = int(_pool_row["range_upper"])

        # Full simulation year range — costs are zero for non-intervention years
        start_year = int(ID_key["start_year"].iloc[0])
        end_year = int(ID_key["end_year"].iloc[0])
        all_sim_years = np.arange(start_year, end_year + 1)

        unique_ids = np.unique(ID_key.ID)
        cost_filepaths = []

        # Open all three workbooks (WorkbookSession caches them; subsequent calls are no-ops).
        deploy_wb = workbook_session.open_workbook(deploy_model_fp)
        prod_wb = workbook_session.open_workbook(prod_model_fp)
        lm_wb = workbook_session.open_workbook(lm_model_fp)
        xlapp = workbook_session.xlapp

        from tqdm import tqdm

        for scen_id in tqdm(
            unique_ids,
            desc=f"Calculating costs (worker {p_iter_id})",
            position=p_iter_id,
            leave=False,
        ):
            # Output EIA assessment for each intervention scenario
            eia_template_prod = create_eia_template(prod_wb)
            eia_template_deploy = create_eia_template(deploy_wb)
            eia_template_lm = create_lm_eia_template(lm_wb)

            scen_idx = ID_key.ID == scen_id
            iv_years = np.unique(ID_key.intervention_years[scen_idx])
            unique_reps = np.unique(ID_key.rep[scen_idx])

            rep_cost_dfs = []

            for rep in unique_reps:
                rep_idx = scen_idx & (ID_key.rep == rep)

                cost_df = initialize_cost_df(all_sim_years, nsims)

                # Sample cost model parameters once per rep — fixed across years.
                # Incorporate rep ID into seed so different reps get different draws.
                _rep_seed = worker_seed + int(rep) if worker_seed is not None else None
                (
                    prod_spec,
                    prod_factors,
                    deploy_spec,
                    deploy_factors,
                    lm_spec,
                    lm_factors,
                ) = sample_cost_model(nsims, seed=_rep_seed)

                # In normal mode, apply the actual port distance to deploy_factors so
                # the CSV reflects the value used in the simulation, not the config
                # best-point. Use the first reefset of the first intervention year as
                # representative (distance is fixed per reefset across years).
                # In exploration mode (sample_scale=True) the Sobol-sampled distance
                # is already in deploy_factors — do not overwrite it.
                if not sample_scale:
                    _first_yr = iv_years[0]
                    _first_rs = ID_key.loc[
                        (ID_key.intervention_years == _first_yr) & rep_idx, "reefset"
                    ].iloc[0]
                    _first_distance = ID_key.loc[
                        (ID_key.intervention_years == _first_yr)
                        & rep_idx
                        & (ID_key.reefset == _first_rs),
                        "distance_to_port_NM",
                    ].iloc[0]
                    deploy_factors["distance_from_port"] = float(_first_distance)

                # Track outplant CAPEX inventory separately per model for the inventory/replacement model
                prod_inventory = np.zeros(nsims)
                deploy_inventory = np.zeros(nsims)

                # Accumulate costs and metadata across years for the overview CSV
                acc = _OverviewAccumulator(n_years=len(iv_years), nsims=nsims)

                # Precompute per-reefset intervention years so CAPEX comparison
                # uses the previous year for the same reefset, not a global iv_yr - 1.
                reefset_years_map = {}
                for rs in ID_key.loc[rep_idx, "reefset"].unique():
                    rs_mask = rep_idx & (ID_key.reefset == rs)
                    reefset_years_map[rs] = sorted(
                        ID_key.loc[rs_mask, "intervention_years"].unique()
                    )

                for iv_yr in iv_years:
                    curr_selector = (ID_key.intervention_years == iv_yr) & rep_idx
                    yr_idx = iv_years.tolist().index(iv_yr)

                    iv_types_this_yr = (
                        ID_key.loc[curr_selector, "type"].str.lower().unique()
                    )

                    for iv_type in iv_types_this_yr:
                        if iv_type not in active_models:
                            continue

                        # Reefsets active in this year for this type — run the model once
                        # per reefset and accumulate costs so multi-region deployments are summed.
                        reefsets_this_yr = ID_key.loc[
                            curr_selector & (ID_key["type"].str.lower() == iv_type),
                            "reefset",
                        ].unique()

                        if iv_type == "outplant":
                            yr_prod_capex = np.zeros(nsims)
                            yr_deploy_capex = np.zeros(nsims)
                            yr_opex = np.zeros(nsims)

                            for rs in reefsets_this_yr:
                                rs_selector = curr_selector & (ID_key.reefset == rs)
                                _rs_cols = [
                                    "number_of_1YO_corals",
                                    "distance_to_port_NM",
                                    "number_of_groups",
                                    "rep",
                                ]
                                if "reef" in ID_key.columns:
                                    _rs_cols.append("reef")
                                rs_spec = ID_key.loc[rs_selector, _rs_cols]
                                departure_port = ID_key.loc[
                                    rs_selector.idxmax(), "port_name"
                                ]
                                rs_num_1yoec = ID_key.loc[
                                    rs_selector, "number_of_1YO_corals"
                                ].sum()

                                (
                                    deploy_wb,
                                    prod_wb,
                                    deploy_factors,
                                    prod_factors,
                                    eia_template_prod,
                                    eia_template_deploy,
                                ) = _process_outplant_reefset(
                                    xlapp,
                                    deploy_wb,
                                    prod_wb,
                                    deploy_model_fp,
                                    prod_model_fp,
                                    deploy_factors,
                                    deploy_spec,
                                    prod_factors,
                                    prod_spec,
                                    rs_spec,
                                    rs_num_1yoec,
                                    departure_port,
                                    reefset_years_map[rs],
                                    iv_yr,
                                    rep,
                                    nsims,
                                    yr_prod_capex,
                                    yr_deploy_capex,
                                    yr_opex,
                                    acc,
                                    yr_idx,
                                    eia_template_prod,
                                    eia_template_deploy,
                                    sample_scale=sample_scale,
                                    workbook_session=workbook_session,
                                )

                            # Inventory/replacement model applied separately to production and
                            # deployment so each model's retained capacity is tracked independently.
                            total_prod_capex, prod_inventory = _apply_outplant_inventory(
                                yr_prod_capex, prod_inventory
                            )
                            total_deploy_capex, deploy_inventory = (
                                _apply_outplant_inventory(yr_deploy_capex, deploy_inventory)
                            )
                            acc.prod_total_capex[yr_idx] = total_prod_capex
                            acc.deploy_total_capex[yr_idx] = total_deploy_capex
                            total_yr_capex = total_prod_capex + total_deploy_capex

                            cost_sum = np.column_stack([total_yr_capex, yr_opex])
                            component_costs = cost_types(cost_sum, cont_p, nsims)
                            cost_df.loc[cost_df.year == iv_yr, cost_df.columns[2:]] = (
                                component_costs
                            )

                        elif iv_type == "lm":
                            for rs in reefsets_this_yr:
                                rs_selector = curr_selector & (ID_key.reefset == rs)
                                departure_port = ID_key.loc[
                                    rs_selector.idxmax(), "port_name"
                                ]
                                if sample_scale:
                                    # Use the per-draw sampled distances from the
                                    # deployment factors (same geographic draw for LM).
                                    rs_distance = deploy_factors[
                                        "distance_from_port"
                                    ].values[0:nsims]
                                else:
                                    rs_distance = ID_key.loc[
                                        rs_selector, "distance_to_port_NM"
                                    ].iloc[0]

                                lm_wb, lm_factors, eia_template_lm = _process_lm_reefset(
                                    xlapp,
                                    lm_wb,
                                    lm_model_fp,
                                    lm_factors,
                                    lm_spec,
                                    lm_pool_min,
                                    lm_pool_max,
                                    ID_key.loc[
                                        rs_selector, ["number_of_1YO_corals", "rep"]
                                    ],
                                    rs_distance,
                                    departure_port,
                                    reefset_years_map[rs],
                                    iv_yr,
                                    rep,
                                    nsims,
                                    acc,
                                    yr_idx,
                                    eia_template_lm,
                                    sample_scale=sample_scale,
                                    workbook_session=workbook_session,
                                )

                        else:
                            raise ValueError(
                                "Unknown intervention type encountered. Must be one of 'outplant' or 'lm'."
                            )

                # Save sampled cost parameters now that the spreadsheet models have run
                # and capex/opex output columns have been added to the factor dataframes.
                _save_cost_params(
                    cost_dir,
                    scen_id,
                    rep,
                    p_iter_id,
                    prod_factors,
                    deploy_factors,
                    lm_factors,
                    active_models=active_models,
                )

                # Single pass over draws: fold LM inventory costs into cost_df and build overview rows
                overview_rows = _build_overview_rows_and_update_costs(
                    cost_df, iv_years, nsims, acc, cont_p, scen_id, rep
                )

                rep_cost_dfs.append((rep, cost_df, overview_rows))

            combined, all_overview_rows = _merge_rep_costs(rep_cost_dfs)

            combined["year"] = combined["year"].astype(int)
            combined["component"] = combined["component"].astype(int)

            cost_filepath = path_join(
                cost_dir,
                f"CBA_cost_scaled_ID{scen_id}_iter_pid{p_iter_id}.csv",
            )
            combined.to_csv(cost_filepath, index=False)
            cost_filepaths.append(cost_filepath)

            if all_overview_rows:
                overview_df = (
                    pd.DataFrame(all_overview_rows)
                    .sort_values(["intervention_id", "rep_id", "year"])
                    .reset_index(drop=True)
                )
                overview_df.to_csv(
                    path_join(
                        cost_dir,
                        f"ID{scen_id}_cost_overview_iter_pid{p_iter_id}.csv",
                    ),
                    index=False,
                )

            # EIA scaled outputs need a single multiplier per year; use the mean
            # across draws (in exploration mode draws have different distances).
            _mult_rows = [
                r for r in all_overview_rows if r.get("lm_opex_multiplier", 0) != 0
            ]
            if _mult_rows:
                _mult_df = pd.DataFrame(_mult_rows)[["year", "lm_opex_multiplier"]]
                lm_opex_mult_by_year = (
                    _mult_df.groupby("year")["lm_opex_multiplier"].mean().to_dict()
                )
            else:
                lm_opex_mult_by_year = {}

            _ov = pd.DataFrame(all_overview_rows)
            rep_draw_pair = _ov[["rep_id", "draw"]].drop_duplicates().sort_values(["rep_id", "draw"]).reset_index(drop=True)
            rep_draw_pair["iteration"] = rep_draw_pair.index + 1
            _ov = (
                _ov.merge(rep_draw_pair, on=["rep_id", "draw"], how="left")
                .set_index(["year", "iteration"])
            )

            model_totals = {
                "production": {
                    "capex": _ov["production_capex"],
                    "opex": _ov["production_opex"],
                },
                "deployment": {
                    "capex": _ov["deployment_capex"],
                    "opex": _ov["deployment_opex"],
                },
                "lm": {
                    "capex": _ov["lm_capex"],
                    "opex": _ov["lm_opex"],
                },
            }
            _write_eia_outputs(
                cost_dir,
                scen_id,
                eia_template_prod,
                eia_template_deploy,
                eia_template_lm,
                all_sim_years,
                model_totals,
                lm_opex_mult_by_year=lm_opex_mult_by_year,
                p_iter_id=p_iter_id,
            )

    finally:
        # In worker processes (parallel path), eagerly close Excel before the
        # task returns. Each worker runs exactly one task, so the persistent-
        # session optimisation is not needed there.  Closing here guarantees
        # cleanup on both the success path AND on any exception, because a
        # worker that raises stays alive waiting for more pool tasks — it is
        # only killed by pool.terminate() when the parent handles the
        # exception, which skips atexit on Windows.
        if _in_worker:
            # uninitialize_com=False: defer CoUninitialize() to natural process
            # exit so win32com.client's module-level COM caches release cleanly.
            proc_session._cleanup(uninitialize_com=False)
            _process_sessions.clear()

    return cost_filepaths

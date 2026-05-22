import win32com.client as w32client

import pandas as pd


def get_industry_codes(ws: w32client.CDispatch):
    """
    Retrieve industry classification codes found in the given worksheet.

    Parameters
    ----------
    ws : win32com.client.CDispatch
        Excel WorkSheet object

    Returns
    -------
    List of industry classification codes
    """
    industry_code_header = ws.Cells.Find("Industry classification code")

    if industry_code_header:
        # Get start of data
        start_cell = ws.Cells(industry_code_header.Row + 1, industry_code_header.Column)

        # Select data region
        data_range = start_cell.CurrentRegion
        df = pd.DataFrame(data_range.Value[1:])
    else:
        raise ValueError("Could not find industry classification codes!")

    return df.iloc[:, 0].to_numpy()


def find_table(ws: w32client.CDispatch, first_header: str) -> pd.DataFrame:
    """
    Identifies and retrieves table in a given worksheet identified by its first header.
    Selects contiguous data regions.

    Note: Drops NA rows.

    Parameters
    ----------
    ws : w32client.CDispatch
        Excel worksheet

    first_header: str
        Header of first column
    """
    initial_header = ws.Cells.Find(first_header)
    if not initial_header:
        raise ValueError(f"Header {first_header} not found in given worksheet!")

    region = initial_header.CurrentRegion

    # Explicitly define the range from the header row to the bottom of the region
    start_cell = ws.Cells(initial_header.Row, region.Column)
    end_cell = ws.Cells(
        region.Row + region.Rows.Count - 1, region.Column + region.Columns.Count - 1
    )
    table_range = ws.Range(start_cell, end_cell)

    data = table_range.Value
    df = pd.DataFrame(data[1:], columns=data[0])

    # Drop columns that are entirely empty (e.g. "batch" in the production model)
    # to avoid including rows that are just blank cells.
    df = df.loc[~df.loc[:, first_header].isna(), :].reset_index(drop=True)

    # If duplicate column names exist (e.g. two "Cost" columns), keep the last occurrence
    return df.loc[:, ~df.columns.duplicated(keep="last")]


def find_cost_table(ws: w32client.CDispatch) -> pd.DataFrame:
    """
    Identifies and retrieves cost table based on initial header "Amount".
    Selects contiguous data regions.

    Note: Drops NA rows.

    Parameters
    ----------
    ws : w32client.CDispatch
        Excel worksheet
    """
    initial_header = ws.Cells.Find("Amount")
    if initial_header:
        second_header = ws.Cells.FindNext(initial_header)

        # Check that it is not the same
        if second_header.Address != initial_header.Address:
            target_header = second_header
        else:
            target_header = initial_header

    else:
        raise ValueError("Appropriate table could not be found!")

    table_range = target_header.CurrentRegion
    data = table_range.Value

    type_header = ws.Cells.Find("Type")
    type_data = type_header.CurrentRegion.Value
    dt = (
        pd.DataFrame(type_data[1:], columns=type_data[0])["Type"]
        .dropna()
        .reset_index(drop=True)
    )

    cost_data = pd.DataFrame(data[1:], columns=data[0]).dropna().reset_index(drop=True)
    cost_data.loc[:, "Type"] = dt

    return cost_data


def create_eia_template(wb):
    # Setup EIA template
    eia_template = pd.DataFrame(
        columns=[
            "iteration",
            "year",
            "intervention",
            "location",
            "type",
        ]
    )

    ws = wb.Sheets("Scale CAPEX")

    # Get industry codes
    ind_codes = get_industry_codes(ws)
    unique_ind_codes = pd.unique(ind_codes)

    # Fill industry codes for CAPEX
    ind_codes = get_industry_codes(ws)
    for code in unique_ind_codes:
        eia_template[code] = None

    # Fill industry codes for OPEX
    ws = wb.Sheets("Batch OPEX")
    ind_codes = get_industry_codes(ws)
    unique_ind_codes = pd.unique(ind_codes)
    for code in unique_ind_codes:
        eia_template[code] = None

    eia_template["labour"] = None

    return eia_template


def find_or_fill_row(eia_template, it, year, intervention, port, expense_type):
    """
    Attempts to identify the matching row. If none found, adds a new row.
    Returns the row ID (of the existing row, or the new row).
    """
    if eia_template.empty:
        eia_template.loc[0] = [None] * len(eia_template.columns)
        return 0

    sel = (
        (eia_template.iteration == it)
        & (eia_template.year == year)
        & (eia_template.intervention == intervention)
        & (eia_template.location == port)
        & (eia_template.type == expense_type)
    )
    if sel.any():
        next_idx = sel.idxmax()
    else:
        next_idx = len(eia_template.index)
        eia_template.loc[next_idx] = [None] * len(eia_template.columns)

    return next_idx


def _setup_EIA_calculation(
    wb, sheet_name, eia_template, it, year, intervention, port, expense_type
):
    """Common setup for deployment cost calculations"""
    ws = wb.Sheets(sheet_name)

    if sheet_name.lower() == "batch opex":
        cost_df = find_cost_table(ws)
    else:
        cost_df = find_table(ws, "Resource")

    ind_codes = get_industry_codes(ws)
    unique_ind_codes = pd.unique(ind_codes)

    next_idx = find_or_fill_row(
        eia_template, it, year, intervention, port, expense_type
    )
    eia_template.iloc[next_idx, 0:5] = (
        it,
        year,
        intervention,
        port,
        expense_type,
    )

    return cost_df, ind_codes, unique_ind_codes, next_idx


def fill_industry_costs(eia_template, next_idx, cost_df, ind_codes, unique_ind_codes):
    """Single-pass update of industry costs in EIA template. Accumulates into existing
    values."""
    for code in unique_ind_codes:
        try:
            matches_code = ind_codes[cost_df.index] == code
        except Exception as exc:
            raise RuntimeError(
                f"Failed to match industry code {code!r} against cost_df index: {exc}"
            ) from exc

        if matches_code.sum() == 0:
            continue

        if code not in eia_template.columns:
            eia_template[code] = 0.0

        existing = eia_template.loc[next_idx, code]
        existing = float(existing) if pd.notna(existing) else 0.0

        try:
            eia_template.loc[next_idx, code] = existing + float(
                cost_df.loc[matches_code, "Cost"].sum()
            )
        except KeyError:
            eia_template.loc[next_idx, code] = existing + float(
                cost_df.loc[matches_code, "Cost/all"].sum()
            )

# TODO: fill_opex and fill_capex share the same _setup_EIA_calculation / fillna
# skeleton; the only difference is the labour split in fill_opex. Both could be
# merged into a single function with an expense_type parameter.
def fill_opex(it, _, year, intervention, port, eia_template, wb):
    """Fill OPEX industry costs and labour, summing all relevant costs for each industry code.

    Rows whose Type is 'passive labour' or 'active labour' are accumulated into
    the 'labour' column regardless of their industry classification code.
    All other rows are accumulated into their respective industry code columns.
    """
    sheet_name = "Batch OPEX"
    cost_df, ind_codes, unique_ind_codes, next_idx = _setup_EIA_calculation(
        wb, sheet_name, eia_template, it, year, intervention, port, "Opex"
    )

    # Identify labour rows (passive or active) — these go into the labour column only.
    is_labour = cost_df["Type"].str.lower().isin(["passive labour", "active labour"])

    existing_labour = eia_template.loc[next_idx, "labour"]
    labour_total = float(existing_labour) if pd.notna(existing_labour) else 0.0
    labour_total += float(cost_df.loc[is_labour, "Cost"].sum())
    eia_template.loc[next_idx, "labour"] = labour_total

    # Non-labour rows accumulate into their respective industry code columns.
    non_labour_df = cost_df.loc[~is_labour]
    for code in unique_ind_codes:
        matches_code = ind_codes[non_labour_df.index] == code

        if code not in eia_template.columns:
            eia_template[code] = 0.0

        existing = eia_template.loc[next_idx, code]
        existing = float(existing) if pd.notna(existing) else 0.0
        eia_template.loc[next_idx, code] = existing + float(
            non_labour_df.loc[matches_code, "Cost"].sum()
        )

    eia_template.fillna(0.0, inplace=True)

    return eia_template


def fill_capex(it, _, year, intervention, port, eia_template, wb):
    """Fill CAPEX industry costs, summing all relevant costs for each industry code."""
    sheet_name = "Scale CAPEX"
    cost_df, ind_codes, unique_ind_codes, next_idx = _setup_EIA_calculation(
        wb, sheet_name, eia_template, it, year, intervention, port, "Capex"
    )

    fill_industry_costs(eia_template, next_idx, cost_df, ind_codes, unique_ind_codes)

    eia_template.fillna(0.0, inplace=True)

    return eia_template


def _process_cost_section(eia_template, wb, shared_args):
    """Process a single cost section.

    Accumulates Capex and Opex into rows for the given
    (iteration, year, intervention, distance, port) combination.
    shared_args must be (it, iv_start_year, year, intervention, dest, port).
    """
    eia_template = fill_capex(*shared_args, eia_template, wb)
    eia_template = fill_opex(*shared_args, eia_template, wb)

    return eia_template


def create_lm_eia_template(wb) -> pd.DataFrame:
    """
    Create an EIA template for the LM cost model.

    The LM workbook has a single ``"Expenses"`` sheet (no CAPEX/OPEX split).
    Industry classification codes are read from that sheet and used as columns,
    with a trailing ``"labour"`` column for labour costs.
    """
    eia_template = pd.DataFrame(
        columns=["iteration", "year", "intervention", "location", "type"]
    )

    ws = wb.Sheets("Expenses")
    ind_codes = get_industry_codes(ws)
    for code in pd.unique(ind_codes):
        eia_template[code] = None

    eia_template["labour"] = None

    return eia_template


def fill_lm_EIA_info(
    wb,
    intervention: str,
    it: int,
    iv_start_year: int,
    year: int,
    port: str,
    eia_template: pd.DataFrame,
) -> pd.DataFrame:
    """
    Fill an LM EIA template with cost data from the ``"Expenses"`` sheet.

    All costs are treated as a single expense type (``"Expenses"``).
    Rows whose ``"Type"`` is ``"Labour"`` accumulate into the ``"labour"``
    column; all other rows accumulate into their industry code column using
    the ``"Total cost"`` column.

    Parameters
    ----------
    wb :
        Open LM cost model workbook.
    intervention : str
        Intervention type code, e.g. ``"LM"``.
    it : int
        ReefMod Engine Iteration ID.
    iv_start_year : int
        Year interventions begin.
    year : int
        Simulation year.
    port : str
        Port / location label.
    eia_template : pd.DataFrame
        Template to fill (as created by ``create_lm_eia_template``).
    """
    ws = wb.Sheets("Expenses")
    cost_df = find_table(ws, "Resource")
    ind_codes = get_industry_codes(ws)
    unique_ind_codes = pd.unique(ind_codes)

    _ = iv_start_year

    is_labour = cost_df["OPEX types"].fillna("").str.lower() == "labour"
    non_labour_df = cost_df.loc[~is_labour]

    # Create one EIA row per expense type (Capex, Opex, etc.) as declared in the sheet.
    for expense_type in non_labour_df["Type"].unique():
        normalized_type = expense_type.title()
        type_rows = non_labour_df[non_labour_df["Type"] == expense_type]
        next_idx = find_or_fill_row(
            eia_template, it, year, intervention, port, normalized_type
        )
        eia_template.iloc[next_idx, 0:5] = (
            it,
            year,
            intervention,
            port,
            normalized_type,
        )

        for code in unique_ind_codes:
            matches_code = ind_codes[type_rows.index] == code

            if code not in eia_template.columns:
                eia_template[code] = 0.0

            existing = eia_template.loc[next_idx, code]
            existing = float(existing) if pd.notna(existing) else 0.0
            eia_template.loc[next_idx, code] = existing + float(
                type_rows.loc[matches_code, "Total cost"].sum()
            )

    # Labour rows accumulate into the Opex row's labour column.
    opex_idx = find_or_fill_row(eia_template, it, year, intervention, port, "Opex")
    existing_labour = eia_template.loc[opex_idx, "labour"]
    labour_total = float(existing_labour) if pd.notna(existing_labour) else 0.0
    labour_total += float(cost_df.loc[is_labour, "Total cost"].sum())
    eia_template.loc[opex_idx, "labour"] = labour_total

    eia_template.fillna(0.0, inplace=True)
    eia_template["labour"] = eia_template.pop("labour")

    return eia_template


def fill_EIA_info(
    wb,
    intervention: str,
    it: int,
    iv_start_year: int,
    year: int,
    port: str,
    eia_template: pd.DataFrame,
):
    """
    Fill EIA template with cost data for a single workbook and intervention type.

    Parameters
    ----------
    wb :
        Cost model workbook (production or deployment).
    intervention : str
        Intervention type code, e.g. ``"CA-P"`` (production) or ``"CA-D"`` (deployment).
    it : int
        ReefMod Engine Iteration ID.
    iv_start_year : int
        Year interventions begin.
    year : int
        Simulation year.
    port : str
        Port where deployments launched from.
    eia_template : pd.DataFrame
        Template to fill.
    """
    shared_args = (it, iv_start_year, year, intervention, port)
    eia_template = _process_cost_section(eia_template, wb, shared_args)

    # Move labour column to last position
    eia_template["labour"] = eia_template.pop("labour")

    return eia_template

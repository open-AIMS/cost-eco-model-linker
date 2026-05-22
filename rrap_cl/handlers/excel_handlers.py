import os
import win32com.client as w32client
import pywintypes


def open_excel(fp, xlapp=None):
    """
    Open an Excel workbook.

    Parameters
    ----------
    fp : str
        Path to the workbook file.
    xlapp : Excel.Application, optional
        An existing Excel application instance to use. If None, a new
        isolated instance is created via DispatchEx. Pass an existing
        instance when opening multiple workbooks to avoid running several
        Excel processes simultaneously (which causes RPC_E_DISCONNECTED).

    Returns
    -------
    xlapp : Excel.Application
    wb : Workbook
    """
    wb_file_path = os.path.abspath(fp)

    if xlapp is None:
        xlapp = w32client.DispatchEx("Excel.Application")
        xlapp.Interactive = False
        xlapp.Visible = False
        xlapp.DisplayAlerts = False
        xlapp.EnableEvents = False

    wb = xlapp.Workbooks.Open(wb_file_path)

    # These properties often fail if set before a workbook is open
    xlapp.ScreenUpdating = False
    xlapp.Calculation = -4135  # xlCalculationManual

    return xlapp, wb


def close_excel(xlapp, wb, quit_app=True):
    """
    Close a workbook and optionally quit the Excel application.

    Parameters
    ----------
    xlapp : Excel.Application
    wb : Workbook
    quit_app : bool
        If True (default), quit the Excel application after closing the
        workbook. Pass False when the application hosts other open workbooks
        that should remain alive.
    """
    try:
        wb.Close(SaveChanges=False)
    except (pywintypes.com_error, AttributeError):
        pass  # wb may be stale if reset_workbook was called; xlapp.Quit() handles cleanup
    if quit_app:
        try:
            xlapp.Quit()
        except (pywintypes.com_error, AttributeError):
            pass  # xlapp COM object may already be dead (e.g. Excel crashed mid-run)


def reset_workbook(xlapp, wb, fp):
    """Reset workbook state. (Optimized: No longer re-opens file)"""
    # In the optimized version, we rely on overwrite-all-inputs behavior
    # in evaluate_spreadsheet rather than re-opening the file.
    return wb


class WorkbookSession:
    """
    Manages a persistent Excel application instance and caches open workbooks.

    This class ensures that only one Excel process is created per worker and
    that workbooks are reused across evaluations, significantly reducing the
    overhead of DispatchEx and Workbooks.Open calls.
    """

    def __init__(self):
        self.xlapp = None
        self.workbooks = {}  # path -> wb object
        self.seeded = set()  # set of paths that have been seeded

    def _get_xlapp(self):
        if self.xlapp is None:
            # DispatchEx creates an isolated Excel process per worker, which is
            # required for multiprocessing correctness (no shared COM apartment).
            # The trade-off vs. gencache.EnsureDispatch is late binding (every
            # attribute access goes through COM name resolution).  If this project
            # ever moves to single-process evaluation, switching to EnsureDispatch
            # would give early-binding performance at the cost of losing isolation.
            self.xlapp = w32client.DispatchEx("Excel.Application")
            self.xlapp.Interactive = False
            self.xlapp.Visible = False
            self.xlapp.DisplayAlerts = False
            self.xlapp.EnableEvents = False
        return self.xlapp

    def open_workbook(self, fp):
        """Open a workbook or return a cached instance."""
        fp = os.path.abspath(fp)
        if fp in self.workbooks:
            return self.workbooks[fp]

        xlapp = self._get_xlapp()
        wb = xlapp.Workbooks.Open(fp)

        # Performance properties MUST be set AFTER opening a workbook
        xlapp.ScreenUpdating = False
        xlapp.Calculation = -4135  # xlManual

        self.workbooks[fp] = wb
        return wb

    def is_seeded(self, fp):
        """Check if a workbook has already been seeded with baseline factors."""
        return os.path.abspath(fp) in self.seeded

    def mark_seeded(self, fp):
        """Mark a workbook as seeded."""
        self.seeded.add(os.path.abspath(fp))

    def cleanup(self, uninitialize_com=True):
        """Close all workbooks and quit the Excel application."""
        import gc

        # Pop and close each workbook, deleting the wrapper immediately so no
        # COM reference lingers as a loop variable past gc.collect().
        while self.workbooks:
            _, wb = self.workbooks.popitem()
            try:
                wb.Close(SaveChanges=False)
            except (pywintypes.com_error, AttributeError):
                pass
            del wb

        if self.xlapp:
            xlapp = self.xlapp
            self.xlapp = None
            try:
                xlapp.Quit()
            except (pywintypes.com_error, AttributeError):
                pass
            del xlapp

        # All COM wrappers are now explicitly released; gc.collect() handles
        # any remaining cyclic references before CoUninitialize tears down the apartment.
        gc.collect()

        if uninitialize_com:
            # Uninitialize the COM apartment for this thread
            import pythoncom
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass

        self.seeded = set()


def cleanup_excel_processes():
    """Quit all running Excel instances via COM.

    Enumerates the Windows Running Object Table (ROT) to find every open
    Excel workbook, collects their parent Application objects (deduplicated
    by window handle), and calls Quit() on each one.  This is safer than
    force-killing excel.exe because Excel gets to free its own COM state
    before the process exits.

    Returns
    -------
    int
        Number of Excel Application instances that were closed.
    """
    import gc

    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    try:
        ctx = pythoncom.CreateBindCtx(0)
        rot = pythoncom.GetRunningObjectTable()

        apps = {}
        for moniker in rot.EnumRunning():
            try:
                name = moniker.GetDisplayName(ctx, None)
                if not any(
                    name.lower().endswith(ext)
                    for ext in (".xlsx", ".xlsm", ".xls", ".xlsb")
                ):
                    continue
                obj = rot.GetObject(moniker)
                wb = win32com.client.Dispatch(obj.QueryInterface(pythoncom.IID_IDispatch))
                app = wb.Application
                apps[app.Hwnd] = app
            except Exception:
                pass

        for app in apps.values():
            try:
                app.DisplayAlerts = False
                app.Quit()
            except Exception:
                pass

        n = len(apps)
        del apps
        gc.collect()
        return n
    finally:
        pythoncom.CoUninitialize()


# TODO: Use objects to auto-handle state
# class ExcelConn:
#     """Excel file connection"""

#     xlapp
#     wb
#     fp

#     def __init__(self, fp):
#         # win32com Excel interface can only open files using their absolute paths
#         wb_file_path = os.path.abspath(fp)

#         # Open workbook
#         xlapp = w32client.DispatchEx("Excel.Application")
#         xlapp.Interactive = False
#         xlapp.Visible = False
#         xlapp.DisplayAlerts = False

#         wb = xlapp.Workbooks.Open(wb_file_path)
#         self.xlapp = xlapp
#         self.wb = wb
#         self.fp = fp

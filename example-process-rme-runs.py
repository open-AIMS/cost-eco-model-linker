import cost_calculations as cc
import process_RME_data as prd
import pandas as pd

# Filepath to RME runs to process
rme_files_path = "C:\\Users\\rcrocker\\Documents\\Github\\ReefModEngine.jl\\sandbox\\test_results_econ_metrics"

# Number of sims for metrics sampling (default includes ecological and expert uncertainty in RCI calcs)
nsims = 3
# Create metric datafiles for economics modelling and extract filename for intervention key
int_keys_fn = prd.create_economics_metric_files(rme_files_path)
# Number of cost model draws
n_draws = 2**2

# Load ID doc which links scenarios to settings and outputs
ID_key = pd.read_csv(int_keys_fn)
# Create cost datafiles for the intervention run ids in ID_key
cc.calculate_costs(ID_key, n_draws)

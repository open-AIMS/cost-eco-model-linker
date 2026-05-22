import rrap_cl as cl

# Filepath to RME runs to process
rme_files_path = "C:/users/dtan/data/exported_rme_results"

# Number of sims for metrics sampling (default includes ecological and expert uncertainty in RCI calcs)
nsims = 10

# Note: when coral_only=True, evaluate() will automatically use:
# 1. rci_3 logic (Cover, SV, Juveniles)
# 2. rfi logic
# 3. Transparently save the 3-metric RCI as '_var_rci.parq' for downstream compatibility.

cl.evaluate(
    rme_files_path,
    nsims,
    "3.8.0_deploy_config.csv",
    "3.8.0_prod_config.csv",
    "3.9.6_LM_config.csv", # Fixed placeholder to a valid model version
    "./",
    coral_only=True
)

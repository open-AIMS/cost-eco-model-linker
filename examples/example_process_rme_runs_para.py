import rrap_cl as cl
import multiprocessing as mp


# Filepath to RME runs to process
rme_files_path = "path to RME resultset"

# Number of sims for metrics sampling (default includes ecological and expert uncertainty in RCI calcs)
nsims = 10
ncores = 5  # number of cores to use

economics_spatial_filepath = ".//datasets//econ_spatial.csv"
econ_storage_path = ".//econ_outputs//"

# Create economics metrics input files, get number of batches needed to complete nsims over ncores
int_keys_fn, nbatches = cl.para_sample_econ(
    rme_files_path, nsims, economics_spatial_filepath, econ_storage_path, ncores=ncores
)

# Delete global variables which cause memory errors when running parallelised cost sampling
for var in list(globals().keys()):
    if var not in [
        "pc",
        "mp",
        "int_keys_fn",
        "nsims",
        "ncores",
        "nbatches",
        "__name__",
        "__builtins__",
        "__spec__",
    ]:
        del globals()[var]

# Run cost sampling in parallel on ncores
if __name__ == "__main__":
    pool = mp.Pool(ncores)
    result = [
        pool.apply(cl.calculate_costs, args=(iter_id, int_keys_fn, nbatches))
        for iter_id in range(ncores)
    ]
    pool.close()
    pool.join()

    # Post-process saved samples to be in single file
    pc.post_process_costs(result, nsims)

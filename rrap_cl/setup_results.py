from dotenv import dotenv_values

import os
from os.path import join as path_join

from copy import copy
from collections import OrderedDict
from dataclasses import dataclass

RESULT_DIRS = OrderedDict(
    {
        "cost_dir": "Costs",
        "rci_dir": os.path.join("Indicators", "rci"),
        "rfi_dir": os.path.join("Indicators", "rfi"),
        "rti_dir": os.path.join("Indicators", "rti"),
        "intervention_keys_dir": "intervention_keys",
    }
)


# Create immutable instances
@dataclass(frozen=True)
class OutputStores:
    cost_dir: str
    rci_dir: str
    rfi_dir: str
    rti_dir: str
    intervention_keys_dir: str


def setup_dirs(base_dir: str) -> OutputStores:
    """
    Create output directories at specified location.

    Parameters
    ----------
    base_dir : str
        Base directory for all output folders.

    Returns
    -------
    OutputStores
        Named collection of output directory paths.
    """
    config = copy(RESULT_DIRS)

    env_file = path_join("./.env")
    if os.path.isfile(env_file):
        user_config = {**dotenv_values(env_file)}

        # Merge dicts so that default values are used if not provided
        config = config | user_config

    for k, dir_name in config.items():
        full_path = path_join(base_dir, dir_name)
        os.makedirs(full_path, exist_ok=True)
        config[k] = full_path

    return OutputStores(**config)

from os.path import join as path_join, dirname
import numpy as np
import pandas as pd
from math import radians, cos, sin, asin, sqrt
from scipy.cluster.hierarchy import fclusterdata
from scipy.spatial import distance_matrix


REPR_PORTS = pd.read_csv(
    path_join(dirname(__file__), "representative_port_locations.csv")
)

REPR_REEFS = pd.read_csv(
    path_join(dirname(__file__), "representative_reef_locations.csv")
)


def haversine(x, y):
    """
    Calculate the great circle distance in kilometers between two points
    on the earth (specified in decimal degrees)

    Order of values are expected to be in longitude and latitude.
    """
    lon1, lat1 = x
    lon2, lat2 = y
    # convert decimal degrees to radians
    lon1, lat1, lon2, lat2 = map(radians, [lon1, lat1, lon2, lat2])

    # haversine formula
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    c = 2 * asin(sqrt(a))
    r = 6371  # Radius of earth in kilometers. Determines return value units.
    return c * r


def find_closest_port(iv_reef_spatial):
    """
    Find closest port from mean location of reefs where interventions occur.

    Returns
    -------
    Tuple, of the name of closest port and the distance in nautical miles.
    """
    # Use the mean distance to port:
    mean_lon = np.mean(iv_reef_spatial.LON)
    mean_lat = np.mean(iv_reef_spatial.LAT)
    distance_to_port = [
        haversine((mean_lon, mean_lat), list(port_pos))
        for port_pos in REPR_PORTS.loc[:, ["LON", "LAT"]].itertuples(index=False)
    ]

    min_dist_port_idx = np.argmin(distance_to_port)

    # Convert kilometers to nautical miles
    min_dist_port_NM = distance_to_port[min_dist_port_idx] * 0.539957
    closest_port = REPR_PORTS.loc[min_dist_port_idx, "port_name"]

    return closest_port, min_dist_port_NM


def find_representative_reef(reef_spatial_data, iv_reefs):
    """
    Find closest representative reef from mean location of reefs where interventions occur.

    Returns
    -------
    Name of closest representative reef.
    """
    iv_reef_spatial = reef_spatial_data.loc[
        reef_spatial_data["GBRMPA_ID"].isin(iv_reefs)
    ]

    # Use the mean distance to port:
    mean_lon = np.mean(iv_reef_spatial.LON)
    mean_lat = np.mean(iv_reef_spatial.LAT)

    distance_to_reef = [
        haversine((mean_lon, mean_lat), list(reef_pos))
        for reef_pos in REPR_REEFS.loc[:, ["LON", "LAT"]].itertuples(index=False)
    ]

    min_dist_port_idx = np.argmin(distance_to_reef)
    closest_reef = REPR_REEFS.loc[min_dist_port_idx, "reef_name"]

    return closest_reef


def find_representative_reefs(iv_reef_spatial, regions_data, max_dist=25.0):
    """
    Find clusters of reefs within max_dist of each other. These represent the furthest a ship would travel
    between reefs to implement interventions before going back to port. For reefs in unique clusters, the reef
    furthest from port is selected to represent the maximum travel distance from port, which is used to estimate
    the logistical cost of the intervention.

    Parameters
    ----------
    reef_spatial_data : dataframe
        A dataframe from the RME specified key reef IDs and spatial information (loaded from reefmod_gbr.gpkg).
    regions_data : dataframe
        A dataframe with key spatial and economics data for each reef in the GBR (loaded from econ_spatial.csv).
    iv_reefs : np.array(str)
        GBRMPA IDs of all reefs intervened at in the intervention
    max_dist : float
        Maximum allowable distance between reefs in a cluster.

    Returns
    -------
    representative_reefs : Tuple
        GBRMPA IDs which represent a subset of iv_reefs which give the reefs furthest
        from port for each cluster, their names, and distances.
    """
    # TODO: Refactor code here to use geospatial methods

    # Get lats and longs of intervention reefs
    X = iv_reef_spatial[["LON", "LAT"]].values

    # Cluster according to Haversine distance, with a maximum distance apart
    c_mat = fclusterdata(X, t=max_dist, metric=haversine, criterion="distance")

    # Find distance to port for each intervention reef
    iv_reef_spatial = iv_reef_spatial.assign(
        distance_to_port_NM=np.zeros((iv_reef_spatial.shape[0],))
    )

    min_dist_port_m = regions_data.minimum_distance_to_nearest_port_m
    for reef in iv_reef_spatial["UNIQUE_ID"].values:
        curr_reef = iv_reef_spatial["UNIQUE_ID"] == reef
        reg_curr_reef = regions_data["UNIQUE_ID"] == reef

        # Convert to nautical miles
        iv_reef_spatial.loc[curr_reef, "distance_to_port_NM"] = (
            min_dist_port_m[reg_curr_reef].iloc[0] * 0.00053996
        )

    representative_reefs = []
    rep_reef_names = []
    rep_reefs_max_dist = np.zeros(len(np.unique(c_mat)))

    # Get reef with highest distance to port as "representative" of each cluster
    for cl_idx, cl_id in enumerate(np.unique(c_mat)):
        subset = iv_reef_spatial.loc[c_mat == cl_id, :]

        dist_to_port = subset.loc[:, "distance_to_port_NM"]
        max_dist_to_port = np.argmax(dist_to_port)
        representative_reefs += [subset.loc[:, "GBRMPA_ID"].iloc[max_dist_to_port]]

        r_name = subset.loc[:, "reef_name"].iloc[max_dist_to_port]
        rep_reef_names += [r_name]

        rep_reefs_max_dist[cl_idx] = dist_to_port.iloc[max_dist_to_port]

    return representative_reefs, rep_reef_names, rep_reefs_max_dist


def find_representative_port(reef_spatial_data, iv_reefs):
    iv_reef_spatial = reef_spatial_data.loc[
        reef_spatial_data["GBRMPA_ID"].isin(iv_reefs)
    ]

    port_name, distance_NM = find_closest_port(iv_reef_spatial)

    return port_name, distance_NM


def find_max_reef_distance(reef_spatial_data, regions_data, iv_reefs, max_dist=25.0):
    """
    Finds the total estimated travel distance from port via the max distance to port from
    the closest reef cluster plus the sum of distances between that cluster and any other
    clusters.

    Parameters
    ----------
    iv_reefs : list
        List of reef IDs intervened at for a particular intervention
    data_store : dataframe
        Storage dataframe for creating economics metric files

    Returns
    -------
    Tuple, of (reef_ids, reef_names, distances)
    """

    iv_reef_spatial = reef_spatial_data.loc[
        reef_spatial_data["GBRMPA_ID"].isin(iv_reefs)
    ]

    if len(iv_reefs) == 1:
        # Only a single reef, so return details for that reef
        rep_reefs_sort = [iv_reefs[0], iv_reefs[0]]
        total_dist = (
            regions_data.loc[
                regions_data["UNIQUE_ID"] == iv_reef_spatial["UNIQUE_ID"].values[0],
                ["minimum_distance_to_nearest_port_m"],
            ].iloc[0]
        ).minimum_distance_to_nearest_port_m  # Convert to nautical miles

        reef_name = iv_reef_spatial.reef_name.iloc[0].split(" ")[0]

        # Convert directly to nautical miles
        return rep_reefs_sort, [reef_name, reef_name], total_dist * 0.00053996

    # Otherwise, find the most representative reef
    representative_reefs, rep_reef_names, rep_reefs_max_dist = (
        find_representative_reefs(iv_reef_spatial, regions_data, max_dist=max_dist)
    )

    # Find cluster which is closest to port and set distance to port as distance to this reef
    cl_idx_sort = np.argsort(rep_reefs_max_dist)
    initial_dist_from_port = rep_reefs_max_dist[cl_idx_sort[0]]

    # Order representative reefs from smallest to largest distance to port
    rep_reefs_sort = [representative_reefs[i] for i in cl_idx_sort]

    # Calculate distances between representative reefs from the closest to port reef to furthest
    rep_reef_spatial = reef_spatial_data.loc[
        reef_spatial_data["GBRMPA_ID"].isin(rep_reefs_sort)
    ]

    X = rep_reef_spatial[["LON", "LAT"]].values
    rep_reef_dist_mat = distance_matrix(X, X)

    total_dist = initial_dist_from_port
    for dist_idx in range(rep_reef_dist_mat.shape[1] - 1):
        total_dist += rep_reef_dist_mat[dist_idx, dist_idx + 1]

    return rep_reefs_sort, rep_reef_names, total_dist

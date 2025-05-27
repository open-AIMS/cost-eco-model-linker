import numpy as np
from math import radians, cos, sin, asin, sqrt
from scipy.cluster.hierarchy import fclusterdata
from scipy.spatial import distance_matrix

def haversine(x, y):
    """
    Calculate the great circle distance in kilometers between two points
    on the earth (specified in decimal degrees)

    """
    lon1, lat1 = x
    lon2, lat2 = y
    # convert decimal degrees to radians
    lon1, lat1, lon2, lat2 = map(radians, [lon1, lat1, lon2, lat2])

    # haversine formula
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    c = 2 * asin(sqrt(a))
    r = 6371 # Radius of earth in kilometers. Determines return value units.
    return c * r

def find_representative_reefs(iv_reef_spatial, regions_data, max_dist = 25.0):
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

    Returns:
        representative_reefs : List of GBRMPA IDs which represent a subset of iv_reefs which give the reefs furthest
        from port for each cluster.
    """
    # Get lats and longs of intervention reefs
    X = iv_reef_spatial[["LON","LAT"]].values

    # Cluster according to Haversine distance, with a maximum distance apart
    c_mat = fclusterdata(X, t=max_dist, metric=haversine, criterion='distance')

    # Find distance to port for each intervention reef
    iv_reef_spatial["distance_to_port_NM"] = np.zeros((iv_reef_spatial.shape[0],))
    for reef in iv_reef_spatial["UNIQUE_ID"].values:
        iv_reef_spatial.loc[iv_reef_spatial["UNIQUE_ID"]==reef, "distance_to_port_NM"] = (regions_data.loc[regions_data["UNIQUE_ID"]==reef,["minimum_distance_to_nearest_port_m"]].iloc[0]*0.00053996).minimum_distance_to_nearest_port_m # Convert to nautical miles

    representative_reefs = []
    rep_reefs_max_dist = np.zeros(len(np.unique(c_mat)))

    # Get reef with highest distance to port as "representative" of each cluster
    for (cl_idx, cl_id) in enumerate(np.unique(c_mat)):
        dist_to_port_argmax = np.argmax(iv_reef_spatial.loc[c_mat==cl_id, "distance_to_port_NM"])
        representative_reefs = representative_reefs + [iv_reef_spatial.loc[c_mat==cl_id, "GBRMPA_ID"].iloc[dist_to_port_argmax]]
        rep_reefs_max_dist[cl_idx] = iv_reef_spatial.loc[c_mat==cl_id, "distance_to_port_NM"].iloc[dist_to_port_argmax]

    return representative_reefs, rep_reefs_max_dist

def find_max_reef_distance(reef_spatial_data, regions_data, iv_reefs, max_dist = 25.0):
    """
    Finds the total estimated travel distance from port via the max distance to port from the closest reef cluster
    plus the sum of distances between that cluster and any other clsuters.

    Parameters
    ----------
        iv_reefs : list
            List of reef IDs intervened at for a particular intervention
        data_store : dataframe
            Storage dataframe for creating economics metric files
    """
    iv_reef_spatial = reef_spatial_data.loc[reef_spatial_data["GBRMPA_ID"].isin(iv_reefs)]

    if len(iv_reefs)>1:
        representative_reefs, rep_reefs_max_dist = find_representative_reefs(iv_reef_spatial, regions_data, max_dist = max_dist)

            # Find cluster which is closest to port and set distance to port as distance to this reef
        cl_idx_sort = np.argsort(rep_reefs_max_dist)
        initial_dist_from_port = rep_reefs_max_dist[cl_idx_sort[0]]

        # Order representative reefs from smallest to largest distance to port
        rep_reefs_sort = [representative_reefs[i] for i in cl_idx_sort]

        # Calculate distances between representative reefs from the closest to port reef to furthest
        rep_reef_spatial = reef_spatial_data.loc[reef_spatial_data["GBRMPA_ID"].isin(rep_reefs_sort)]
        X = rep_reef_spatial[["LON","LAT"]].values
        rep_reef_dist_mat = distance_matrix(X, X)

        total_dist = initial_dist_from_port
        for dist_idx in range(rep_reef_dist_mat.shape[1]-1):
            total_dist += rep_reef_dist_mat[dist_idx, dist_idx+1]
    else:
        rep_reefs_sort = [iv_reefs[0], iv_reefs[0]]
        total_dist = (regions_data.loc[regions_data["UNIQUE_ID"]==iv_reef_spatial["UNIQUE_ID"].values[0], ["minimum_distance_to_nearest_port_m"]].iloc[0]).minimum_distance_to_nearest_port_m # Convert to nautical miles


    return [rep_reefs_sort, total_dist*0.00053996]

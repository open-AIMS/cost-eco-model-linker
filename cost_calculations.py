import numpy as np
import json
import pandas as pd

from cost_model_queries.sampling.sampling_functions import (
    problem_spec,
    convert_factor_types,
    sample_deployment_cost,
    sample_production_cost
)

def load_config():
    with open('config.json', 'r') as file:
        return json.load(file)

config = load_config()

def get_N(n_draws, n_factors):
    return n_draws * ((2 * n_factors) + 2)

def cost_types(cost, contingency, N):
    # Calculate key cost codes
    # CAPEX - 1	sum of production and deployment cost
    # Contingency CAPEX	- 2	% of CAPEX
    # OPEX - 3	sum of production and deployment cost
    # Sustaining capital OPEX - 4 set to zero for now (assumed to be included in OPEX through contract)
    # Contingency OPEX - 5 % of OPEX
    # Vessel fuel - 6 only relevant if volunteer vessels are used - set to zero for now
    # CAPEX-monitoring - 7 set to zero (assumed no monitoring cost)
    # Contingency CAPEX-monitoring - 8	% of CAPEX-monitoring
    # OPEX-monitoring - 9 set to zero (assumed no monitoring cost)
    # Sustaining capital OPEX-monitoring - 10 set to zero (assumed no monitoring cost)
    # Contingency OPEX-monitoring - 11 % of OPEX-monitoring

    cost = np.reshape(np.array(cost), (2,N))
    return np.vstack((np.vstack((cost[0,:], cost[0,:]*contingency, cost[1,:], np.zeros(N), cost[1,:]*contingency)), np.zeros((6, N))))

def initialise_cost_df(years, N):
    # Dataframe for saving cost data to
    n_years = len(years)
    cols = ["year", "component"] + ["draw"+str(n) for n in range(1, N+1)]
    cost_df = pd.DataFrame(np.zeros((n_years*11, 2 + N)), columns=cols)
    cost_df["year"] = np.array(np.repeat(years, 11))
    cost_df["component"] = np.tile(np.array(range(1, 12)),n_years)
    return cost_df

def factors_dataframe_update(n_draws):
    # Sample deployment model factors
    sp_dep, factor_specs_dep = problem_spec("deployment")
    sp_dep.sample_sobol(n_draws, calc_second_order=True)

    factors_df_dep = pd.DataFrame(data=sp_dep.samples, columns=factor_specs_dep.factor_names)

    # Sample production model factors
    sp_prod, factor_specs_prod = problem_spec("production")
    sp_prod.sample_sobol(n_draws, calc_second_order=True)
    factors_df_prod = pd.DataFrame(data=sp_prod.samples, columns=factor_specs_prod.factor_names)

    # Convert factor types to suitable format for cost model sampling
    factors_df_dep = convert_factor_types(factors_df_dep, factor_specs_dep.is_cat)
    factors_df_prod = convert_factor_types(factors_df_prod, factor_specs_prod.is_cat)
    n_factors = np.min([factors_df_dep.shape[1], factors_df_prod.shape[1]])

    return factor_specs_dep, factors_df_dep, factor_specs_prod, factors_df_prod, n_factors

def update_factors(factors_df_dep, factors_df_prod, ID_key):
    factors_df_dep['num_devices'] = ID_key["number_of_1YO_corals"].iloc[0]
    factors_df_prod['num_devices'] = ID_key["number_of_1YO_corals"].iloc[0]
    factors_df_prod['species_no'] = ID_key["number_of_species"].iloc[0]
    factors_df_dep['port'] = int(ID_key["port_id"].iloc[0])
    factors_df_dep['distance_from_port'] = ID_key["distance_to_port_NM"].iloc[0]

    return factors_df_dep, factors_df_prod

def update_setupcost_factors(factors_df_dep, factors_df_prod, ID_key):
    factors_df_dep['num_devices'] = factors_df_dep['num_devices'] - ID_key["number_of_1YO_corals"].iloc[0]
    factors_df_prod['num_devices'] = factors_df_prod['num_devices'] - ID_key["number_of_1YO_corals"].iloc[0]

    return factors_df_dep, factors_df_prod

def calculate_costs(ID_key, n_draws, deploy_model_filepath=config["deploy_model_filepath"],
                 prod_model_filepath=config["prod_model_filepath"], cont_p = 0.8):

    for scen_id in np.unique(ID_key.ID):
        n_reps = int(max(ID_key.rep)) # Number of rme reps (ecological uncertainty)
        scen_idx = ID_key.ID==scen_id # Intervention scenario ID to link costs to ecological model outcomes
        int_years = ID_key.intervention_years[scen_idx] # Intervention years

        # Sample cost model factorswith n draws
        factor_specs_dep, factors_df_dep, factor_specs_prod, factors_df_prod, n_factors = factors_dataframe_update(n_draws)
        N = get_N(n_draws, n_factors)
        cost_df = initialise_cost_df(np.unique(int_years), N*n_reps)

        for (int_yr_idx, int_yr) in enumerate(int_years):
            for rep in range(n_reps):
                # Add key intervention parameters for year to dataframe as constants
                factors_df_dep, factors_df_prod = update_factors(factors_df_dep.iloc[0:N], factors_df_prod.iloc[0:N], ID_key[["number_of_1YO_corals", "port_id", "distance_to_port_NM", "number_of_species"]].loc[(ID_key.intervention_years==int_yr)&(ID_key.rep==rep+1)])

                # Sample deployment and production costs for dataframe parameters
                factors_df_dep = sample_deployment_cost(deploy_model_filepath, factors_df_dep, factor_specs_dep, n_draws, n_factors=n_factors)
                factors_df_prod = sample_production_cost(prod_model_filepath, factors_df_prod, factor_specs_prod, n_draws, n_factors=n_factors)

                if int_yr>int_years.iloc[0]:
                    # Save calculated operrational costs
                    save_cost_prod = factors_df_prod["Cost"]
                    save_cost_dep = factors_df_dep["Cost"]

                    # Adjust number of corals to "how many more are being deployed this year than last year?" to caculate setup cost correctly
                    factors_df_dep, factors_df_prod = update_setupcost_factors(factors_df_dep.iloc[0:N], factors_df_prod.iloc[0:N], ID_key[["number_of_1YO_corals", "port_id", "distance_to_port_NM", "number_of_species"]].loc[(ID_key.intervention_years==int_years.iloc[int_yr_idx-1])&(ID_key.rep==rep+1)])

                    if all(factors_df_dep['num_devices']<=0):
                        # If deploying no more than previous year, setup cost is zero
                        factors_df_prod["setupCost"] = 0
                        factors_df_dep["setupCost"] = 0
                    else:
                        # If deploying more than last year, recalculate setup cost for only those additional corals
                        factors_df_dep = sample_deployment_cost(deploy_model_filepath, factors_df_dep, factor_specs_dep, n_draws, n_factors=n_factors)
                        factors_df_prod = sample_production_cost(prod_model_filepath, factors_df_prod, factor_specs_prod, n_draws, n_factors=n_factors)

                        # Retain originally sampled operational cost for full number of corals, regardless of intervention year
                        factors_df_prod["Cost"] = save_cost_prod
                        factors_df_dep["Cost"] = save_cost_dep

                # Calculate all cost codes and add to dataframe
                cost_df.loc[cost_df.year==int_yr,cost_df.columns[rep*N+2:rep*N+N+2]] = cost_types(factors_df_dep[["Cost","setupCost"]]+factors_df_prod[["Cost","setupCost"]], cont_p, N)

                # Drop cost columns
                factors_df_dep = factors_df_dep.drop(columns=["Cost","setupCost"])
                factors_df_prod = factors_df_prod.drop(columns=["Cost","setupCost"])

        cost_df.to_csv('./Outputs/intervention'+str(scen_id)+'_mc_cost_data.csv')

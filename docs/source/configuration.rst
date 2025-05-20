Configuration files
===================

Sampling the intervention cost models requires 2 key configuration files 'config.csv' and 'config.json'.
The file 'config.csv' is a configuration file for the cost model parameters to sample and describes the
names, excel spreadsheet positions and ranges of parameters to sample from the cost models. An example of
this file is below:

.. image:: config_file_ex.png
  :width: 800

A full example of the 'config.csv' file is available in the examples folder. The other configuration file
specifies the path to the cost model excel documents. For example:

.. literalinclude:: config.json
  :language: JSON

Both configuration files should be placed in the same directory as the script calling functions in
Cost-eco-model-linker.

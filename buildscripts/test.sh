#!/bin/bash

source activate $CONDA_ENV

if [ "$RUN_COVERAGE" == "yes" ]; then
    export PYTHONPATH=.
    coverage erase
    coverage run --source=./hpat -m unittest
else
    python -m unittest
fi

mpiexec -n 2 python -m unittest
#mpiexec -n 3 python -m unittest

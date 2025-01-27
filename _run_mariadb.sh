#!/bin/bash --login
eval "$("$MAMBA_EXE" shell hook --shell bash --root-prefix "$MAMBA_ROOT_PREFIX" 2> /dev/null)"
micromamba activate benchmark_env
pip install mariadb

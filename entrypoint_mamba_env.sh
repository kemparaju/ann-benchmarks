#!/bin/bash --login
#set -e
export MAMBA_EXE='/root/.local/bin/micromamba'
export MAMBA_ROOT_PREFIX='/root/micromamba'
eval "$("$MAMBA_EXE" shell hook --shell bash --root-prefix "$MAMBA_ROOT_PREFIX" 2> /dev/null)"
micromamba activate benchmark_env
#exec "$@"
micromamba install --yes python=3.10.6
micromamba install --yes scipy numpy pip
python --version | grep 'Python 3.10'
python -m pip install docker mariadb
micromamba install -y -c conda-forge ansicolors==1.1.8 h5py==3.8.0 matplotlib==3.6.3 numpy==1.24.2 psutil==5.9.4 pytest==7.2.2
micromamba install -y pyyaml==6.0 scikit-learn==1.2.1 jinja2==3.1.2 datasets==2.12.0
python -u run_algorithm.py --dataset glove-100-angular --algorithm mariadb --runs 1 --count 5 --module ann_benchmarks.algorithms.mariadb --constructor MariaDB '["angular", {"txt": "test", "M": 8, "engine": "MyISAM"}]' '[[10], [20], [30], [40]]'

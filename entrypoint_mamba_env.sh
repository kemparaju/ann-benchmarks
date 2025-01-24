#!/bin/bash --login
set -e
micromamba activate benchmark_env
exec "$@"
micromamba install --yes python=3.10.6
micromamba install --yes scipy numpy git pip
python --version | grep 'Python 3.10'
pip install docker
micromamba install -y -c conda-forge ansicolors==1.1.8 h5py==3.8.0 matplotlib==3.6.3 numpy==1.24.2 psutil==5.9.4 pytest==7.2.2
micromamba install -y pyyaml==6.0 scikit-learn==1.2.1 jinja2==3.1.2 datasets==2.12.0
python -u run_algorithm.py

# FROM registry.access.redhat.com/ubi8:latest
FROM quay.io/kemparaju/ubuntu:22.04
RUN apt-get update
RUN apt-get install build-essential bzip2 procps wget -y
RUN wget micro.mamba.pm/install.sh
RUN '' Y Y '' | /bin/bash install.sh
COPY condarc_conf ${HOME}/.condarc
ENV PATH=$PATH:/root/.local/bin
ENV MAMBA_EXE='/root/.local/bin/micromamba'
ENV MAMBA_ROOT_PREFIX='/root/micromamba'
RUN micromamba create -n benchmark_env python=3.10.6
WORKDIR /home/app
COPY requirements.txt run_algorithm.py ./
COPY entrypoint_mamba_env.sh ./
RUN chmod u+x ./entrypoint_mamba_env.sh
ENTRYPOINT /home/app/entrypoint_mamba_env.sh

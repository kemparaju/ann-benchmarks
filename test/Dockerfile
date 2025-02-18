# FROM registry.access.redhat.com/ubi8:latest
FROM quay.io/kemparaju/ubuntu:22.04
RUN apt-get update && apt-get -y upgrade
RUN apt-get install libmariadb3 libmariadb-dev build-essential bzip2 procps wget git -y
WORKDIR /home/app
COPY requirements.txt run_algorithm.py ./
COPY entrypoint_mamba_env.sh ./
COPY ann_benchmarks ./ann_benchmarks

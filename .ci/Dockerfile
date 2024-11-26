
# SPDX-License-Identifier: Apache-2.0
# Copyright 2024 Arm Limited

FROM ubuntu:noble

ENV DEBIAN_FRONTEND=noninteractive 

RUN set -ex; \
    export DEBIAN_FRONTEND=noninteractive; \
    apt-get update \
    && apt-get -y install software-properties-common \
    && add-apt-repository -y ppa:deadsnakes/ppa \
    && apt-get update \
    && apt-get -y install gcc make zip git \
                          python3 python3-pip python3-venv \
                          python3.8 python3.9 python3.10 python3.11 python3.12 python3.13 \
                          python3.8-distutils python3.9-distutils \
     && if [ "$(uname -m)" = "aarch64" ]; then \
        apt-get -y install gcc-10; \
    else \
        apt-get -y install gcc-10-aarch64-linux-gnu gcc-aarch64-linux-gnu; \
    fi; \
    rm -rf /var/lib/apt/lists/* /usr/share/man/* /usr/share/doc/*;

ARG UBUNTU_VERSION=22.04

FROM ubuntu:${UBUNTU_VERSION} AS builder

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        cmake \
        csh \
        file \
        git \
        gfortran \
        libnetcdf-dev \
        libnetcdff-dev \
        m4 \
        perl \
        python3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src/wrf
COPY . .

RUN git submodule update --init --recursive \
    && ./ci/build-wrf.sh

FROM ubuntu:${UBUNTU_VERSION}

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libgfortran5 \
        libgomp1 \
        libnetcdf19 \
        libnetcdff7 \
        libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/wrf /opt/wrf
COPY ci/verify-wrf-runtime.sh /usr/local/bin/verify-wrf-runtime.sh
COPY ci/smoke-test-wrf.sh /usr/local/bin/smoke-test-wrf.sh

RUN chmod +x /usr/local/bin/verify-wrf-runtime.sh \
    /usr/local/bin/smoke-test-wrf.sh \
    && /usr/local/bin/verify-wrf-runtime.sh

WORKDIR /opt/wrf/run

FROM ubuntu:22.04 AS builder

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
    && ./configure_new -p GNU -x -d _build -i /opt/wrf \
    && ./compile_new _build -j"$(nproc)" \
    && test -e /opt/wrf/run/real.exe \
    && test -e /opt/wrf/run/wrf.exe

FROM ubuntu:22.04

COPY --from=builder /opt/wrf /opt/wrf
WORKDIR /opt/wrf/run

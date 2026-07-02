FROM mcr.microsoft.com/playwright:v1.53.0-noble

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
ENV WORKBENCH_SKIP_PLAYWRIGHT_INSTALL=1

USER pwuser

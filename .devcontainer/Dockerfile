FROM mcr.microsoft.com/devcontainers/python:1-3.11-bullseye

# Install uv by copying it from the official image (fastest and safest route)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# (Optional) Install extra system dependencies if you need them
# RUN apt-get update && export DEBIAN_FRONTEND=noninteractive \
#     && apt-get -y install --no-install-recommends <your-packages>

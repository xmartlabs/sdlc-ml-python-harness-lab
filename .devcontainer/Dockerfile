FROM mcr.microsoft.com/devcontainers/python:1-3.11-bullseye

# Instalar uv copiándolo desde su imagen oficial (método más rápido y seguro)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# (Opcional) Instalar dependencias del sistema adicionales si las necesitas
# RUN apt-get update && export DEBIAN_FRONTEND=noninteractive \
#     && apt-get -y install --no-install-recommends <tus-paquetes>

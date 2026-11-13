FROM ghcr.io/astral-sh/uv:0.13.8 as uv
FROM python:3.13.18-trixie
COPY --from=uv /uv /uvx /bin/

ENV PYTHONUNBUFFERED 1

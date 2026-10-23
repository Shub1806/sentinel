# syntax=docker/dockerfile:1

# ---- Stage 1: install Python libraries into a virtual environment ----
FROM python:3.14-slim-trixie AS build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY requirements.txt .
RUN pip install -r requirements.txt

# ---- Stage 2: small runtime image (no pip cache, no build leftovers) ----
FROM python:3.14-slim-trixie
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
RUN useradd --system --uid 10001 --no-create-home app
WORKDIR /app
COPY --from=build /opt/venv /opt/venv
COPY app ./app
USER 10001
EXPOSE 8003
# 0.0.0.0 = accept connections from outside the container (the default 127.0.0.1 would not)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8003"]

# ──────────────────────────────────────────────────────────────────────────────
# AMC.Py.Agent.EdiAuto — Dockerfile
# Puerto: 8080 (estándar AMC). Variable DOCKER=SI activa modo producción.
# ──────────────────────────────────────────────────────────────────────────────

FROM python:3.12-slim AS build
HEALTHCHECK NONE
WORKDIR /install

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/deps -r requirements.txt


FROM python:3.12-slim AS final
HEALTHCHECK NONE

ARG entorno=development
ARG AMC_VERSION=1.0.0

WORKDIR /app
EXPOSE 8080

ENV DOCKER="SI"
ENV ENVIRONMENT=${entorno}
ENV AMC_VERSION=${AMC_VERSION}
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

COPY --from=build /deps /usr/local
COPY . .

RUN groupadd --system app && useradd --system --gid app app \
    && chown -R app:app /app
USER app

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]

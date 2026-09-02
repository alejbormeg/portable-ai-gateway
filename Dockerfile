# SPDX-License-Identifier: AGPL-3.0-only
FROM python:3.14-slim@sha256:cad9a2c871761c413caa6fdd6441c783451e740a48aaeba60ae62a8b53525ef6 AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build
COPY . ./
RUN mkdir -p /build-wheels /project-wheel /runtime-wheels \
    && if [ -f requirements.lock ] && [ -f requirements-build.lock ]; then \
      python -m pip wheel --require-hashes --wheel-dir /runtime-wheels --requirement requirements.lock \
      && python -m pip wheel --require-hashes --wheel-dir /build-wheels --requirement requirements-build.lock \
      && python -m pip install --no-index --find-links=/build-wheels hatchling \
      && python -m pip wheel --no-index --find-links=/build-wheels --no-build-isolation --no-deps --wheel-dir /project-wheel .; \
    else \
      python -m pip wheel --wheel-dir /runtime-wheels ".[production]"; \
    fi

FROM python:3.14-slim@sha256:cad9a2c871761c413caa6fdd6441c783451e740a48aaeba60ae62a8b53525ef6 AS runtime

ARG VERSION=0.1.0-alpha
ARG REVISION=unknown
LABEL org.opencontainers.image.title="Portable AI Gateway" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}" \
      org.opencontainers.image.licenses="AGPL-3.0-only"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PAG_DATABASE_URL=sqlite:////data/gateway.db

RUN addgroup --system --gid 10001 gateway \
    && adduser --system --uid 10001 --ingroup gateway --home /nonexistent --no-create-home gateway \
    && mkdir -p /data /usr/share/licenses/portable-ai-gateway \
    && chown gateway:gateway /data
COPY --from=builder /build/LICENSE /usr/share/licenses/portable-ai-gateway/LICENSE
COPY --from=builder /build/THIRD_PARTY_NOTICES.md /usr/share/licenses/portable-ai-gateway/THIRD_PARTY_NOTICES.md
COPY --from=builder /build-wheels /build-wheels
COPY --from=builder /project-wheel /project-wheel
COPY --from=builder /runtime-wheels /runtime-wheels
RUN if find /project-wheel -maxdepth 1 -name 'portable_ai_gateway-*.whl' -print -quit | grep -q .; then \
      project_wheel="$(find /project-wheel -maxdepth 1 -name 'portable_ai_gateway-*.whl' -print -quit)" \
      && python -m pip install --no-cache-dir --no-index --find-links=/runtime-wheels "${project_wheel}[production]"; \
    else \
      python -m pip install --no-cache-dir --no-index /runtime-wheels/*; \
    fi \
    && rm -rf /build-wheels /project-wheel /runtime-wheels

USER 10001:10001
WORKDIR /app
EXPOSE 8080
ENTRYPOINT ["python", "-m", "portable_ai_gateway"]
CMD ["data-plane", "--host", "0.0.0.0", "--port", "8080"]

FROM python:3.12.14-slim-bookworm

ARG TARGETARCH=amd64
ARG SKOPEO_DEBIAN_VERSION=1.9.3+ds1-1+b10
ARG HELM_VERSION=v3.22.0
ARG HELM_SHA256_AMD64=1e4ab49e429626cf6c6958d914248b78c9730803c2751b87627e171dc800e7bb
ARG HELM_SHA256_ARM64=0236e2ebea9577799375a236c5e0e98d0eded4b9a3260678e51bfbc9dcca97a1

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/home/htp

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        ca-certificates \
        gzip \
        skopeo="${SKOPEO_DEBIAN_VERSION}" \
        tar; \
    rm -rf /var/lib/apt/lists/*; \
    skopeo --version | grep -F '1.9.3'

RUN set -eux; \
    case "${TARGETARCH}" in \
        amd64) helm_arch='amd64'; helm_sha="${HELM_SHA256_AMD64}" ;; \
        arm64) helm_arch='arm64'; helm_sha="${HELM_SHA256_ARM64}" ;; \
        *) echo "Unsupported TARGETARCH for Helm: ${TARGETARCH}" >&2; exit 2 ;; \
    esac; \
    archive="helm-${HELM_VERSION}-linux-${helm_arch}.tar.gz"; \
    python -c "import urllib.request; urllib.request.urlretrieve('https://get.helm.sh/${archive}', '/tmp/${archive}')"; \
    printf '%s  %s\n' "${helm_sha}" "/tmp/${archive}" | sha256sum -c -; \
    tar -xzf "/tmp/${archive}" -C /tmp; \
    install -m 0755 "/tmp/linux-${helm_arch}/helm" /usr/local/bin/helm; \
    rm -rf "/tmp/${archive}" "/tmp/linux-${helm_arch}"; \
    helm version --short | grep -F "${HELM_VERSION}"

RUN groupadd --gid 10001 htp \
    && useradd --uid 10001 --gid 10001 --create-home --home-dir /home/htp htp

WORKDIR /app
COPY backend/pyproject.toml ./pyproject.toml
COPY backend/app ./app
RUN python -m pip install --no-cache-dir .

RUN mkdir -p \
        /app/data/database \
        /app/data/packages \
        /app/data/incoming \
        /app/data/outgoing \
        /app/data/logs \
        /app/data/receipts \
        /app/data/tmp \
    && chown -R htp:htp /app /home/htp

USER htp
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

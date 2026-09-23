ARG RELEASE_VERSION=dev
ARG VCS_REF=unknown

FROM node:22.23.2-alpine3.24 AS build

WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM node:22.23.2-alpine3.24 AS docsify-vendor
ARG DOCSIFY_VERSION=5.0.0
WORKDIR /vendor
RUN npm pack --ignore-scripts --silent "docsify@${DOCSIFY_VERSION}" \
    && tar -xzf "docsify-${DOCSIFY_VERSION}.tgz"

FROM node:22.23.2-alpine3.24 AS docsify-extensions-vendor
ARG DOCSIFY_COPY_CODE_VERSION=3.0.1
ARG DOCSIFY_PAGINATION_VERSION=2.10.1
ARG PRISM_VERSION=1.29.0
WORKDIR /vendor
RUN mkdir -p copy-code pagination prism \
    && cd copy-code \
    && npm pack --ignore-scripts --silent "docsify-copy-code@${DOCSIFY_COPY_CODE_VERSION}" \
    && tar -xzf "docsify-copy-code-${DOCSIFY_COPY_CODE_VERSION}.tgz" \
    && cd ../pagination \
    && npm pack --ignore-scripts --silent "docsify-pagination@${DOCSIFY_PAGINATION_VERSION}" \
    && tar -xzf "docsify-pagination-${DOCSIFY_PAGINATION_VERSION}.tgz" \
    && cd ../prism \
    && npm pack --ignore-scripts --silent "prismjs@${PRISM_VERSION}" \
    && tar -xzf "prismjs-${PRISM_VERSION}.tgz"

FROM nginx:1.30.1-alpine

ARG RELEASE_VERSION
ARG VCS_REF
LABEL org.opencontainers.image.title="Harbor Transfer Portal frontend" \
      org.opencontainers.image.version="${RELEASE_VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}"

ENV PORTAL_FRONTEND_REVISION=${VCS_REF}

RUN rm -f /etc/nginx/conf.d/default.conf
COPY frontend/nginx.conf /etc/nginx/templates/default.conf.template
COPY frontend/docker-entrypoint.d/40-runtime-config.sh /docker-entrypoint.d/40-runtime-config.sh
COPY --from=build /app/dist /usr/share/nginx/html

# Documentation is part of the offline frontend artifact. Markdown stays the
# source of truth; Docsify renders it in-browser without runtime CDN access.
# Reuse the frontend design tokens directly so docs cannot drift to a second palette.
COPY docs/ /usr/share/nginx/html/docs/
COPY frontend/src/styles/tokens.css /usr/share/nginx/html/docs/_portal/tokens.css
COPY deploy/README.md /usr/share/nginx/html/deploy/README.md
COPY deploy/offline/README.md /usr/share/nginx/html/deploy/offline/README.md
COPY README.md CONTRIBUTING.md CHANGELOG.md LICENSE /usr/share/nginx/html/
COPY --from=docsify-vendor /vendor/package/dist/docsify.min.js /usr/share/nginx/html/docs/_vendor/docsify.min.js
COPY --from=docsify-vendor /vendor/package/dist/plugins/search.min.js /usr/share/nginx/html/docs/_vendor/search.min.js
COPY --from=docsify-vendor /vendor/package/dist/themes/core.min.css /usr/share/nginx/html/docs/_vendor/core.min.css
COPY --from=docsify-extensions-vendor /vendor/copy-code/package/dist/docsify-copy-code.min.js /usr/share/nginx/html/docs/_vendor/docsify-copy-code.min.js
COPY --from=docsify-extensions-vendor /vendor/pagination/package/dist/docsify-pagination.min.js /usr/share/nginx/html/docs/_vendor/docsify-pagination.min.js
COPY --from=docsify-extensions-vendor /vendor/prism/package/components/prism-bash.min.js /usr/share/nginx/html/docs/_vendor/prism-bash.min.js
COPY --from=docsify-extensions-vendor /vendor/prism/package/components/prism-batch.min.js /usr/share/nginx/html/docs/_vendor/prism-batch.min.js
COPY --from=docsify-extensions-vendor /vendor/prism/package/components/prism-powershell.min.js /usr/share/nginx/html/docs/_vendor/prism-powershell.min.js
COPY --from=docsify-extensions-vendor /vendor/prism/package/components/prism-docker.min.js /usr/share/nginx/html/docs/_vendor/prism-docker.min.js
COPY --from=docsify-extensions-vendor /vendor/prism/package/components/prism-json.min.js /usr/share/nginx/html/docs/_vendor/prism-json.min.js
COPY --from=docsify-extensions-vendor /vendor/prism/package/components/prism-yaml.min.js /usr/share/nginx/html/docs/_vendor/prism-yaml.min.js
COPY --from=docsify-extensions-vendor /vendor/prism/package/components/prism-python.min.js /usr/share/nginx/html/docs/_vendor/prism-python.min.js
COPY --from=docsify-extensions-vendor /vendor/prism/package/components/prism-sql.min.js /usr/share/nginx/html/docs/_vendor/prism-sql.min.js

RUN chmod 0755 /docker-entrypoint.d/40-runtime-config.sh

EXPOSE 8080

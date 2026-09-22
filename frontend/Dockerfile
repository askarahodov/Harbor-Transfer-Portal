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

RUN chmod 0755 /docker-entrypoint.d/40-runtime-config.sh

EXPOSE 8080

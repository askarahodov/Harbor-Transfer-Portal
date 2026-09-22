ARG RELEASE_VERSION=dev
ARG VCS_REF=unknown

FROM node:22.23.2-alpine3.24 AS build

WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

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

RUN chmod 0755 /docker-entrypoint.d/40-runtime-config.sh

EXPOSE 8080

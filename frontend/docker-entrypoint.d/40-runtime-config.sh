#!/bin/sh
set -eu

case "${PORTAL_CONTOUR:-}" in
    SOURCE|TARGET) ;;
    *)
        echo "PORTAL_CONTOUR должен быть SOURCE или TARGET" >&2
        exit 2
        ;;
esac

revision=${PORTAL_FRONTEND_REVISION:-unknown}
case "$revision" in
    ''|*[!A-Za-z0-9._-]*)
        echo "PORTAL_FRONTEND_REVISION содержит недопустимые символы" >&2
        exit 2
        ;;
esac

cat > /usr/share/nginx/html/runtime-config.js <<EOF
window.__HTP_CONFIG__ = Object.freeze({ contour: '${PORTAL_CONTOUR}', revision: '$revision' })
EOF

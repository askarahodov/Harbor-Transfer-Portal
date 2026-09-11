#!/bin/sh
set -eu

case "${PORTAL_CONTOUR:-}" in
    SOURCE|TARGET) ;;
    *)
        echo "PORTAL_CONTOUR must be SOURCE or TARGET" >&2
        exit 2
        ;;
esac

cat > /usr/share/nginx/html/runtime-config.js <<EOF
window.__HTP_CONFIG__ = Object.freeze({ contour: '${PORTAL_CONTOUR}' })
EOF

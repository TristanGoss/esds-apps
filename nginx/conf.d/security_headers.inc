# Response headers for the application server block. Included at SERVER level on purpose.
#
# nginx only inherits add_header from an enclosing level if the inner level defines none of its own, so a single
# add_header inside any `location` silently discards every header below for that location. Never put add_header
# inside a location block in this configuration (a test enforces it).
#
# This file has a .inc extension so the `include conf.d/*.conf` in the stock nginx.conf does not load it on its own.

add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
add_header X-Content-Type-Options "nosniff" always;
add_header X-Frame-Options "SAMEORIGIN" always;
add_header Referrer-Policy "strict-origin-when-cross-origin" always;
add_header Cross-Origin-Opener-Policy "same-origin" always;
add_header Permissions-Policy "camera=(self), microphone=(), geolocation=(), payment=(), usb=()" always;

# Scripts may only come from this origin or the one pinned CDN (and every CDN tag carries an SRI hash). Inline scripts and
# inline event handlers are not allowed, which is what stops an injected <script> from running. Inline *styles* are
# still allowed (style-src 'unsafe-inline') because the templates and ECharts use style attributes.
add_header Content-Security-Policy "default-src 'self'; script-src 'self' https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'self'" always;

# Disable caching during development
add_header Cache-Control "no-store, no-cache, must-revalidate, proxy-revalidate, max-age=0";

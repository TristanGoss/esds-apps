# Forward a request to the application container. No URI part on proxy_pass, so the request path is passed through
# unchanged whichever location includes this (a trailing slash would strip the matched prefix, e.g. /auth/).
proxy_pass http://esds-apps:8080;
proxy_set_header Host $host;
proxy_set_header X-Real-IP $remote_addr;
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;

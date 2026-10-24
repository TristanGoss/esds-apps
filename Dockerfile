# Pinned by digest so a rebuild cannot silently pick up a different base image. To update, pull the new tag, check the
# digest it resolves to, and review what changed: python:3.13-slim -> docker buildx imagetools inspect python:3.13-slim
FROM python:3.13-slim@sha256:d98cc29d3266e535dd9da1099688d9cb3ca4753813571e071763d17725460962

WORKDIR /app

# Install Cairosvg/WeasyPrint system dependencies. These come from Debian's own repositories and are deliberately not
# version-pinned: pinning them would block the security updates the base image's maintainers publish.
RUN apt-get update && apt-get install -y \
    libcairo2 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf-xlib-2.0-0 \
    libharfbuzz-subset0 \
    fonts-dejavu-core \
    fontconfig \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies in a virtualenv, installed from requirements.txt, which scripts/lock_tools.py generates from
# poetry.lock. Every package is pinned to an exact version and verified against its hash, and only the runtime ('main')
# group is installed: no Poetry, test tooling or Jupyter stack in the image.
RUN python -m venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --require-hashes --no-deps -r requirements.txt

COPY ./README.md /app/README.md
COPY ./src /app/src
COPY ./public /app/public
COPY ./templates /app/templates
COPY ./fonts/futura /usr/local/share/fonts
# Rebuild font cache to include provided fonts
RUN fc-cache -f -v

# Remember this file is not present in the repo, you need to add it manually!
# It contains the secrets that are not stored in the repository
COPY ./.env /app/.env

ENV PYTHONPATH=/app/src

RUN groupadd --gid 1001 appuser && useradd --uid 1001 --gid 1001 --no-create-home appuser
RUN chown -R appuser:appuser /app
RUN mkdir -p /tmp/esds_cache && chown appuser:appuser /tmp/esds_cache
USER appuser

CMD ["uvicorn", "esds_apps.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-server-header"]

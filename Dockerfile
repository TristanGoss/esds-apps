FROM python:3.13-slim

WORKDIR /app

RUN pip install poetry
COPY pyproject.toml poetry.lock /app/

# Install Cairosvg dependencies
RUN apt-get update && apt-get install -y \
    libcairo2 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf2.0-0 \
    fonts-dejavu-core \

# Install python dependencies only as a separate layer (for speed in rebuilding)
RUN poetry install --no-root

COPY ./README.md /app/README.md
COPY ./src /app/src
# Remember this file is not present in the repo, you need to add it manually!
COPY ./.env /app/.env

ENV PYTHONPATH=/app/src

RUN poetry install

CMD ["poetry", "run", "uvicorn", "esds_apps.main:app", "--host", "0.0.0.0", "--port", "8080"]
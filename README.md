# esds-apps

Various applications designed to assist the Edinburgh Swing Dance Society.

## Membership Cards
The first use of this repository is the provision of an add-on for dancecloud.com that issues styled membership cards to Edinburgh Swing Dance Society members.

## Secrets
Some settings are not present in config.py, and instead need to be provided by creating a `.env` file in the repository after it has been checked out. If you are planning to develop this code further and would like some relevant values for these settings, please email info@esds.org.uk to request them.

## Development setup
We use Ruff for linting. When developing, please install the pre-commit hooks after installing the package:
```bash
poetry install
poetry run pre-commit install
```

You can then run the auto-formatter like so:
```bash
poetry run ruff format .
```

And run the linter with auto-fixes like so:
```bash
poetry run ruff check --fix .
```

A settings file is included in the repo to help you integrate with CV Code, but you'll need to install the Ruff extension to use it.

### Running the dev server
```bash
poetry run uvicorn esds_apps.main:app --reload
```
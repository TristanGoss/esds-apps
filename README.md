# esds-apps

Various applications designed to assist the Edinburgh Swing Dance Society.

## Membership Cards
The first use of this repository is the provision of an add-on for dancecloud.com that issues styled membership cards to Edinburgh Swing Dance Society members.

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

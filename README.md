# esds-apps

Various applications designed to assist the Edinburgh Swing Dance Society.

## Membership Cards
The first use of this repository is the provision of an add-on for dancecloud.com that issues styled membership cards to Edinburgh Swing Dance Society members.

## Secrets
Some settings are not present in config.py, and instead need to be provided by creating a `.env` file in the repository after it has been checked out. If you are planning to develop this code further and would like some relevant values for these settings, please email info@esds.org.uk to request them.

## https
Note that https certificates are intended to be managed using certbot, so the docker-compose.yml mounts them from /etc/letsencrypt
https certificate renewal should be via the webroot method, using `/var/www/certbot` as the webroot path;
this will need to be manually configured after deployment by editing `/etc/letsencrypt/renewal/apps.esds.org.uk`.

## Service install
It's useful to install this repo as a systemd service:
```bash
sudo cp esds-apps.service /etc/systemd/system/esds-apps.service
sudo systemctl daemon-reexec
sudo systemctl daemon-reload
sudo systemctl enable esds-apps.service
sudo systemctl start esds-apps.service
```

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
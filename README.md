# esds-apps

Various applications designed to assist the Edinburgh Swing Dance Society.

## Membership Cards
The first use of this repository is the provision of an add-on for dancecloud.com that issues styled membership cards to Edinburgh Swing Dance Society members.

## Secrets
Some settings are not present in config.py, and instead need to be provided by creating a `.env` file in the repository after it has been checked out. If you are planning to develop this code further and would like some relevant values for these settings, please email info@esds.org.uk to request them.

The required variables are:

| Variable | Description |
|---|---|
| `COOKIE_SECRET` | Secret key used to sign session cookies. Any long random string. |
| `DC_API_TOKEN` | Dancecloud API bearer token. |
| `GMAIL_APP_EMAIL` | Gmail address used to send membership card emails. |
| `GMAIL_APP_PASSWORD` | Gmail app password for the above account. |
| `PASS2U_API_KEY` | pass2u.net API key for Apple/Google Wallet pass creation. |
| `DOOR_VOLUNTEERS_TEAM_ID` | Dancecloud team ID for the door volunteers group. |
| `GOOGLE_CLIENT_ID` | OAuth 2.0 client ID from Google Cloud Console. |
| `GOOGLE_CLIENT_SECRET` | OAuth 2.0 client secret from Google Cloud Console. |
| `GOOGLE_OAUTH_REDIRECT_URI` | The callback URL registered in Google Cloud Console. Use `http://localhost:8000/auth/callback` for local dev and `https://apps.esds.org.uk/auth/callback` in production. |
| `GOOGLE_ALLOWED_GROUP_EMAIL` | Email address of the Google Group whose members are granted access (e.g. `committee@esds.org.uk`). |
| `GOOGLE_ADMIN_IMPERSONATE_EMAIL` | Email of a Google Workspace admin account that the service account impersonates to call the Admin SDK. |
| `GOOGLE_SERVICE_ACCOUNT_FILE` | Absolute path to the service account JSON key file. In production (Docker) this is `/run/secrets/esds-group-checker-sa.json`. |
| `GOOGLE_WORKSPACE_DOMAIN` | Optional. If set (e.g. `esds.org.uk`), the Google sign-in prompt will be pre-filtered to that domain. Omit if you need to allow personal Gmail accounts that are members of the group. |

## Google Workspace / Cloud setup

Authentication uses Google OAuth with group membership enforcement. A one-time setup is required in Google Cloud Console and Google Workspace Admin.

### 1. Create an OAuth 2.0 client

1. In [Google Cloud Console](https://console.cloud.google.com), go to **APIs & Services → OAuth consent screen**. Choose **Internal** (restricts sign-in to your Workspace org; no app review required). Fill in app name and support email.
2. Go to **APIs & Services → Credentials → Create Credentials → OAuth 2.0 Client ID**. Choose **Web application**.
3. Under **Authorised redirect URIs**, add both:
   - `http://localhost:8000/auth/callback` (local development)
   - `https://apps.esds.org.uk/auth/callback` (production)
4. Save and note the **Client ID** and **Client Secret** — these go into `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`.

### 2. Enable the Admin SDK API

In **APIs & Services → Library**, search for **Admin SDK API** and enable it.

### 3. Create a service account

1. Go to **IAM & Admin → Service Accounts → Create service account**. Name it `esds-group-checker`. No project-level roles needed.
2. On the service account detail page, go to **Keys → Add Key → JSON**. Download the JSON file. In production, place it alongside `docker-compose.yml` as `esds-group-checker-sa.json`. Set `GOOGLE_SERVICE_ACCOUNT_FILE` to its absolute path.
3. On the service account detail page, click **Edit → Show advanced settings** and enable **Google Workspace Domain-wide Delegation**. Note the numeric **Client ID** shown.

### 4. Authorise domain-wide delegation in Workspace Admin

1. Go to [admin.google.com](https://admin.google.com) → **Security → Access and data control → API controls → Manage Domain-wide Delegation**.
2. Click **Add new** and enter:
   - **Client ID**: the numeric client ID from step 3
   - **OAuth scopes**: `https://www.googleapis.com/auth/admin.directory.group.member.readonly`
3. Save.

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
### cairosvg
Note that this software uses cairosvg, and so needs to install some non-python dependencies. If using Linux, these are clearly recorded in the Dockerfile, and you can simply follow the steps there to set up a dev environment. If on windows, things are more tricky.

To install cairosvg on windows, first download the GTK 3 Windows runtime from:
https://github.com/tschoonj/GTK-for-Windows-Runtime-Environment-Installer/releases. Make sure you add to path when installing.

### Linting
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

A settings file is included in the repo to help you integrate with VS Code, but you'll need to install the Ruff extension to use it.

### Running the dev server
```bash
poetry run uvicorn esds_apps.main:app --reload
```
# MUNDRA - MUNSoc Delegate Resource Application (1.0.0)

### Named after Mundra Port, Kutch, Gujarat, MUNDRA - MUNSoc Delegate Resource Application is a centralized database designed to optimize event planning, streamline communication, and facilitate delegate management

### Deployment environments

| Environment | Branch | Documentation URL                        |
| -------------| --------| ------------------------------------------|
| PROD        | main   | https://mundra.munsocietympstme.com/docs |

This backend is used by the Delego app available at
[AppStore](https://apps.apple.com/no/app/delego-mumbai-mun-2024/id1661612842) and will be available at PlayStore soon.

## Setup

Dependencies are managed with [uv](https://docs.astral.sh/uv/). `pyproject.toml` and
`uv.lock` are the source of truth; `requirements.txt` is a generated export kept for
hosts that do not run uv.

1. Clone the repository:

```bash
git clone https://github.com/munsoc-mpstme/mundra
cd mundra
```

2. Install the dependencies (creates `.venv` and installs the locked versions):

```bash
uv sync
```

3. Create your `.env`:

```bash
cp .env.example .env
```

Then fill in the two required values:

- `SECRET_KEY` — generate one with
  `uv run python -c "import secrets; print(secrets.token_urlsafe(48))"`
- `MAIL_SERVER` — set to `localhost` for local development. The SMTP connection is only
  opened when an email is actually sent, so every route except `/register` and
  `/forgot_password` works without a real mail server.

> **Note:** an empty value is not the same as an absent one. `DOCS_URL=` is read as the
> empty string and *overrides* the default rather than falling back to it, which silently
> disables that documentation route. Omit a key entirely if you want its default.

4. Run the development server:

```bash
uv run fastapi dev app.py
```

The SQLite databases under `databases/` are created automatically on first start.

### Running the tests

```bash
uv run pytest
```

The suite runs against a temporary database and does not touch `databases/`.

### Regenerating `requirements.txt`

```bash
uv export --no-hashes --no-dev --no-annotate --format requirements-txt -o requirements.txt
```

## Backend Documentation

### Overview

This backend is built with FastAPI to handle authentication, delegate management, and MUN event operations. It uses SQLite for persistent storage and includes key features such as:

 - User registration and login
 - Email verification and password reset
 - Delegate data querying and updates
 - Admin-only endpoints
 - Mumbai MUN–specific delegate routes
 - Automatic QR code generation
 
## Project Structure

 - **app.py**: Main entry point containing routes for auth, delegate actions, and admin tasks.
 - **auth.py**: Handles JWT-based authentication (creation/verification of tokens) and password hashing.
 - **config.py**: Pydantic-settings `Settings` model, loaded from `.env`.
 - **database.py**: Interacts with the SQLite databases (main and mm).
 - **models.py**: Defines pydantic models for Admin, Delegate, User, etc.
 - **mails.py**: Sends email via FastMail (for verification and password reset).
 - **templates/**: HTML templates for pages like password reset, food selection, and QR scanning.
 - **data/**: JSON content served by the app (`rooms.json`, `schedule.json`).
 - **tests/**: Smoke tests covering the unauthenticated routes, templates and admin flow.
 - **utils.py**: Contains helper functions, such as QR code generation.

## Key Endpoints
### Below is a concise list. See the code for exact response and request models.

### Auth Routes

 1. `POST /register`: Register a new user (creates Delegate if needed).
 2. `POST /login`: Obtain JWT with email + password.
 3. `GET /verify_email`: Verifies email with token.
 4. `GET /resend_verification`: Resend verification email.
 5. `GET /forgot_password`: Send password reset email.
 6. `PATCH /change_pass`: Change an authenticated delegate’s password.
 7. `DELETE /account`: Delete an authenticated delegate’s account.

### Admin Routes

 1. `GET /hash_password`: Hashes the provided password (utility).
 2. `GET /backup`: Backs up main and MM databases into a zip.
 3. `GET /delegates`: Lists all delegates in JSON or CSV (admin only).
 4. `POST /manual_verify`: Manually verify delegate email.

### Delegate Routes

 1. `GET /delegates/me`: Returns the current delegate’s profile.
 2. `GET /delegates/{id}`: Gets a specific delegate (admin or same delegate).
 3. `PATCH /delegates/{id}`: Updates delegate data (admin or same delegate).
 
### Mumbai MUN Routes

 1. `POST /mumbaimun/register`: Register user as Mumbai MUN delegate.
 2. `GET /mumbaimun/delegates`: Returns all MM delegates in JSON or CSV (admin only).

### QR-Related Routes

 1. `GET /qr`: Returns QR code image for a given ID (generates if not found).
 2. `GET /scan`: Serves a page to scan QR codes.
 3. `GET /food`: Returns a page to update meal preferences for a delegate.
 4. `POST /food`: Submits meal preferences for a delegate.

## Authentication & Security
 - Uses JWT with a secret key.
 - Passwords are hashed with bcrypt.
 - Many routes are protected by Depends(get_current_user) to verify tokens.
 - Admin endpoints only accessible to the Admin model, enforced at runtime.

## Database Interactions
 - SQLite is used.
 - database.py has functions to add, get, update, and delete user/delegate data.
 - A second DB (mm.db) stores Mumbai MUN delegates.
 - Backups are created as zipped copies of both DBs.

## API Usage
 - Send requests with Authorization: Bearer <token> to protected endpoints.
 - For CSV output, add ?format=csv to relevant endpoints.
 - JSON responses generally follow the pydantic models from models.py.

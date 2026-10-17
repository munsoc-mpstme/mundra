# MUNDRA - MUNSoc Delegate Resource Application (1.0.0)

### Named after Mundra Port, Kutch, Gujarat, MUNDRA - MUNSoc Delegate Resource Application is a centralized database designed to optimize event planning, streamline communication, and facilitate delegate management

### Deployment environments

| Environment | Branch | Documentation URL                        |
| -------------| --------| ------------------------------------------|
| PROD        | master | https://mundra.onrender.com/docs         |

This backend is used by the Delego app will be available at
AppStore and PlayStore soon.

## Setup

You need [uv](https://docs.astral.sh/uv/) and [Docker](https://www.docker.com/products/docker-desktop/)
(Docker Desktop on macOS or Windows, Docker Engine on Linux). `pyproject.toml` and `uv.lock`
are the source of truth for dependencies.

1. Clone the repository and install the dependencies (creates `.venv` with the locked versions):

```bash
git clone https://github.com/munsoc-mpstme/mundra
cd mundra
uv sync
```

> If `uv` says Python 3.11 is incompatible, an environment variable named `UV_PYTHON` is
> overriding the project's `.python-version`. Run `unset UV_PYTHON`, or
> `export UV_PYTHON=3.12` for the current terminal.

2. Create your `.env`:

```bash
cp .env.example .env
```

Then fill in the three required values:

- `POSTGRES_PASSWORD` - the password for the Postgres database. Pick anything for local
  development. Set it before the first `docker compose up`; changing it later does not
  change an existing database.
- `SECRET_KEY` - generate one with
  `uv run python -c "import secrets; print(secrets.token_urlsafe(48))"`
- `MAIL_SERVER` - set to `localhost` for local development. The SMTP connection is only
  opened when an email is actually sent, so every route except `/register` and
  `/forgot_password` works without a real mail server. (`/register` still creates the
  account before it fails to send the email.)

If something else on your machine already uses port 5432 (for example a Homebrew
Postgres), also set `POSTGRES_PORT=5433` (or any free port) in `.env`.

> **Note:** an empty value is not the same as an absent one. `DOCS_URL=` is read as the
> empty string and *overrides* the default rather than falling back to it, which silently
> disables that documentation route. Omit a key entirely if you want its default.

3. Start Postgres and create the tables:

```bash
docker compose up -d db
uv run alembic upgrade head
```

The database listens on `127.0.0.1` only, so it is reachable from your machine but not
from the network. Its data lives in the `pgdata` Docker volume and survives restarts.

4. Run the development server:

```bash
uv run fastapi dev app.py
```

The API is at http://localhost:8000 and the documentation at http://localhost:8000/docs.

To run the whole stack in containers instead (the app applies pending migrations on
start):

```bash
docker compose up -d --build
```

### Creating the first admin

Admins are ordinary users with the `admin` role. There is no route that creates the
first one, because someone has to be trusted before any route can be.

1. Register the account: `POST /register` with `firstname`, `lastname`, `email` and
   `password`. Without a real mail server this returns a 500 because the verification
   email cannot be sent, but the account is created.
2. Verify the email. Click the link in the email, or in local development skip it with
   `curl -X POST "http://localhost:8000/manual_verify?email=you@example.com"`.
3. Give the account the admin role, on the machine that runs the app:

```bash
uv run python database.py make-admin you@example.com
# in Docker: docker compose exec app python database.py make-admin you@example.com
```

**Without a shell (for example a free Render instance):** set `ADMIN_EMAIL` in the host's
environment settings (never in the code or the repo). On every start the server makes that
account an admin and verifies it; its password is left alone. If the account does not
exist yet, also set `ADMIN_PASSWORD` (at least 8 characters) and it is created on the next
start. Remove `ADMIN_PASSWORD` once the account exists. The password is never logged.

From then on, admins manage roles with `PATCH /admin/users/{email}/role`
(body: `{"role": "delegate" | "oc" | "admin"}`). Every change is recorded in the
`admin_audit` table. You cannot change your own role.

### Running the tests

```bash
docker compose up -d db
uv run pytest
```

The suite needs Postgres running and `POSTGRES_PASSWORD` set in `.env`. It creates a
separate database called `mundra_test` (dropping any previous one) and applies the
Alembic migrations to it, so it never touches your real data. The `GET /backup` tests are
skipped if `pg_dump` is not installed.

### Changing the schema

Tables are defined in `db.py`. After changing them, generate and apply a migration:

```bash
uv run alembic revision --autogenerate -m "describe the change"
uv run alembic upgrade head
```

Read the generated file in `alembic/versions/` before applying it.

### Backups

The `backup` service in `docker-compose.yml` writes a `pg_dump` into `./backups` every 24
hours and deletes dumps older than 14 days. `GET /backup` (admin only) takes a dump on
demand, returns it, and keeps a copy in `./backups` too (so the same cleanup applies).
Restore with `pg_restore --clean --dbname <url> <file>`. Copy `./backups` off the machine
as well; a backup on the same disk does not survive the disk.

On a Linux server, `./backups` and `./qrcodes` must be writable by the container's `app`
user (uid 1000), otherwise `GET /backup` and `GET /qr` fail with a permission error.
`sudo chown -R 1000:1000 backups qrcodes` fixes it. Docker Desktop on macOS and Windows
does not have this problem.

## Backend Documentation

### Overview

This backend is built with FastAPI to handle authentication, delegate management, and MUN event operations. It uses PostgreSQL for persistent storage and includes key features such as:

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
 - **db.py**: The async SQLAlchemy engine and the table classes (`DelegateRow`, `UserRow`, ...).
 - **database.py**: Query functions. They return pydantic models, never table rows.
 - **models.py**: Pydantic models for the API (Delegate, User, ...).
 - **alembic/**: Database migrations. `alembic.ini` and `alembic/env.py` read the connection from `.env`.
 - **docker-compose.yml**, **Dockerfile**: The app, Postgres and the scheduled backup.
 - **mails.py**: Sends email via FastMail (for verification and password reset).
 - **templates/**: HTML templates (currently the password reset page).
 - **data/**: JSON content served by the app (`rooms.json`, `schedule.json`).
 - **tests/**: Smoke, database and role tests, run against a real Postgres.
 - **utils.py**: Contains helper functions, such as QR code generation.

## Key Endpoints
### Below is a concise list. See the code for exact response and request models.

### Auth Routes

 1. `POST /register`: Register a new user (creates Delegate if needed).
 2. `POST /login`: Obtain JWT with email + password. An account whose email is not verified
    yet gets `403 "Please verify your email!"` and no token.
 3. `POST /verify_email`: Verify the email with the 6-digit code (`{"email", "code"}`).
 4. `GET /resend_verification`: Email a new 6-digit code.
 5. `GET /forgot_password`: Send password reset email.
 6. `PATCH /change_pass`: Change an authenticated delegate’s password.
 7. `DELETE /account`: Delete an authenticated delegate’s account.

### Admin Routes

 1. `GET /backup`: Runs `pg_dump` and returns the dump (admin only).
 2. `GET /delegates`: Lists all delegates in JSON or CSV (admin only).
 3. `POST /manual_verify`: Manually verify a delegate's email (any OC member).
 4. `PATCH /admin/users/{email}/role`: Set a user's role to delegate, eb, oc or admin (admin only, audited).

### Delegate Routes

 1. `GET /delegates/me`: Returns the current delegate’s profile, plus their OC access
    (`is_head`, `permissions`, `teams`).
 2. `GET /delegates/{id}`: Gets a specific delegate (admin or same delegate).
 3. `PATCH /delegates/{id}`: Updates delegate data (admin or same delegate).

### Mumbai MUN Routes

 1. `POST /mumbaimun/register`: Register user as Mumbai MUN delegate. The account starts
    unverified and a 6-digit code is emailed; it cannot log in until `POST /verify_email`
    accepts the code. The response reports `verified` and `email_sent` (if the email could
    not be sent the account is still created and the app offers "Send a new code").
    With `MAIL_SERVER=localhost` (local development) the code is printed in the server log.
 2. `GET /mumbaimun/delegates`: Returns all MM delegates in JSON or CSV (admin only).
 3. `GET /mumbaimun/delegates/me`: The caller's own MM details (name, food preference).
 4. `PATCH /mumbaimun/delegates/{id}/food_preference`: Set a delegate's diet (self, or
    hospitality/head/admin).

### QR-Related Routes

 1. `GET /qr`: Returns QR code image for a given ID (generates if not found). The QR
    encodes only the delegate id; the app displays name and preference around it.

### Food Routes (docs/adr/0003)

 1. `POST /food/scans`: Record a delegate collecting a meal (form fields `delegate_id`,
    `meal`, optional `diet`, optional `scanned_at`). The day is derived from the date in
    the conference's timezone; a second scan of the same meal returns `duplicate` and is
    flagged. `scanned_at` is the phone's scan time (ISO 8601 with an offset): it decides
    the day when it is at most 72 hours old and not in the future, so a scan saved offline
    and uploaded the next morning still counts for the day it was made. Otherwise the
    server's clock is used.
    Needs `food.manage_entitlement` (or the `oc` role, see "Delego app contract").
    `meal` is `breakfast`, `lunch` or `hitea`; `high_tea` is accepted as an alias.
 2. `GET /food/plate_count`: Live plate count for a meal today, by diet (the diet the
    operator picked at the scanner, else the delegate's registered preference).
 3. `GET /food/flags`: Rejected second-scans (who tried for seconds). Teams/heads only.
    Works outside the event's dates too.

### OC Admin Routes (docs/adr/0003)

 1. `GET /events`: List events (head/admin). `PATCH /events/{id}`: Set an event's
    start/end dates (head/admin).
 2. `GET /events/{id}/teams`: List an event's teams (any OC).
 3. `POST /events/{id}/teams`: Create a team with permissions (`team.manage_definition`).
 4. `PATCH /teams/{id}/permissions`: Replace a team's permissions (`team.manage_definition`).
 5. `GET|POST /teams/{id}/members`, `DELETE /teams/{id}/members/{email}`: Manage a team's
    roster (that team's lead, or a head/admin). Adding an unregistered email creates an
    invite that becomes a membership when they verify. Audited to `membership_audit`.
 6. `GET /events/{id}/heads`: List heads (head/admin).
 7. `POST /events/{id}/heads`, `DELETE /events/{id}/heads/{email}`: Grant/revoke a head
    (admin only).

### Chat & Committees (docs/adr/0003)

 1. `POST /events/{id}/committees`: Create a committee (head/admin).
 2. `GET /events/{id}/committees`: List committees and their session status (any user; a
    delegate can check whether their own committee has broken for a meal).
    `GET /committees`: the committees the caller may read, across events, in creation
    order (403 if none). This is the list the Delego app uses.
 3. `PATCH /committees/{id}/status`: Set `in_session`/`adjourned` (that committee's
    rapporteur, or head/admin).
 4. `GET|POST /committees/{id}/messages`: Read history / send a message. A committee's
    channel is that committee's rapporteurs + all hospitality + heads; `kind: "status"`
    messages carry a quick-action payload (the "we're free" / "running late" buttons).
 5. `WS /ws/committees/{id}/chat`: Live chat. The client sends `{"token": "<jwt>"}` as its
    first frame, then `NewChatMessage` frames. Fan-out is in-process (single uvicorn
    worker); Postgres holds the durable history. See `chat.py` for the swap-in point if the
    app is ever scaled to multiple processes.

## Delego app contract

The Delego mobile app was built against these behaviours, and `tests/test_app_contract.py`
replays its exact requests. They sit on top of the OC model above without replacing it.

- **Screens follow `GET /delegates/me` → `permissions`.** Besides the team permissions
  above, it lists the strings the app gates screens on, derived from the role (and team
  permissions) in `permissions.py`:

  | Role | App permissions |
  | --- | --- |
  | `delegate` | `guides.view`, `badge.view` |
  | `eb` | `guides.view`, `badge.view`, `eb.tools` |
  | `oc` | `eb.tools`, `food.scan`, `chat.view`, `chat.send_request` |
  | `admin` | all of the above plus `admin.roles` |

  A team member or head also gets `food.scan` / `chat.*` from the matching team
  permission: `chat.post` on an unscoped team (Hospitality) gives `chat.respond`, and on a
  committee-scoped team (a rapporteur) gives `chat.send_request` for that committee. These
  strings decide what the app shows, and the server enforces the same rule on every route
  and on the live feed.
- **The `oc` role is the baseline for meal scanning and break coordination.** An OC
  member can scan meals, read plate counts and use every committee's chat without being
  on a team. Teams and heads still work as before, and `/food/flags` stays team/head only.
- **Meal scanning works without event dates.** If no event's dates cover today, the scan
  is filed under the first event with the calendar date as its day key, so "once per meal
  per day" holds on any day. Set the dates with `PATCH /events/{id}` to get day 1, 2, 3.
- **Days are the conference's local days.** Day numbers and that calendar date are worked
  out in the event timezone (`EVENT_UTC_OFFSET_MINUTES`, default 330 = IST), not UTC.
  `PATCH /events/{id}` needs an offset on both values and an end that is not before the
  start, e.g. `{"starts_at": "2026-10-30T00:00:00+05:30", "ends_at":
  "2026-11-01T23:59:59+05:30"}`. Setting the dates also moves every team membership of
  that event to end at the midnight that closes its last local day. Set them before the
  conference: changing them on a conference day restarts that day's duplicate check.
- **Break requests.** `POST /committees/{id}/messages` accepts `{"type": "free" | "late" |
  "accept" | "reject"}`, stored as a `status` message with that quick action in its
  payload and a standard text if no body is sent. The committee side asks (`free`, `late`:
  the `oc` role, or a committee's own rapporteur) and hospitality answers (`accept`,
  `reject`: a member of a team with unscoped `chat.post`). Admins and heads can do both.
  Anyone else gets 403, including when the same action is sent as a plain `status`
  message or over the WebSocket. Messages carry the aliases `sender`
  (the sender's email) and `type`, next to the upstream fields. The eight committees
  (UNSC, CCC, PSC, WTO, UNODC, UNICEF, ECOSOC, IPC) are seeded into the first event.
- **Roles.** `eb` (executive board) is a fourth role, assignable with
  `PATCH /admin/users/{email}/role`.

## Roles, teams and permissions

Beyond the `role` column (`delegate`, `oc`, `admin`), the Organizing Committee has a
permission model: teams carry named permissions as data, memberships grant a person a
team's permissions for an event, and heads hold everything. See
`docs/adr/0003-oc-teams-permissions-and-memberships.md`.

## Authentication & Security
 - Uses JWT with a secret key. Login tokens expire after 12 hours (`ACCESS_TOKEN_EXPIRE_MINUTES`).
 - Passwords are hashed with bcrypt.
 - Many routes are protected by Depends(get_current_user) to verify tokens.
 - Every user has a role (`delegate`, `oc` or `admin`). It, and their OC permissions, are
   read from the database on each request, not from the token, so a demotion or a lapsed
   membership applies immediately.
 - Admin endpoints use `Depends(require_admin)`; OC feature routes use
   `Depends(require_permission(...))`.

## Database Interactions
 - PostgreSQL 16 with async SQLAlchemy 2.0 (asyncpg); the schema is managed by Alembic.
 - database.py has async functions to add, get, update, and delete user/delegate data.
 - A Mumbai MUN delegate is a delegate plus a row in `mm_delegates` (country, committee,
   food preference), joined on the delegate id. Meals collected are rows in `meal_scans`.
 - Past MUN experience is stored one row per entry in `mun_experiences`.
 - Backups are `pg_dump` files (see Backups above).

## API Usage
 - Send requests with Authorization: Bearer <token> to protected endpoints.
 - For CSV output, add ?format=csv to relevant endpoints.
 - JSON responses generally follow the pydantic models from models.py.

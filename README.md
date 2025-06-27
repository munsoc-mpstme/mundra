# MUNDRA - MUNSoc Delegate Resource Application (1.0.0)

### Named after Mundra Port, Kutch, Gujarat, MUNDRA - MUNSoc Delegate Resource Application is a centralized database designed to optimize event planning, streamline communication, and facilitate delegate management

### Deployment environments

| Environment | Branch | Documentation URL                  |
| ----------- | ------ | ---------------------------------- |
| PROD        | main   | https://mundra.munsocietympstme.com/docs |

This backend is used by the Delego app available at
[AppStore](https://apps.apple.com/no/app/delego-mumbai-mun-2024/id1661612842) and will be available at PlayStore soon.

## Setup

1. Clone the repository:

```bash
git clone https://github.com/munsoc-mpstme/mundra
cd mundra
```

2. Create a virtual environment called `env`

```bash
python -m venv env
```

3. Activate the virtual environment

```bash
source env/bin/activate
```

4. Install the dependencies

```bash
pip install -r requirements.txt
```

5. Run the development server

```bash
fastapi dev app.py
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
 - **database.py**: Interacts with the SQLite databases (main and mm).
 - **models.py**: Defines pydantic models for Admin, Delegate, User, etc.
 - **mails.py**: Sends email via FastMail (for verification and password reset).
 - **templates/**: HTML templates for pages like password reset, food selection, and QR scanning.
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

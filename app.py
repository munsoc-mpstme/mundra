import asyncio
import contextlib
import csv
import logging
from contextlib import asynccontextmanager
from functools import lru_cache
from io import StringIO
import os
from typing import Annotated
import time
import uuid
import json

from fastapi import (
    APIRouter,
    Body,
    Depends,
    FastAPI,
    Form,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from auth import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    oauth2_scheme,
    create_access_token,
    create_reset_token,
    verify_reset_token,
    generate_verification_code,
    get_current_user,
    hash_password,
    require_admin,
    require_any_oc,
    require_head,
    require_permission,
    verify_password,
)
from sqlalchemy.exc import IntegrityError
import chat
import floor
import config
import database
import db
import mails
import models
import permissions
import utils

from datetime import datetime, timedelta, timezone

####################

# Initialization

limiter = Limiter(key_func=get_remote_address)

settings = config.get_settings()

async def bootstrap_admin() -> None:
    """If ADMIN_EMAIL is set, make that account an admin (see config.py). A problem here is
    logged and never stops the server from starting. The password is never logged."""
    log = logging.getLogger("uvicorn.error")
    email = (settings.admin_email or "").strip()
    if not email:
        return
    try:
        password_hash = None
        if settings.admin_password:
            if len(settings.admin_password) < 8:
                log.error("ADMIN_PASSWORD must be at least 8 characters; ignoring it.")
            else:
                password_hash = await asyncio.to_thread(hash_password, settings.admin_password)
        outcome = await database.ensure_bootstrap_admin(email, password_hash)
        if outcome == "missing":
            log.warning(
                "ADMIN_EMAIL %s has no account yet. Register it in the app, or also set "
                "ADMIN_PASSWORD so it is created on the next start.",
                email,
            )
        else:
            log.info("Bootstrap admin %s: %s", email, outcome)
    except Exception:
        log.exception("Could not set up the bootstrap admin %s", email)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await bootstrap_admin()
    await floor.hub.restore()
    yield
    await floor.hub.shutdown()
    await db.engine.dispose()


app = FastAPI(
    lifespan=lifespan,
    title="MUNDRA - MUNSoc Delegate Resource Application",
    description="Named after Mundra Port, Kutch, Gujarat, MUNDRA - MUNSoc Delegate Resource Application is a centralized database designed to optimize event planning, streamline communication, and facilitate delegate management",
    version="1.0.0",
    docs_url=settings.docs_url,
    redoc_url=settings.redoc_url,
)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.get("/", tags=["Status"])
def status():
    return {"message": "Server is up and running"}


####################

# Auth


@app.post(
    "/register",
    tags=["Auth"],
    status_code=201,
    responses={
        429: {"model": models.ErrorResponse},
        409: {"model": models.ErrorResponse},
        500: {"model": models.ErrorResponse},
    },
)
@limiter.limit("10/minute")
async def register(request: Request, user: models.User):
    try:
        user.password = await asyncio.to_thread(hash_password, user.password)

        user_exists = await database.get_user_by_email(user.email)
        if user_exists:
            raise HTTPException(status_code=409, detail="User already exists")

        delegate = await database.get_delegate_by_email(user.email)
        if not delegate:
            uid = str(uuid.uuid4()).replace("-", "")
            delegate = await database.add_delegate(
                models.Delegate(
                    id=uid,
                    firstname=user.firstname,
                    lastname=user.lastname,
                    email=user.email,
                    backup_email=user.backup_email,
                    contact=user.contact,
                    dateofbirth=user.dateofbirth,
                )
            )
        await database.add_user(user)

        try:
            await send_verification_code(delegate)
            return JSONResponse(
                status_code=201,
                content={
                    "message": "User created successfully. Please verify your email."
                },
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post(
    "/login",
    tags=["Auth"],
    response_model=models.Token,
    responses={
        401: {"model": models.ErrorResponse},
        403: {"model": models.ErrorResponse},
        500: {"model": models.ErrorResponse},
    },
)
@limiter.limit("10/minute")
async def login(request: Request, form_data: OAuth2PasswordRequestForm = Depends()):
    email = form_data.username
    password = form_data.password

    user = await database.get_user_by_email(email)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid email")
    if not await asyncio.to_thread(verify_password, password, user.password):
        raise HTTPException(status_code=401, detail="Invalid password")
    # No token until the email is verified: the app sends them to the code screen instead.
    # (Checked after the password so this does not reveal which emails are registered.)
    account = await database.get_auth_user(user.email)
    if account is not None and not account.verified:
        raise HTTPException(status_code=403, detail="Please verify your email!")

    # user_type keeps the two values the app already understands; "oc" reports as "user".
    user_type = "admin" if await database.get_role(user.email) == "admin" else "user"
    access_token = create_access_token(
        data={"sub": user.email, "type": user_type},
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    return models.Token(access_token=access_token, token_type="bearer", user_type=user_type)


async def send_verification_code(delegate: models.Delegate) -> None:
    """Generate a fresh 6-digit code, store it, and email it to the delegate (and their
    backup email, if set). Used by register, mumbaimun/register and resend."""
    code = generate_verification_code()
    expires_at = datetime.now(timezone.utc) + timedelta(
        minutes=settings.verification_code_expire_minutes
    )
    await database.set_verification_code(delegate.email, code, expires_at)
    if settings.mail_server in ("localhost", "127.0.0.1"):
        # Local development has no mail server to deliver the email, so show the code here.
        logging.getLogger("uvicorn.error").warning(
            "DEV ONLY (MAIL_SERVER is localhost): verification code for %s is %s",
            delegate.email,
            code,
        )
    await mails.send_verification_email(delegate, code)


async def _send_code_if_unverified(delegate: models.Delegate) -> bool:
    """Email a fresh code to a delegate who still has to verify. Returns whether one was
    sent; a mail failure is logged and reported, not raised, so sign-up still succeeds."""
    if delegate.verified:
        return False
    try:
        await send_verification_code(delegate)
        return True
    except Exception:
        logging.getLogger("uvicorn.error").exception(
            "Could not send the verification code to %s", delegate.email
        )
        return False


@app.post(
    "/verify_email",
    tags=["Auth"],
    status_code=200,
    responses={
        400: {"model": models.ErrorResponse},
        404: {"model": models.ErrorResponse},
        429: {"model": models.ErrorResponse},
    },
)
@limiter.limit("10/minute")
async def verify_email(request: Request, body: models.VerifyEmail):
    """Verify an email with the 6-digit code that was sent to it."""
    status = await database.check_verification_code(
        body.email, body.code, settings.verification_code_max_attempts
    )
    if status == "ok":
        await database.verify_delegate_email(body.email)
        # A rostered OC member becomes a team member the moment they finish signing up.
        await database.apply_pending_invites(body.email)
        return JSONResponse(status_code=200, content={"message": "Email verified!"})
    if status == "none":
        raise HTTPException(status_code=404, detail="No verification pending for this email")
    if status == "expired":
        raise HTTPException(status_code=400, detail="Code expired, request a new one")
    if status == "too_many":
        raise HTTPException(status_code=429, detail="Too many attempts, request a new code")
    raise HTTPException(status_code=400, detail="Invalid code")


@app.get(
    "/resend_verification",
    tags=["Auth"],
    responses={
        404: {"model": models.ErrorResponse},
        409: {"model": models.ErrorResponse},
        500: {"model": models.ErrorResponse},
    },
)
@limiter.limit("10/minute")
async def resend_verification_email(request: Request, email: models.EmailStr):
    try:
        delegate = await database.get_delegate_by_email(email)
        if not delegate:
            raise HTTPException(status_code=404, detail="Delegate not found")
        if delegate.verified:
            raise HTTPException(status_code=409, detail="Email already verified")
        await send_verification_code(delegate)
        return JSONResponse(
            status_code=200, content={"message": "Verification code sent!"}
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get(
    "/forgot_password",
    tags=["Auth"],
    status_code=200,
    responses={
        403: {"model": models.ErrorResponse},
        404: {"model": models.ErrorResponse},
        500: {"model": models.ErrorResponse},
    },
)
@limiter.limit("1/minute")
async def forgot_password(request: Request, email: models.EmailStr):
    try:
        delegate = await database.get_delegate_by_email(email)
        if not delegate:
            raise HTTPException(status_code=404, detail="User not found")
        if not delegate.verified:
            raise HTTPException(status_code=403, detail="User not verified")
        user = await database.get_user_by_email(delegate.email)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        reset_token = create_reset_token(delegate.email, user.password)
        link = f"{settings.url.rstrip('/')}/reset?token={reset_token}"
        await mails.send_password_reset_email(delegate, link)
        return JSONResponse(
            status_code=200, content={"message": "Password reset email sent!"}
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.patch(
    "/change_pass",
    tags=["Auth"],
    responses={
        403: {"model": models.ErrorResponse},
        404: {"model": models.ErrorResponse},
        500: {"model": models.ErrorResponse},
    },
)
@limiter.limit("1/minute")
async def change_password(
    request: Request,
    password: str,
    delegate: models.AuthUser = Depends(get_current_user),
):
    try:
        user = await database.get_user_by_email(delegate.email)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        await database.change_user_pass(
            user.email, await asyncio.to_thread(hash_password, password)
        )
        return JSONResponse(status_code=200, content={"message": "Password changed!"})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


####################

# ADMIN STUFF


@app.get(
    "/backup",
    tags=["Admin"],
    response_class=FileResponse,
    responses={
        403: {"model": models.ErrorResponse},
        500: {"model": models.ErrorResponse},
    },
)
async def backup_database(user: models.AuthUser = Depends(require_admin)):
    """Runs pg_dump now and returns the dump (restore it with pg_restore)."""
    try:
        path = await database.backup_database()
        return FileResponse(
            path, media_type="application/octet-stream", filename=os.path.basename(path)
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.patch(
    "/admin/users/{email}/role",
    tags=["Admin"],
    responses={
        403: {"model": models.ErrorResponse},
        404: {"model": models.ErrorResponse},
    },
)
async def change_role(
    email: models.EmailStr,
    change: models.RoleChange,
    admin: models.AuthUser = Depends(require_admin),
):
    """Give a user the delegate, eb, oc or admin role. Every change is written to the
    admin_audit table."""
    try:
        old_role = await database.set_role(admin.email, email, change.role)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    return {"email": email, "old_role": old_role, "new_role": change.role}


@app.get(
    "/delegates",
    tags=["Admin"],
    response_model=list[models.Delegate],
    responses={
        403: {"model": models.ErrorResponse},
        404: {"model": models.ErrorResponse},
        500: {"model": models.ErrorResponse},
    },
)
async def get_delegates(token: str = "", format: str = ""):
    try:
        user = await get_current_user(token)
        if user.role != "admin":
            raise HTTPException(status_code=403, detail="Forbidden")
        data = await database.get_delegates()
        if data:
            if format == "csv":
                output = StringIO()
                writer = csv.writer(output)
                writer.writerow(
                    [
                        "id",
                        "firstname",
                        "lastname",
                        "email",
                        "contact",
                        "dateofbirth",
                        "gender",
                        "pastmuns",
                    ]
                )

                for delegate in data:
                    past_muns_info = []
                    for mun in delegate.pastmuns:
                        past_muns_info.append(
                            f"{mun.name} | {mun.committee} | {mun.delegation} | {mun.year} | {mun.award}"
                        )

                    writer.writerow(
                        [
                            delegate.id,
                            delegate.firstname,
                            delegate.lastname,
                            delegate.email,
                            delegate.contact,
                            delegate.dateofbirth,
                            delegate.gender,
                            " ; ".join(past_muns_info),
                        ]
                    )

                csv_data = output.getvalue()
                output.close()

                return Response(content=csv_data, media_type="text/csv")
            return data
        raise HTTPException(status_code=404, detail="No delegates found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


####################

# DELEGATE STUFF


@app.get(
    "/delegates/me",
    tags=["Delegates"],
    response_model=models.Me,
    responses={500: {"model": models.ErrorResponse}},
)
async def get_current_delegate(user: models.AuthUser = Depends(get_current_user)):
    # is_head, permissions and teams are additive over the old Delegate response, so an
    # app install that does not read them keeps working (ADR 0003).
    is_head, perms, memberships = await database.get_effective_access(user.email)
    mm = await database.get_mm_delegate_by_id(user.id)
    return models.Me(
        **user.model_dump(),
        is_head=is_head,
        # OC team permissions as before, plus the strings the Delego app gates its screens on
        # (derived from the role and the team permissions, see permissions.py).
        permissions=sorted(
            set(perms)
            | permissions.app_permissions(user.role, is_head, perms, memberships)
        ),
        teams=memberships,
        food_preference=mm.food_preference if mm else None,
    )


@app.get(
    "/delegates/{id}",
    tags=["Delegates"],
    response_model=models.Delegate,
    responses={
        403: {"model": models.ErrorResponse},
        404: {"model": models.ErrorResponse},
        500: {"model": models.ErrorResponse},
    },
)
async def get_delegate_by_id(
    id: str, user: models.AuthUser = Depends(get_current_user)
):
    try:
        if user.role != "admin":
            if user.id == id:
                return user
            raise HTTPException(status_code=403, detail="Forbidden")
        data = await database.get_delegate_by_id(id)
        if data:
            return data
        raise HTTPException(status_code=404, detail="Delegate not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.patch(
    "/delegates/{id}",
    tags=["Delegates"],
    response_model=models.Delegate,
    responses={
        403: {"model": models.ErrorResponse},
        404: {"model": models.ErrorResponse},
        500: {"model": models.ErrorResponse},
    },
)
async def update_delegate(
    id: str,
    user: models.AuthUser = Depends(get_current_user),
    firstname: str = "",
    lastname: str = "",
    backup_email: str = "",
    contact: str = "",
    dateofbirth: str = "",
    gender: str = "",
    pastmuns: list[models.MunExperience] = [],
    verified: bool = False,
):
    try:
        if user.role != "admin" and user.id != id:
            raise HTTPException(status_code=403, detail="Forbidden")
        data = await database.get_delegate_by_id(id)
        if not data:
            raise HTTPException(status_code=404, detail="Delegate not found")
        if firstname != "":
            data.firstname = firstname
        if lastname != "":
            data.lastname = lastname
        if backup_email != "":
            data.backup_email = backup_email
        if contact != "":
            data.contact = contact
        if dateofbirth != "":
            data.dateofbirth = dateofbirth
        if gender != "":
            data.gender = gender
        if pastmuns != []:
            data.pastmuns = pastmuns
        if verified:
            data.verified = verified
        return await database.update_delegate_by_id(id, data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


####################

# MUMBAIMUN QR CODES


@app.get("/qr", tags=["QR"], responses={404: {"model": models.ErrorResponse}})
async def get_qr(id: str):
    # The id becomes part of a file path, so only a real delegate's id is accepted.
    # Anything else (including ../ tricks) is a plain 404.
    if await database.get_delegate_by_id(id) is None:
        raise HTTPException(status_code=404, detail="Delegate not found")
    try:
        qr_folder = utils.qr_folder
        if not os.path.exists(qr_folder):
            os.makedirs(qr_folder)

        qr_image = f"{qr_folder}/{id}.jpg"

        if not os.path.exists(qr_image):
            await asyncio.to_thread(utils.generate_qr, id)
        try:
            return FileResponse(qr_image)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# REGISTER STUFF

mm_router = APIRouter(prefix="/mumbaimun", tags=["Mumbai MUN"])


@mm_router.post(
    "/register",
    tags=["Auth"],
    status_code=201,
    responses={
        400: {"model": models.ErrorResponse},
        500: {"model": models.ErrorResponse},
    },
)
async def mm_register(request: Request, user: models.User):
    try:
        user.password = await asyncio.to_thread(hash_password, user.password)
        user_exists = await database.get_user_by_email(user.email)

        if user_exists:
            delegate = await database.get_delegate_by_email(user.email)

            if not delegate:
                raise HTTPException(
                    status_code=400, detail="User exists but is not a delegate."
                )
            mm_delegate = await database.get_mm_delegate_by_email(user.email)

            if not delegate.verified:
                # Nobody has proven they own this email yet, so this is the same person
                # trying again (they left the code screen, or the mail never came). The
                # password they just typed replaces the first one, otherwise logging in
                # with it fails as "Invalid password"; they get a fresh code.
                await database.change_user_pass(user.email, user.password)

            if mm_delegate:
                if delegate.verified:
                    raise HTTPException(
                        status_code=409,
                        detail=f"Mumbai MUN Delegate already registered! ID: {mm_delegate.id}",
                    )
                email_sent = await _send_code_if_unverified(delegate)
                return JSONResponse(
                    status_code=201,
                    content={
                        "message": f"Mumbai MUN Delegate registered successfully! ID: {mm_delegate.id}",
                        "verified": False,
                        "email_sent": email_sent,
                    },
                )

            if user.contact or user.dateofbirth:
                if user.contact:
                    delegate.contact = user.contact
                if user.dateofbirth:
                    delegate.dateofbirth = user.dateofbirth
                delegate = await database.update_delegate_by_id(delegate.id, delegate)

            mm_delegate = await database.add_mm_delegate(
                models.MMDelegate(
                    id=delegate.id,
                    firstname=delegate.firstname,
                    lastname=delegate.lastname,
                    email=delegate.email,
                    contact=delegate.contact,
                    dateofbirth=delegate.dateofbirth,
                    gender=delegate.gender,
                    pastmuns=delegate.pastmuns,
                    verified=delegate.verified,
                    food_preference=user.food_preference,
                )
            )

            email_sent = await _send_code_if_unverified(delegate)
            return JSONResponse(
                status_code=201,
                content={
                    "message": f"Mumbai MUN Delegate registered successfully! ID: {mm_delegate.id}",
                    "verified": delegate.verified,
                    "email_sent": email_sent,
                },
            )

        else:

            delegate = await database.get_delegate_by_email(user.email)
            if not delegate:
                uid = str(uuid.uuid4()).replace("-", "")
                delegate = await database.add_delegate(
                    models.Delegate(
                        id=uid,
                        firstname=user.firstname,
                        lastname=user.lastname,
                        email=user.email,
                        backup_email=user.backup_email,
                        contact=user.contact,
                        dateofbirth=user.dateofbirth,
                        verified=False,  # becomes True only when they enter the emailed code
                    )
                )
            elif user.contact or user.dateofbirth:
                if user.contact:
                    delegate.contact = user.contact
                if user.dateofbirth:
                    delegate.dateofbirth = user.dateofbirth
                delegate = await database.update_delegate_by_id(delegate.id, delegate)

            await database.add_user(user)

            mm_delegate = await database.add_mm_delegate(
                models.MMDelegate(
                    id=delegate.id,
                    firstname=delegate.firstname,
                    lastname=delegate.lastname,
                    email=delegate.email,
                    contact=delegate.contact,
                    dateofbirth=delegate.dateofbirth,
                    gender=delegate.gender,
                    pastmuns=delegate.pastmuns,
                    verified=delegate.verified,
                    food_preference=user.food_preference,
                )
            )

            # The account exists but cannot log in until the code is entered. If the email
            # could not be sent the account is still created; the app offers "Resend code".
            email_sent = await _send_code_if_unverified(delegate)
            return JSONResponse(
                status_code=201,
                content={
                    "message": f"User with id {delegate.id} created successfully!",
                    "verified": delegate.verified,
                    "email_sent": email_sent,
                },
            )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@mm_router.get(
    "/delegates",
    tags=["Admin"],
    response_model=list[models.MMDelegate],
    responses={
        403: {"model": models.ErrorResponse},
        404: {"model": models.ErrorResponse},
        500: {"model": models.ErrorResponse},
    },
)
async def get_mm_delegates(
    user: models.AuthUser = Depends(require_admin), format: str = ""
):
    try:
        data = await database.get_mm_delegates()
        if data:
            if format == "csv":
                output = StringIO()
                writer = csv.writer(output)
                writer.writerow(
                    [
                        "id",
                        "firstname",
                        "lastname",
                        "email",
                        "contact",
                        "dateofbirth",
                        "gender",
                        "pastmuns",
                    ]
                )

                for delegate in data:
                    past_muns_info = []
                    for mun in delegate.pastmuns:
                        past_muns_info.append(
                            f"{mun.name} | {mun.committee} | {mun.delegation} | {mun.year} | {mun.award}"
                        )

                    writer.writerow(
                        [
                            delegate.id,
                            delegate.firstname,
                            delegate.lastname,
                            delegate.email,
                            delegate.contact,
                            delegate.dateofbirth,
                            delegate.gender,
                            " ; ".join(past_muns_info),
                        ]
                    )

                csv_data = output.getvalue()
                output.close()

                return Response(content=csv_data, media_type="text/csv")
            return data
        raise HTTPException(status_code=404, detail="No delegates found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


app.include_router(mm_router)

#####################################
# FOOD: preference and meal scanning (docs/adr/0003)
#####################################

# Whoever runs the food counter: scans, plate counts and the flagged list.
require_food = require_permission(permissions.FOOD_MANAGE_ENTITLEMENT)
# Scanning meals and reading plate counts: any OC member, as well as teams/heads with the
# food permission. (The flagged-scan list above stays team/head only.)
require_food_scan = require_permission(
    permissions.FOOD_MANAGE_ENTITLEMENT, roles=permissions.OC_BASELINE_ROLES
)

# The app (and older clients) call the third meal "high_tea"; the database says "hitea".
_MEAL_ALIASES = {"high_tea": "hitea", "high-tea": "hitea", "hightea": "hitea"}
_DIET_ALIASES = {"nonveg": "non_veg", "non-veg": "non_veg"}


def _parse_meal(raw: str) -> str:
    meal = _MEAL_ALIASES.get(raw.strip().lower(), raw.strip().lower())
    if meal not in db.MEALS:
        raise HTTPException(
            status_code=422, detail="meal must be breakfast, lunch or high_tea"
        )
    return meal


def _parse_diet(raw: str) -> str | None:
    """The diet the operator picked. Blank means "use the delegate's registered one"."""
    diet = raw.strip().lower()
    if not diet:
        return None
    diet = _DIET_ALIASES.get(diet, diet)
    if diet not in ("veg", "non_veg", "jain"):
        raise HTTPException(status_code=422, detail="diet must be veg, non_veg or jain")
    return diet


# How far back a phone's own scan time is believed: the app keeps unsent scans for the
# three days of the conference. A little clock drift into the future is allowed.
_SCAN_TIME_MAX_AGE = timedelta(hours=72)
_SCAN_TIME_MAX_AHEAD = timedelta(minutes=5)


def _parse_scanned_at(raw: str) -> datetime | None:
    """When the phone scanned the badge, so a scan saved offline and uploaded the next
    morning still counts for the day it was made. None (use the server's clock) when it
    is missing, unreadable, has no offset, or is outside the window above. Never an
    error: a bad value must not cost a delegate their plate."""
    try:
        scanned = datetime.fromisoformat(raw.strip())
    except ValueError:
        return None
    if scanned.tzinfo is None:
        return None
    now = datetime.now(timezone.utc)
    if not now - _SCAN_TIME_MAX_AGE <= scanned <= now + _SCAN_TIME_MAX_AHEAD:
        return None
    return scanned


@app.get(
    "/mumbaimun/delegates/me",
    tags=["Food"],
    response_model=models.MMDelegate,
    responses={404: {"model": models.ErrorResponse}},
)
async def get_my_mm_delegate(user: models.AuthUser = Depends(get_current_user)):
    """The caller's own Mumbai MUN details (name, preference), for the QR screen."""
    mm = await database.get_mm_delegate_by_id(user.id)
    if not mm:
        raise HTTPException(status_code=404, detail="Not registered for Mumbai MUN")
    return mm


@app.get(
    "/mumbaimun/badge",
    tags=["Food"],
    response_model=models.Badge,
    responses={404: {"model": models.ErrorResponse}},
)
async def get_my_badge(user: models.AuthUser = Depends(get_current_user)):
    """The caller's food badge: the string to show as a QR, which carries their id and
    diet so a scanner can show the diet even offline. Re-fetch it after the diet changes."""
    mm = await database.get_mm_delegate_by_id(user.id)
    if not mm:
        raise HTTPException(status_code=404, detail="Not registered for Mumbai MUN")
    return models.Badge(
        qr=utils.badge_payload(mm.id, mm.food_preference),
        delegate_id=mm.id,
        name=f"{mm.firstname} {mm.lastname}",
        food_preference=mm.food_preference,
    )


@app.get(
    "/mumbaimun/badge/qr",
    tags=["Food"],
    response_class=Response,
    responses={200: {"content": {"image/png": {}}}, 404: {"model": models.ErrorResponse}},
)
async def get_my_badge_qr(user: models.AuthUser = Depends(get_current_user)):
    """The caller's food badge as a PNG QR code (the same string as /mumbaimun/badge)."""
    badge = await get_my_badge(user)
    png = await asyncio.to_thread(utils.qr_png, badge.qr)
    return Response(content=png, media_type="image/png", headers={"Cache-Control": "no-store"})


@app.patch(
    "/mumbaimun/delegates/{id}/food_preference",
    tags=["Food"],
    response_model=models.MMDelegate,
    responses={
        403: {"model": models.ErrorResponse},
        404: {"model": models.ErrorResponse},
    },
)
async def set_food_preference(
    id: str,
    change: models.FoodPreferenceChange,
    user: models.AuthUser = Depends(get_current_user),
):
    """A delegate sets their own diet, or hospitality (food.manage_entitlement) overrides
    it on the day. A head or admin may also set it."""
    if user.id != id and user.role != "admin":
        is_head, perms, _ = await database.get_effective_access(user.email)
        if not is_head and permissions.FOOD_MANAGE_ENTITLEMENT not in perms:
            raise HTTPException(status_code=403, detail="Forbidden")
    updated = await database.set_food_preference(id, change)
    if not updated:
        raise HTTPException(status_code=404, detail="Not a Mumbai MUN delegate")
    return updated


@app.post(
    "/food/scans",
    tags=["Food"],
    response_model=models.ScanResult,
    responses={
        400: {"model": models.ErrorResponse},
        403: {"model": models.ErrorResponse},
        404: {"model": models.ErrorResponse},
    },
)
async def scan_meal(
    delegate_id: Annotated[str, Form()],
    meal: Annotated[str, Form()] = "",
    diet: Annotated[str, Form()] = "",
    scanned_at: Annotated[str, Form()] = "",  # when the phone scanned it (see _parse_scanned_at)
    user: models.AuthUser = Depends(require_food_scan),
):
    """Record a delegate collecting a meal. `delegate_id` is what the QR holds: a badge
    code (DELEGO1:...) or a plain id. The day comes from the date of the scan and, when
    `meal` is left blank, the meal from its time (see GET /food/context), so the operator
    picks neither. `diet` optionally records what was actually served. Returns `served`,
    or `duplicate` (with a 200) when they already collected this meal, which is logged to
    the flagged list."""
    delegate_id, _ = utils.parse_badge(delegate_id)
    scanned = _parse_scanned_at(scanned_at)
    meal = _parse_meal(meal) if meal.strip() else database.current_meal(scanned)
    diet = _parse_diet(diet)
    try:
        event_id, day = await database.resolve_scan_day(now=scanned)
    except LookupError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        return await database.record_meal_scan(
            event_id=event_id,
            day=day,
            meal=meal,
            delegate_id=delegate_id,
            scanned_by=user.email,
            diet=diet,
        )
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get(
    "/food/context",
    tags=["Food"],
    response_model=models.ScanContext,
    responses={403: {"model": models.ErrorResponse}},
)
async def scan_context(user: models.AuthUser = Depends(require_food_scan)):
    """Today's event day and the meal being served now, so the scanner shows them instead
    of asking. Also returns the event's dates and the meal windows: cache them, and the
    scanner can work out the day and meal itself while offline."""
    return await database.scan_context()


@app.get(
    "/food/lookup",
    tags=["Food"],
    response_model=models.BadgeLookup,
    responses={
        400: {"model": models.ErrorResponse},
        403: {"model": models.ErrorResponse},
        404: {"model": models.ErrorResponse},
    },
)
async def lookup_badge(
    code: str, meal: str = "", user: models.AuthUser = Depends(require_food_scan)
):
    """Who a scanned badge (or delegate id) belongs to, their registered diet and
    allergy notes, and whether they already had the current meal today. Records nothing:
    serve the plate with POST /food/scans."""
    delegate_id, _ = utils.parse_badge(code)
    mm = await database.get_mm_delegate_by_id(delegate_id)
    if not mm:
        raise HTTPException(status_code=404, detail="Not a Mumbai MUN delegate")
    meal = _parse_meal(meal) if meal.strip() else database.current_meal()
    try:
        event_id, day = await database.resolve_scan_day()
    except LookupError as e:
        raise HTTPException(status_code=400, detail=str(e))
    collected = await database.meals_collected(event_id, day, delegate_id)
    return models.BadgeLookup(
        delegate_id=mm.id,
        name=f"{mm.firstname} {mm.lastname}",
        food_preference=mm.food_preference,
        food_notes=mm.food_notes,
        committee=mm.committee,
        day=day,
        meal=meal,
        collected_today=collected,
        already_collected=meal in collected,
    )


@app.get(
    "/food/delegates",
    tags=["Food"],
    response_model=list[models.DelegateMatch],
    responses={403: {"model": models.ErrorResponse}, 422: {"model": models.ErrorResponse}},
)
async def search_delegates_for_food(
    q: Annotated[str, Query(min_length=2, max_length=100)],
    user: models.AuthUser = Depends(require_food_scan),
):
    """Find a Mumbai MUN delegate by name or email, for someone who cannot show their QR
    (no phone, flat battery). Serve them with POST /food/scans using the delegate_id."""
    return await database.search_mm_delegates(q.strip())


@app.get(
    "/food/plate_count",
    tags=["Food"],
    response_model=models.MealCount,
    responses={400: {"model": models.ErrorResponse}, 403: {"model": models.ErrorResponse}},
)
async def plate_count(meal: str, user: models.AuthUser = Depends(require_food_scan)):
    """The live count of plates served for a meal today, broken down by diet."""
    meal = _parse_meal(meal)
    try:
        event_id, day = await database.resolve_scan_day()
    except LookupError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return await database.get_plate_count(event_id, day, meal)


@app.get(
    "/food/flags",
    tags=["Food"],
    response_model=list[models.FlaggedScan],
    responses={400: {"model": models.ErrorResponse}, 403: {"model": models.ErrorResponse}},
)
async def flagged_scans(user: models.AuthUser = Depends(require_food)):
    """Every rejected second-scan for the event scans are filed under: who tried for
    seconds. Works outside the event's dates too, e.g. to review the list afterwards."""
    try:
        event_id, _ = await database.resolve_scan_day()
    except LookupError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return await database.get_flagged_scans(event_id)


#####################################
# OC ADMINISTRATION: events, teams, rosters, heads (docs/adr/0003)
#####################################

require_team_admin = require_permission(permissions.TEAM_MANAGE_DEFINITION)


async def _authorize_roster(user: models.AuthUser, team_id: int) -> None:
    """Roster edits: the team's own lead, or a head/admin. Anyone else is forbidden."""
    if user.role == "admin":
        return
    is_head, _, _ = await database.get_effective_access(user.email)
    if is_head or await database.is_team_lead(user.email, team_id):
        return
    raise HTTPException(status_code=403, detail="Forbidden")


@app.get(
    "/events",
    tags=["OC Admin"],
    response_model=list[models.Event],
    responses={403: {"model": models.ErrorResponse}},
)
async def list_events(user: models.AuthUser = Depends(require_head)):
    """The events, oldest first (head/admin). The Delego app reads the event id from here
    to create and manage the Hospitality team."""
    return await database.list_events()


@app.patch(
    "/events/{event_id}",
    tags=["OC Admin"],
    response_model=models.Event,
    responses={403: {"model": models.ErrorResponse}, 404: {"model": models.ErrorResponse}},
)
async def set_event_dates(
    event_id: int,
    dates: models.EventDates,
    user: models.AuthUser = Depends(require_head),
):
    """Set an event's start and end dates, which meal scanning derives the day from."""
    event = await database.set_event_dates(event_id, dates)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return event


@app.get(
    "/events/{event_id}/teams",
    tags=["OC Admin"],
    response_model=list[models.Team],
    responses={403: {"model": models.ErrorResponse}},
)
async def list_teams(event_id: int, user: models.AuthUser = Depends(require_any_oc)):
    return await database.list_teams(event_id)


@app.post(
    "/events/{event_id}/teams",
    tags=["OC Admin"],
    status_code=201,
    response_model=models.Team,
    responses={
        403: {"model": models.ErrorResponse},
        409: {"model": models.ErrorResponse},
        422: {"model": models.ErrorResponse},
    },
)
async def create_team(
    event_id: int,
    new_team: models.NewTeam,
    user: models.AuthUser = Depends(require_team_admin),
):
    try:
        return await database.create_team(event_id, new_team)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except IntegrityError:
        raise HTTPException(status_code=409, detail="A team with that name already exists")


@app.patch(
    "/teams/{team_id}/permissions",
    tags=["OC Admin"],
    response_model=models.Team,
    responses={
        403: {"model": models.ErrorResponse},
        404: {"model": models.ErrorResponse},
        422: {"model": models.ErrorResponse},
    },
)
async def set_team_permissions(
    team_id: int,
    change: models.TeamPermissionsChange,
    user: models.AuthUser = Depends(require_team_admin),
):
    try:
        team = await database.set_team_permissions(team_id, change)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    return team


@app.get(
    "/teams/{team_id}/members",
    tags=["OC Admin"],
    response_model=list[models.RosterEntry],
    responses={403: {"model": models.ErrorResponse}},
)
async def list_team_members(
    team_id: int, user: models.AuthUser = Depends(get_current_user)
):
    await _authorize_roster(user, team_id)
    return await database.list_team_members(team_id)


@app.post(
    "/teams/{team_id}/members",
    tags=["OC Admin"],
    status_code=201,
    responses={403: {"model": models.ErrorResponse}, 404: {"model": models.ErrorResponse}},
)
async def add_team_member(
    team_id: int,
    add: models.RosterAdd,
    user: models.AuthUser = Depends(get_current_user),
):
    """Add someone to a team's roster. If they already have an account they become a
    member now; otherwise they are invited and join automatically when they verify."""
    await _authorize_roster(user, team_id)
    try:
        status = await database.add_to_roster(user.email, team_id, add)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"email": add.email, "status": status}


@app.delete(
    "/teams/{team_id}/members/{email}",
    tags=["OC Admin"],
    responses={403: {"model": models.ErrorResponse}, 404: {"model": models.ErrorResponse}},
)
async def remove_team_member(
    team_id: int,
    email: models.EmailStr,
    user: models.AuthUser = Depends(get_current_user),
):
    await _authorize_roster(user, team_id)
    try:
        removed = await database.remove_from_roster(user.email, team_id, email)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    if not removed:
        raise HTTPException(status_code=404, detail="Not on this team's roster")
    return {"email": email, "status": "removed"}


@app.get(
    "/events/{event_id}/heads",
    tags=["OC Admin"],
    response_model=list[str],
    responses={403: {"model": models.ErrorResponse}},
)
async def list_heads(event_id: int, user: models.AuthUser = Depends(require_head)):
    return await database.list_heads(event_id)


@app.post(
    "/events/{event_id}/heads",
    tags=["OC Admin"],
    status_code=201,
    responses={
        403: {"model": models.ErrorResponse},
        404: {"model": models.ErrorResponse},
        409: {"model": models.ErrorResponse},
    },
)
async def add_head(
    event_id: int,
    head: models.HeadAdd,
    admin: models.AuthUser = Depends(require_admin),
):
    """Only an admin grants the head role (docs/adr/0003)."""
    try:
        added = await database.add_head(admin.email, event_id, head.email)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    if not added:
        raise HTTPException(status_code=409, detail="Already a head")
    return {"email": head.email, "status": "head"}


@app.delete(
    "/events/{event_id}/heads/{email}",
    tags=["OC Admin"],
    responses={403: {"model": models.ErrorResponse}, 404: {"model": models.ErrorResponse}},
)
async def remove_head(
    event_id: int,
    email: models.EmailStr,
    admin: models.AuthUser = Depends(require_admin),
):
    if not await database.remove_head(event_id, email):
        raise HTTPException(status_code=404, detail="Not a head")
    return {"email": email, "status": "removed"}


#####################################
# CHAT: committees, status and the hospitality<->rapporteur channel (docs/adr/0003)
#####################################


async def _committee_or_404(committee_id: int) -> models.Committee:
    committee = await database.get_committee(committee_id)
    if not committee:
        raise HTTPException(status_code=404, detail="Committee not found")
    return committee


# The text shown when the app sends a quick action without its own wording.
_BREAK_TEXT = {
    "free": "We are free for a break",
    "late": "Running 5 minutes late",
    "accept": "Accepted - come down now",
    "reject": "Rejected - canteen is full",
}


def _message_parts(message: models.NewChatMessage) -> tuple[str, str, dict | None]:
    """(kind, body, payload) to store. The app's {"type": "free"} shorthand becomes a
    status message carrying that quick action, with a standard text if none was given."""
    if message.type is None:
        return message.kind, message.body, message.payload
    body = message.body.strip() or _BREAK_TEXT[message.type]
    return "status", body, {**(message.payload or {}), "type": message.type}


def _break_action(message: models.NewChatMessage) -> str | None:
    """The break quick action a message carries, whether sent as {"type": "accept"} or as
    a status message with that type in its payload, so it cannot be smuggled past the
    check by using the generic form."""
    if message.type is not None:
        return message.type
    if message.kind == "status" and isinstance(message.payload, dict):
        action = message.payload.get("type")
        if action in permissions.BREAK_REQUEST_ACTIONS + permissions.BREAK_RESPONSE_ACTIONS:
            return action
    return None


async def _may_send(
    user: models.AuthUser, committee: models.Committee, message: models.NewChatMessage
) -> bool:
    """chat.post in this committee, and for a break quick action also the right side of
    the conversation (see permissions.can_use_break_action)."""
    if not await _committee_access(user, committee, permissions.CHAT_POST):
        return False
    action = _break_action(message)
    if action is None:
        return True
    is_head, _, memberships = await database.get_effective_access(user.email)
    return permissions.can_use_break_action(
        user.role, is_head, memberships, action, committee.name
    )


async def _committee_access(
    user: models.AuthUser, committee: models.Committee, permission: str
) -> bool:
    """Whether the user may use a committee-scoped permission on this committee: an admin
    or head always may; a rapporteur only on their own committee; hospitality on all."""
    if user.role == "admin":
        return True
    # The OC role is the baseline for break coordination (the Delego app): every OC member
    # can read and post in every committee. Team scoping below still applies to everyone
    # else, such as a delegate-role user who was given a rapporteur membership.
    if user.role in permissions.OC_BASELINE_ROLES and permission in (
        permissions.CHAT_VIEW,
        permissions.CHAT_POST,
    ):
        return True
    is_head, _, memberships = await database.get_effective_access(user.email)
    return permissions.can_act_on_committee(
        is_head, memberships, permission, committee.name
    )


@app.get(
    "/committees",
    tags=["Chat"],
    response_model=list[models.Committee],
    responses={403: {"model": models.ErrorResponse}},
)
async def list_my_committees(user: models.AuthUser = Depends(get_current_user)):
    """The committees whose chat the caller may read, across events, in creation order.
    This is the list the Delego app shows (it has no event id to ask with). 403 when the
    caller can read none, so the app can tell the user they have no access."""
    visible = [
        c
        for c in await database.list_all_committees()
        if await _committee_access(user, c, permissions.CHAT_VIEW)
    ]
    if not visible:
        raise HTTPException(status_code=403, detail="Forbidden")
    return visible


@app.post(
    "/events/{event_id}/committees",
    tags=["Chat"],
    status_code=201,
    response_model=models.Committee,
    responses={
        403: {"model": models.ErrorResponse},
        409: {"model": models.ErrorResponse},
        422: {"model": models.ErrorResponse},
    },
)
async def create_committee(
    event_id: int,
    new: models.NewCommittee,
    user: models.AuthUser = Depends(require_head),
):
    try:
        return await database.create_committee(event_id, new)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except IntegrityError:
        raise HTTPException(status_code=409, detail="A committee with that name already exists")


@app.get(
    "/events/{event_id}/committees",
    tags=["Chat"],
    response_model=list[models.Committee],
)
async def list_committees(event_id: int, user: models.AuthUser = Depends(get_current_user)):
    """Any authenticated user can read committee statuses, including a delegate checking
    whether their own committee has broken for a meal."""
    return await database.list_committees(event_id)


@app.patch(
    "/committees/{committee_id}/status",
    tags=["Chat"],
    response_model=models.Committee,
    responses={403: {"model": models.ErrorResponse}, 404: {"model": models.ErrorResponse}},
)
async def set_committee_status(
    committee_id: int,
    change: models.CommitteeStatusChange,
    user: models.AuthUser = Depends(get_current_user),
):
    committee = await _committee_or_404(committee_id)
    if not await _committee_access(
        user, committee, permissions.RAPPORTEUR_SET_COMMITTEE_STATUS
    ):
        raise HTTPException(status_code=403, detail="Forbidden")
    return await database.set_committee_status(committee_id, change.status)


@app.get(
    "/committees/{committee_id}/messages",
    tags=["Chat"],
    response_model=list[models.ChatMessage],
    responses={403: {"model": models.ErrorResponse}, 404: {"model": models.ErrorResponse}},
)
async def get_messages(
    committee_id: int, user: models.AuthUser = Depends(get_current_user)
):
    committee = await _committee_or_404(committee_id)
    if not await _committee_access(user, committee, permissions.CHAT_VIEW):
        raise HTTPException(status_code=403, detail="Forbidden")
    return await database.get_chat_messages(committee_id)


@app.post(
    "/committees/{committee_id}/messages",
    tags=["Chat"],
    status_code=201,
    response_model=models.ChatMessage,
    responses={403: {"model": models.ErrorResponse}, 404: {"model": models.ErrorResponse}},
)
async def post_message(
    committee_id: int,
    message: models.NewChatMessage,
    user: models.AuthUser = Depends(get_current_user),
):
    """Send a message (REST fallback for the WebSocket). Broadcasts to live subscribers."""
    committee = await _committee_or_404(committee_id)
    if not await _may_send(user, committee, message):
        raise HTTPException(status_code=403, detail="Forbidden")
    kind, body, payload = _message_parts(message)
    saved = await database.add_chat_message(committee_id, user.email, kind, body, payload)
    await chat.hub.broadcast(committee_id, saved.model_dump(mode="json"))
    return saved


@app.websocket("/ws/committees/{committee_id}/chat")
async def committee_chat_ws(websocket: WebSocket, committee_id: int):
    """Live chat for a committee's channel. The client sends {"token": "..."} as its first
    frame (headers aren't reliable on a WebSocket handshake, and query-string tokens leak
    into logs). After that, each frame is a NewChatMessage. On connect we replay recent
    history."""
    await websocket.accept()
    try:
        auth_frame = await websocket.receive_json()
    except Exception:
        await websocket.close(code=1008)
        return

    token = auth_frame.get("token") if isinstance(auth_frame, dict) else None
    if not token:
        await websocket.close(code=4401)
        return
    try:
        user = await get_current_user(token)
    except HTTPException:
        await websocket.close(code=4401)
        return

    committee = await database.get_committee(committee_id)
    if not committee:
        await websocket.close(code=4404)
        return
    if not await _committee_access(user, committee, permissions.CHAT_VIEW):
        await websocket.close(code=4403)
        return
    can_post = await _committee_access(user, committee, permissions.CHAT_POST)

    await chat.hub.connect(committee_id, websocket)
    try:
        for msg in await database.get_chat_messages(committee_id):
            await websocket.send_json(msg.model_dump(mode="json"))
        while True:
            data = await websocket.receive_json()
            if not can_post:
                await websocket.send_json({"error": "You cannot post to this channel"})
                continue
            try:
                incoming = models.NewChatMessage(**data)
            except Exception:
                await websocket.send_json({"error": "Invalid message"})
                continue
            if not await _may_send(user, committee, incoming):
                await websocket.send_json({"error": "You cannot send that here"})
                continue
            kind, body, payload = _message_parts(incoming)
            saved = await database.add_chat_message(
                committee_id, user.email, kind, body, payload
            )
            await chat.hub.broadcast(committee_id, saved.model_dump(mode="json"))
    except WebSocketDisconnect:
        pass
    finally:
        await chat.hub.disconnect(committee_id, websocket)


#####################################
# OC STUFF
#####################################

@app.post(
    "/manual_verify",
    tags=["OC"],
    status_code=201,
    responses={
        403: {"model": models.ErrorResponse},
        404: {"model": models.ErrorResponse},
        500: {"model": models.ErrorResponse},
    },
)
async def manual_verify(
    email: str, user: models.AuthUser = Depends(require_any_oc)
):
    """Mark a delegate verified when their email did not arrive. Any OC member can do this
    on the ground (ADR 0003)."""
    try:
        delegate = await database.get_delegate_by_email(email)
        if not delegate:
            raise HTTPException(status_code=404, detail="Delegate not found")

        delegate.verified = True
        await database.update_delegate_by_id(delegate.id, delegate)
        # If this delegate was on an OC roster, applying invites now makes them a member.
        await database.apply_pending_invites(delegate.email)

        return JSONResponse(status_code=201, content={"message": "Email verified!"})
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


#####################################
# DELETE USER ACCOUNT
#####################################


@app.delete("/account", tags=["Auth"], status_code=200)
async def delete_user(user: models.AuthUser = Depends(get_current_user)):
    if user.role == "admin":
        raise HTTPException(
            status_code=403,
            detail="Admins must be demoted before they can delete their account",
        )
    try:
        delegate_id = await database.delete_account(user.email)
        if delegate_id:
            # The cached QR image is the last copy of their id on disk.
            qr_image = os.path.join(utils.qr_folder, f"{delegate_id}.jpg")
            if os.path.exists(qr_image):
                os.remove(qr_image)
        return JSONResponse(
            status_code=200, content={"message": "Account deleted successfully"}
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


###############################################
# Changes Regarding Password Changes By Kartik
###############################################

@app.get(
    "/reset",
    tags=["Auth"],
    responses={
        403: {"model": models.ErrorResponse},
        404: {"model": models.ErrorResponse},
        500: {"model": models.ErrorResponse},
    },
)
async def serve_reset_html(request: Request, token: str):
    try:
        await verify_reset_token(token)
    except HTTPException as e:
        return templates.TemplateResponse(
            request, "reset.html", {"invalid": True}, status_code=e.status_code
        )
    return templates.TemplateResponse(request, "reset.html", {"invalid": False})


# The address the privacy policy gives for deletion requests and questions. Keep it the
# same as the one in _PRIVACY_HTML and the app's PrivacyPolicyPage.
PRIVACY_CONTACT_EMAIL = "secretariat.mumbaimun@gmail.com"


@app.get("/delete-account", tags=["Auth"], response_class=HTMLResponse)
async def delete_account_page(request: Request):
    """Public page explaining how to delete a Delego account and its data. Google Play's
    data safety form needs this as a web link, alongside the in-app option."""
    return templates.TemplateResponse(
        request, "delete_account.html", {"support_email": PRIVACY_CONTACT_EMAIL}
    )


@app.post(
    "/reset_password",
    tags=["Auth"],
    responses={
        400: {"model": models.ErrorResponse},
        422: {"model": models.ErrorResponse},
    },
)
@limiter.limit("5/minute")
async def reset_password(
    request: Request,
    body: models.ResetPassword,
    token: str = Depends(oauth2_scheme),
):
    """Set a new password using the token from the reset email (as a Bearer token).
    The token expires after a short time and stops working once the password changes."""
    user = await verify_reset_token(token)
    await database.change_user_pass(
        user.email, await asyncio.to_thread(hash_password, body.password)
    )
    return JSONResponse(status_code=200, content={"message": "Password changed!"})


###############################################
# App Specific changes for dynamic data
###############################################

ROOMS_FILE_PATH = os.path.join(os.path.dirname(__file__), "data", "rooms.json")
FLOOR_PLANS_FILE_PATH = os.path.join(os.path.dirname(__file__), "data", "floor_plans.json")


def read_rooms_data():
    """Reads and parses the rooms data from the JSON file."""
    try:
        # Check if the file exists
        if not os.path.exists(ROOMS_FILE_PATH):
            return HTTPException(status_code=500, detail="Error reading rooms data: File does not exist")
        
        with open(ROOMS_FILE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
            
    except json.JSONDecodeError:
        # Handle cases where the JSON file is invalid
        print(f"Error decoding JSON from: {ROOMS_FILE_PATH}")
        raise HTTPException(status_code=500, detail="Error reading rooms data: Invalid JSON format")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        raise HTTPException(status_code=500, detail="Internal server error while fetching rooms data")


@app.get(
    "/rooms",
    tags=["Dynamic Data"],
    responses={
        500: {"model": models.ErrorResponse},
    },
)
def get_rooms():
    """Returns the rooms data from data/rooms.json."""
    try:
        rooms_data = read_rooms_data()
        return rooms_data
        
    except HTTPException as e:
        # Re-raise the HTTPException raised by read_rooms_data
        raise e
    except Exception as e:
        # Catch any other unexpected errors
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")


def read_floor_plans_data():
    """Reads and parses the floor plans layout data from data/floor_plans.json."""
    try:
        if not os.path.exists(FLOOR_PLANS_FILE_PATH):
            return {"canvas_width": 1000, "canvas_height": 1000, "floors": []}
        with open(FLOOR_PLANS_FILE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="Error reading floor plans data: Invalid JSON format")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error while fetching floor plans data: {str(e)}")


@app.get(
    "/floor_plans",
    tags=["Dynamic Data"],
    responses={
        500: {"model": models.ErrorResponse},
    },
)
def get_floor_plans():
    """Returns the dynamic building floor plans from data/floor_plans.json."""
    return read_floor_plans_data()


STUDY_GUIDES_FILE_PATH = os.path.join(os.path.dirname(__file__), "data", "study_guides.json")
STUDY_GUIDES_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static", "study_guides")


def read_study_guides_data():
    """Reads study guides config from data/study_guides.json and auto-discovers PDFs in static/study_guides/."""
    try:
        data = {"guides": []}
        if os.path.exists(STUDY_GUIDES_FILE_PATH):
            with open(STUDY_GUIDES_FILE_PATH, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict) and isinstance(loaded.get("guides"), list):
                    data = loaded

        guides_by_key = {
            str(g.get("committee", "")).strip().upper(): g
            for g in data.get("guides", [])
            if isinstance(g, dict) and g.get("committee")
        }

        if os.path.isdir(STUDY_GUIDES_STATIC_DIR):
            for fname in sorted(os.listdir(STUDY_GUIDES_STATIC_DIR)):
                if not fname.lower().endswith(".pdf"):
                    continue
                stem = os.path.splitext(fname)[0].strip()
                key = stem.upper()
                static_url = f"/static/study_guides/{fname}"
                if key in guides_by_key:
                    agendas = guides_by_key[key].setdefault("agendas", [])
                    if not agendas:
                        agendas.append({"title": stem, "url": static_url})
                    elif not agendas[0].get("url"):
                        agendas[0]["url"] = static_url
                else:
                    entry = {
                        "committee": stem,
                        "agendas": [{"title": stem, "url": static_url}],
                    }
                    data.setdefault("guides", []).append(entry)
                    guides_by_key[key] = entry

        return data
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="Error reading study guides data: Invalid JSON format")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error while fetching study guides data: {str(e)}")


@app.get(
    "/study_guides",
    tags=["Dynamic Data"],
    responses={
        500: {"model": models.ErrorResponse},
    },
)
def get_study_guides():
    """Returns backend-hosted study guides from data/study_guides.json and static/study_guides/."""
    return read_study_guides_data()


SCHEDULE_FILE_PATH = os.path.join(os.path.dirname(__file__), "data", "schedule.json")


def read_schedule_data():
    """Reads and parses the full schedule data from the JSON file."""
    try:
        if not os.path.exists(SCHEDULE_FILE_PATH):
            return {"conference_days": [], "events": []}
        
        with open(SCHEDULE_FILE_PATH, "r") as f:
            data = json.load(f)
            return {
                "conference_days": data.get("conference_days", []),
                "events": data.get("events", [])
            }
            
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error while fetching schedule data")


@app.get(
    "/schedule",
    tags=["Dynamic Data"],
    responses={
        500: {"model": models.ErrorResponse},
    },
)
def get_schedule():
    """Returns the full event schedule data including day metadata."""
    schedule_data = read_schedule_data()
    return schedule_data


# The app's update prompt. Each platform has `latest` (x.y.z; an older app is asked, once,
# to update, and can say "Not now") and the store link the prompt opens. There is no
# forced update: a blocked app would lose its offline QR badge and scanner at the venue.
# Edit data/app_config.json and redeploy to announce a release.
APP_CONFIG_FILE_PATH = os.path.join(os.path.dirname(__file__), "data", "app_config.json")
APP_PLATFORMS = ("ios", "android")
DEFAULT_UPDATE_MESSAGE = "A new version of Delego is available."


def _is_app_version(value) -> bool:
    """Whether `value` is `x.y.z` (three plain numbers)."""
    if not isinstance(value, str):
        return False
    parts = value.split(".")
    return len(parts) == 3 and all(p.isascii() and p.isdigit() for p in parts)


def parse_app_config(data) -> dict:
    """The valid part of the config. A platform with a bad version or a store link that
    isn't https is left out (so that platform gets no prompt). Other keys, such as an old
    `min`, are ignored."""
    log = logging.getLogger("uvicorn.error")
    if not isinstance(data, dict):
        log.warning("data/app_config.json is not an object; no update prompt is served")
        return {}
    message = data.get("message")
    out = {"message": message if isinstance(message, str) and message.strip() else DEFAULT_UPDATE_MESSAGE}
    for platform in APP_PLATFORMS:
        entry = data.get(platform)
        if entry is None:
            continue
        if not isinstance(entry, dict):
            log.warning("data/app_config.json: %s is not an object; ignored", platform)
            continue
        store_url = entry.get("store_url", "")
        if not _is_app_version(entry.get("latest")):
            log.warning("data/app_config.json: %s needs latest as x.y.z; ignored", platform)
            continue
        if not isinstance(store_url, str) or (store_url and not store_url.startswith("https://")):
            log.warning("data/app_config.json: %s store_url must be empty or https; ignored", platform)
            continue
        out[platform] = {"latest": entry["latest"], "store_url": store_url}
    return out


@lru_cache
def read_app_config() -> dict:
    """Reads data/app_config.json. A missing or broken file gives {} (no prompt), so a bad
    edit can never stop the app from opening."""
    try:
        with open(APP_CONFIG_FILE_PATH, "r") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except Exception:
        logging.getLogger("uvicorn.error").exception(
            "data/app_config.json could not be read; no update prompt is served"
        )
        return {}
    return parse_app_config(data)


@app.get("/public/app_config", tags=["Dynamic Data"])
def get_app_config():
    """What the app needs to decide whether to ask for an update. Public: the app reads it
    before anyone logs in."""
    return read_app_config()


####################

# Committee floor (EB Tools): the GSL and moderated caucus speakers' lists and the timer,
# shown live on the smartboard (static/live_gsl.html). See floor.py.


@app.get(
    "/public/committees",
    tags=["Floor"],
    response_model=list[models.Committee],
)
async def public_list_committees():
    """The committees of the conference (event 1), by name, for the smartboard and the EB
    Tools committee picker. Public: committee names aren't secret."""
    return await database.list_committees(1)


# Committees that exist, remembered for a minute so the boards' polls and reconnects don't
# each cost a query (committees are only ever added, by a head, before the conference).
_known_committees: dict[int, float] = {}


async def _committee_exists(committee_id: int) -> bool:
    now = time.monotonic()
    seen = _known_committees.get(committee_id)
    if seen is not None and now - seen < 60:
        return True
    if await database.get_committee(committee_id):
        _known_committees[committee_id] = now
        return True
    return False


# Each committee's portfolio matrix, remembered for 5 minutes (the matrices don't change
# during the conference).
_matrices: dict[int, tuple[float, list[dict]]] = {}


_CCC_FLAGS = {
    "cornell franklin": "us",
    "clarence edward gauss": "us",
    "stirling fessenden": "us",
    "addie viola smith": "us",
    "sir william johnston keswick": "gb",
    "kenneth bourne": "gb",
    "frederick wernham gerrard": "gb",
    "sir herbert phillips": "gb",
    "frederick maze": "gb",
    "victor sassoon": "gb",
    "liu xiao": "cn-ccp",
    "pan hannian": "tw",
    "liu changsheng": "cn-ccp",
    "kp chen": "tw",
    "du yuesheng": "tw",
    "yu xiaqing": "tw",
    "huang jinrong": "tw",
    "fu xiao'an": "tw",
    "li shiqun": "tw",
    "dai li": "tw-kmt",
    "ding mocun": "tw",
    "marcel baudez": "fr",
    "robert jacquinot de besange": "fr",
    "dr. oskar trautmann": "de-imp",
}


async def _committee_matrix(committee_id: int) -> list[dict]:
    now = time.monotonic()
    cached = _matrices.get(committee_id)
    if cached is not None and now - cached[0] < 300:
        return cached[1]
    matrix = await database.list_committee_portfolios(committee_id)
    for p in matrix:
        if not p.get("code"):
            fallback_code = _CCC_FLAGS.get(str(p.get("name", "")).strip().casefold())
            if fallback_code:
                p["code"] = fallback_code
    _matrices[committee_id] = (now, matrix)
    return matrix


def _resolve_portfolio(op: dict, matrix: list[dict]) -> dict:
    """For a committee with a matrix, an add takes the matrix's own name, flag and party
    tag (whatever the client sent), so a typo or a wrong flag can't reach the board. A name
    that isn't in the matrix ("Add other") is added as a person: no flag, no tag."""
    if op["op"] != "add" or not matrix:
        return op
    wanted = op["name"].casefold()
    for portfolio in matrix:
        if portfolio["name"].casefold() == wanted:
            return {**op, "name": portfolio["name"], "code": portfolio["code"], "tag": portfolio["tag"]}
    return {**op, "code": None, "tag": None}


async def _floor_committee(committee_id: int) -> None:
    if not await _committee_exists(committee_id):
        raise HTTPException(status_code=404, detail="Committee not found")


@app.get(
    "/public/committees/{committee_id}/portfolios",
    tags=["Floor"],
    response_model=list[models.CommitteePortfolio],
    responses={404: {"model": models.ErrorResponse}},
)
async def public_committee_portfolios(committee_id: int):
    """A committee's official portfolio matrix, in sheet order; [] for a committee without
    one (IPC), whose EBs add anyone. Public, like the matrices themselves."""
    await _floor_committee(committee_id)
    return await _committee_matrix(committee_id)


@app.get(
    "/committees/{committee_id}/floor",
    tags=["Floor"],
    response_model=models.FloorFrame,
    responses={404: {"model": models.ErrorResponse}},
)
async def get_committee_floor(committee_id: int):
    """A committee's lists and timer. Public and read-only, like the board that shows it."""
    await _floor_committee(committee_id)
    try:
        return floor.frame(await floor.hub.snapshot(committee_id))
    except floor.FloorError as e:
        raise HTTPException(status_code=e.status, detail=e.detail)


@app.post(
    "/committees/{committee_id}/floor/ops",
    tags=["Floor"],
    response_model=models.FloorFrame,
    responses={
        403: {"model": models.ErrorResponse},
        404: {"model": models.ErrorResponse},
        409: {"model": models.ErrorResponse},
        503: {"model": models.ErrorResponse},
    },
)
# High: every EB in the venue can share one public IP.
@limiter.limit("600/minute")
async def committee_floor_op(
    request: Request,
    committee_id: int,
    op: Annotated[models.FloorOp, Body()],
    user: Annotated[models.AuthUser, Depends(get_current_user)],
):
    """Change a committee's floor with one op (add, remove, move, clear, set_speaker_time,
    start_speaker, start_unmod, pause, resume, reset, next, set_display) and get the new
    state back. Everyone watching the floor gets it too. For EBs, OC, admins and heads."""
    is_head = False
    if user.role not in permissions.FLOOR_ROLES:
        is_head, _, _ = await database.get_effective_access(user.email)
    if not permissions.can_run_floor(user.role, is_head):
        raise HTTPException(status_code=403, detail="Only the EB can change the floor")
    await _floor_committee(committee_id)
    resolved = _resolve_portfolio(op.model_dump(), await _committee_matrix(committee_id))
    try:
        state = await floor.hub.apply(committee_id, resolved, user.email)
    except floor.FloorError as e:
        raise HTTPException(status_code=e.status, detail=e.detail)
    return floor.frame(state)


@app.websocket("/ws/committees/{committee_id}/floor")
async def committee_floor_ws(websocket: WebSocket, committee_id: int):
    """Follow a committee's floor live: the state on connect, after every change, and at
    least every floor.HEARTBEAT_SECONDS (so a client can tell a dead link from a quiet
    floor). Public and read-only (the smartboard has no login); a client that sends
    anything is closed (1008). 4404 unknown committee, 1013 when the committee has too many
    viewers, 1011 when the client stops taking frames."""
    await websocket.accept()
    if not await _committee_exists(committee_id):
        await websocket.close(code=4404)
        return
    viewer = floor.hub.connect(committee_id, websocket)
    if viewer is None:
        await websocket.close(code=1013)
        return

    async def push_state() -> None:
        try:
            viewer.push_state(await floor.hub.snapshot(committee_id))
        except floor.FloorError:
            pass  # the floor is busy (a slow database); the next beat retries

    try:
        await push_state()
        while not viewer.dead:
            try:
                message = await asyncio.wait_for(
                    websocket.receive(), floor.HEARTBEAT_SECONDS
                )
            except TimeoutError:
                await push_state()  # the heartbeat
                continue
            if message["type"] != "websocket.disconnect":
                await websocket.close(code=1008)
            break
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        floor.hub.disconnect(committee_id, viewer)



_PRIVACY_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Privacy Policy · Delego (Mumbai MUN 2026)</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", Roboto, sans-serif; background: #0f111a; color: #f1f3f9; margin: 0; padding: 32px 20px; line-height: 1.6; }
    main { max-width: 720px; margin: 0 auto; background: #181b28; border-radius: 16px; padding: 28px; border: 1px solid #2b3048; }
    h1 { color: #ff2bb4; margin-top: 0; font-size: 1.75rem; }
    h2 { color: #ff2bb4; font-size: 1.15rem; margin-top: 24px; }
    a { color: #d9ff1f; }
    .meta { color: #9aa3bd; font-size: 0.9rem; margin-bottom: 20px; }
  </style>
</head>
<body>
  <main>
    <h1>Delego — Mumbai MUN 2026 Privacy Policy</h1>
    <div class="meta">Last updated: 22 October 2026 · MUN Society, MPSTME (NMIMS Mumbai)</div>

    <h2>1. What We Collect &amp; Why</h2>
    <p>When you create a Delego account for Mumbai MUN 2026, we collect:</p>
    <ul>
      <li><strong>Name and Email Address:</strong> to identify your account, send your 6-digit verification code and password reset emails, and match your conference registration.</li>
      <li><strong>Contact Phone Number:</strong> to verify your delegate registration at the registration desk and reach you for urgent committee or conference logistics.</li>
      <li><strong>Date of Birth (Optional):</strong> if provided, used only to verify delegate eligibility.</li>
      <li><strong>Food Preference (Veg / Jain) &amp; Meal Scans:</strong> to prepare catering counts and issue your QR meal badge at conference meals (breakfast, lunch, and high tea).</li>
      <li><strong>Allergy &amp; Food Notes:</strong> any allergies or dietary notes recorded with your registration, so the catering team can serve you safely.</li>
      <li><strong>Camera Access (Staff Only):</strong> used solely by Organizing Committee staff to scan delegate QR badges. Camera video is never recorded or uploaded.</li>
    </ul>

    <h2>2. How Your Data Is Processed &amp; Stored</h2>
    <p>Your account profile and meal scan records are transmitted over HTTPS and stored in our cloud backend hosted on Render and Supabase (PostgreSQL). Transactional verification and password-reset emails are delivered through Brevo. We do not sell, rent, or share your personal data with advertisers, and the app does not track you across other apps or websites.</p>

    <h2>3. Who Can See Your Data</h2>
    <p>Only Mumbai MUN 2026 Organising Committee (OC) staff who need it for their role can see your details:</p>
    <ul>
      <li><strong>Hospitality and meal staff</strong> see your name, committee, diet, any allergy or food notes, and the meals you have collected that day when they scan your badge or look you up at a meal. When they search for a delegate by name or email, the email is shown partly hidden.</li>
      <li><strong>Conference organisers and administrators</strong> can see delegate names and committees, and administrators can see your account details to manage registrations and roles.</li>
    </ul>
    <p>Other delegates cannot see your personal details.</p>

    <h2>4. Minors &amp; Student Delegates</h2>
    <p>Mumbai MUN is an academic Model United Nations conference open to high-school and university delegates (aged 13 and above). Personal data of student delegates is collected strictly for event administration, committee participation, and on-campus meal service. Delegates or their parents/guardians may delete their account or request data removal at any time.</p>

    <h2>5. Data Retention</h2>
    <p>We retain delegate account and meal scan data only for the duration of Mumbai MUN 2026 (30 October – 1 November 2026) and post-conference reconciliation (up to 60 days after the conference concludes), after which conference delegate records are permanently deleted.</p>

    <h2 id="delete-account">6. Account &amp; Data Deletion</h2>
    <p>You can permanently delete your account and associated personal data at any time:</p>
    <ul>
      <li><strong>Inside the Delego App:</strong> Open <em>Settings</em> from the side menu and tap <strong>Delete Account</strong> at the bottom of the screen. Your account and profile data are deleted immediately.</li>
      <li><strong>On the Web / By Email:</strong> Visit <a href="/delete-account">/delete-account</a> or email <a href="mailto:secretariat.mumbaimun@gmail.com?subject=Delete%20Delego%20Account">secretariat.mumbaimun@gmail.com</a> from your registered email address with the subject <code>Delete Delego Account</code>, and we will erase your account and associated records within 48 hours.</li>
    </ul>
    <p>Deleting your account removes your login, profile, registration details (including diet and food notes), meal history, QR badge and any OC team memberships. Messages that OC staff sent in a committee's break-coordination chat are kept with the sender's email address, so the conversation stays readable for the other staff in it.</p>

    <h2>7. Contact Us</h2>
    <p>MUN Society, MPSTME, SVKM's NMIMS, Vile Parle (West), Mumbai 400056.<br/>
    Email: <a href="mailto:secretariat.mumbaimun@gmail.com">secretariat.mumbaimun@gmail.com</a><br/>
    Phone: +91 95600 46431</p>
  </main>
</body>
</html>
"""


@app.get("/privacy", response_class=HTMLResponse, include_in_schema=False)
async def privacy_policy_page():
    return HTMLResponse(content=_PRIVACY_HTML)

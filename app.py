import asyncio
import csv
from contextlib import asynccontextmanager
from functools import lru_cache
from io import StringIO
import os
from typing import Annotated
import uuid
import json

from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    Form,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse, JSONResponse, Response
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

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
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

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post(
    "/login",
    tags=["Auth"],
    response_model=models.Token,
    responses={
        401: {"model": models.ErrorResponse},
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
    await mails.send_verification_email(delegate, code)


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
    return models.Me(
        **user.model_dump(),
        is_head=is_head,
        # OC team permissions as before, plus the strings the Delego app gates its screens on
        # (derived from the role and the team permissions, see permissions.py).
        permissions=sorted(
            set(perms) | permissions.app_permissions(user.role, is_head, perms)
        ),
        teams=memberships,
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
            if not delegate.verified:
                delegate.verified = True
                await database.update_delegate_by_id(delegate.id, delegate)

            mm_delegate = await database.get_mm_delegate_by_email(user.email)

            if mm_delegate:
                raise HTTPException(
                    status_code=409,
                    detail=f"Mumbai MUN Delegate already registered! ID: {mm_delegate.id}",
                )

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
                )
            )

            return JSONResponse(
                status_code=201,
                content={
                    "message": f"Mumbai MUN Delegate registered successfully! ID: {mm_delegate.id}"
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
                        verified=True,
                    )
                )

            await database.add_user(user)

            if not delegate.verified:
                delegate.verified = True
                await database.update_delegate_by_id(delegate.id, delegate)

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
                )
            )

            try:
                await send_verification_code(delegate)
                return JSONResponse(
                    status_code=201,
                    content={
                        "message": f"User with id {delegate.id} created successfully!"
                    },
                )
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

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
    meal: Annotated[str, Form()],
    diet: Annotated[str, Form()] = "",
    scanned_at: Annotated[str, Form()] = "",  # sent by the app; the server uses its own clock
    user: models.AuthUser = Depends(require_food_scan),
):
    """Record a delegate collecting a meal. The day is derived from today's date against
    the event, so the operator only picks the meal (breakfast, lunch or high_tea) and,
    optionally, the diet served. Returns `served`, or `duplicate` (with a 200) when they
    already collected this meal, which is logged to the flagged list."""
    meal = _parse_meal(meal)
    diet = _parse_diet(diet)
    try:
        event_id, day = await database.resolve_scan_day()
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
    """Every rejected second-scan for the running event: who tried for seconds."""
    try:
        event_id, _ = await database.resolve_current_event_day()
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
    if not await _committee_access(user, committee, permissions.CHAT_POST):
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
        await database.delete_user(user.email)
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

@lru_cache()
def read_rooms_data():
    """Reads and parses the rooms data from the JSON file."""
    try:
        # Check if the file exists
        if not os.path.exists(ROOMS_FILE_PATH):
            return HTTPException(status_code=500, detail="Error reading rooms data: File does not exist")
        
        with open(ROOMS_FILE_PATH, "r") as f:
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
    
    
SCHEDULE_FILE_PATH = os.path.join(os.path.dirname(__file__), "data", "schedule.json")

@lru_cache()
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
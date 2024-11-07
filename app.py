import csv
from functools import lru_cache
from io import StringIO
import os
from typing import Annotated
import uuid

from fastapi import APIRouter, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, HTMLResponse
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic_core import to_json
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from auth import (
    check_verification_token,
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)
import config
import database
import mails
import models
import utils

####################

# Initialization

limiter = Limiter(key_func=get_remote_address)

settings = config.get_settings()

app = FastAPI(
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

database.init()


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
        user.password = hash_password(user.password)

        user_exists = database.get_user_by_email(user.email)
        if user_exists:
            raise HTTPException(status_code=409, detail="User already exists")

        delegate = database.get_delegate_by_email(user.email)
        if not delegate:
            uid = str(uuid.uuid4()).replace("-", "")
            delegate = database.add_delegate(
                models.Delegate(
                    id=uid,
                    firstname=user.firstname,
                    lastname=user.lastname,
                    email=user.email,
                )
            )
        database.add_user(user)

        try:
            await mails.send_verification_email(delegate)
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
def login(request: Request, form_data: OAuth2PasswordRequestForm = Depends()):
    try:
        email = form_data.username
        password = form_data.password

        admin = database.get_admin_by_email(email)
        if not admin:
            user = database.get_user_by_email(email)
            if not user:
                raise HTTPException(status_code=401, detail="Invalid email")
            if not verify_password(password, user.password):
                raise HTTPException(status_code=401, detail="Invalid password")
            access_token = create_access_token(data={"sub": user.email})
        else:
            if not verify_password(password, admin.password):
                raise HTTPException(status_code=401, detail="Invalid password")
            access_token = create_access_token(data={"sub": admin.email})
        return models.Token(access_token=access_token, token_type="bearer")

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get(
    "/verify_email",
    tags=["Auth"],
    status_code=200,
    responses={500: {"model": models.ErrorResponse}},
)
@limiter.limit("10/minute")
async def verify_email(request: Request, token: str):
    try:
        delegate = await check_verification_token(token)
        if type(delegate) != models.Delegate:
            raise HTTPException(status_code=401, detail="Invalid token")
        database.verify_delegate_email(delegate.email)
        return JSONResponse(status_code=200, content={"message": "Email verified!"})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
        delegate = database.get_delegate_by_email(email)
        if not delegate:
            raise HTTPException(status_code=404, detail="Delegate not found")
        if delegate.verified:
            raise HTTPException(status_code=409, detail="Email already verified")
        await mails.send_verification_email(delegate)
        return JSONResponse(
            status_code=200, content={"message": "Verification email sent!"}
        )
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
        delegate = database.get_delegate_by_email(email)
        if not delegate:
            raise HTTPException(status_code=404, detail="User not found")
        if not delegate.verified:
            raise HTTPException(status_code=403, detail="User not verified")
        await mails.send_password_reset_email(delegate)
        return JSONResponse(
            status_code=200, content={"message": "Password reset email sent!"}
        )
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
def change_password(
    request: Request,
    password: str,
    delegate: models.Delegate | models.Admin = Depends(get_current_user),
):
    try:
        if type(delegate) != models.Delegate:
            raise HTTPException(status_code=403, detail="Forbidden")
        user = database.get_user_by_email(delegate.email)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        database.change_user_pass(user.email, hash_password(password))
        return JSONResponse(status_code=200, content={"message": "Password changed!"})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


####################

# ADMIN STUFF


@app.get(
    "/hash_password", tags=["Admin"], responses={500: {"model": models.ErrorResponse}}
)
def get_hashed_password(password: str) -> str:
    try:
        return hash_password(password)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get(
    "/backup",
    tags=["Admin"],
    response_class=FileResponse,
    responses={
        403: {"model": models.ErrorResponse},
        500: {"model": models.ErrorResponse},
    },
)
def backup_database(user: models.Delegate | models.Admin = Depends(get_current_user)):
    try:
        if type(user) != models.Admin:
            raise HTTPException(status_code=403, detail="Forbidden")
        database.backup_database()
        return FileResponse(
            os.path.join(os.path.dirname(__file__), "backups", "backup_db.zip"),
            media_type="application/zip",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
        if type(user) != models.Admin:
            raise HTTPException(status_code=403, detail="Forbidden")
        data = database.get_delegates()
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
    response_model=models.Delegate,
    responses={500: {"model": models.ErrorResponse}},
)
def get_current_delegate(
    user: models.Delegate | models.Admin = Depends(get_current_user),
):
    try:
        if type(user) == models.Admin:
            raise HTTPException(status_code=500, detail="You are an admin")
        return user
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
def get_delegate_by_id(
    id: str, user: models.Delegate | models.Admin = Depends(get_current_user)
):
    try:
        if type(user) != models.Admin:
            if type(user) == models.Delegate and user.id == id:
                return user
            raise HTTPException(status_code=403, detail="Forbidden")
        data = database.get_delegate_by_id(id)
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
def update_delegate(
    id: str,
    user: models.Delegate | models.Admin = Depends(get_current_user),
    firstname: str = "",
    lastname: str = "",
    contact: str = "",
    dateofbirth: str = "",
    gender: str = "",
    pastmuns: list[models.MunExperience] = [],
    verified: bool = False,
):
    try:
        if type(user) != models.Admin:
            if type(user) == models.Delegate and user.id != id:
                raise HTTPException(status_code=403, detail="Forbidden")
        data = database.get_delegate_by_id(id)
        if not data:
            raise HTTPException(status_code=404, detail="Delegate not found")
        if firstname != "":
            data.firstname = firstname
        if lastname != "":
            data.lastname = lastname
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
        return database.update_delegate_by_id(id, data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


####################

# MUMBAIMUN QR CODES


@app.get("/qr", tags=["QR"])
def get_qr(id: str):
    try:
        qr_folder = utils.qr_folder
        if not os.path.exists(qr_folder):
            os.makedirs(qr_folder)

        qr_image = f"{qr_folder}/{id}.jpg"

        if not os.path.exists(qr_image):
            utils.generate_qr(id)
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
        user.password = hash_password(user.password)
        user_exists = database.get_user_by_email(user.email)

        if user_exists:
            delegate = database.get_delegate_by_email(user.email)

            if not delegate:
                raise HTTPException(
                    status_code=400, detail="User exists but is not a delegate."
                )
            if not delegate.verified:
                raise HTTPException(status_code=403, detail="Email not verified")

            mm_delegate = database.get_mm_delegate_by_email(user.email)

            if mm_delegate:
                raise HTTPException(
                    status_code=409,
                    detail=f"Mumbai MUN Delegate already registered! ID: {mm_delegate.id}",
                )

            mm_delegate = database.add_mm_delegate(
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
            delegate = database.get_delegate_by_email(user.email)
            if not delegate:
                uid = str(uuid.uuid4()).replace("-", "")
                delegate = database.add_delegate(
                    models.Delegate(
                        id=uid,
                        firstname=user.firstname,
                        lastname=user.lastname,
                        email=user.email,
                    )
                )

            database.add_user(user)

            mm_delegate = database.get_mm_delegate_by_email(user.email)

            if mm_delegate:
                raise HTTPException(
                    status_code=409,
                    detail=f"Mumbai MUN Delegate already registered! ID: {mm_delegate.id}",
                )

            mm_delegate = database.add_mm_delegate(
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
                await mails.send_verification_email(delegate)
                return JSONResponse(
                    status_code=201,
                    content={
                        "message": f"User with id {delegate.id} created successfully. Please verify your email."
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
    user: models.Delegate | models.Admin = Depends(get_current_user), format: str = ""
):
    try:
        if type(user) != models.Admin:
            raise HTTPException(status_code=403, detail="Forbidden")

        data = database.get_mm_delegates()
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
# Changes related to food by Kartik #
#####################################


@app.get("/scan", tags=["QR"])
def scan(request: Request):
    return templates.TemplateResponse("scan.html", {"request": request})


@app.get("/food", tags=["Food"], response_class=HTMLResponse)
def get_food(request: Request, id: str):
    try:
        delegate = database.get_mm_delegate_by_id(id)
        if not delegate:
            raise HTTPException(status_code=404, detail="Delegate not found")

        return templates.TemplateResponse(
            "food.html", {"request": request, "delegate": delegate}
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/food", tags=["Food"], status_code=201)
def update_food(
    id: Annotated[str, Form()],
    d1_bf: Annotated[bool, Form()] = True,
    d1_lunch: Annotated[bool, Form()] = False,
    d1_hitea: Annotated[bool, Form()] = False,
    d2_bf: Annotated[bool, Form()] = False,
    d2_lunch: Annotated[bool, Form()] = False,
    d2_hitea: Annotated[bool, Form()] = False,
    d3_bf: Annotated[bool, Form()] = False,
    d3_lunch: Annotated[bool, Form()] = False,
    d3_hitea: Annotated[bool, Form()] = False,
):
    # Fetch the existing delegate
    delegate = database.get_mm_delegate_by_id(id)
    if not delegate:
        raise HTTPException(status_code=404, detail="Delegate not found")

    delegate.d1_bf = d1_bf
    delegate.d1_lunch = d1_lunch
    delegate.d1_hitea = d1_hitea
    delegate.d2_bf = d2_bf
    delegate.d2_lunch = d2_lunch
    delegate.d2_hitea = d2_hitea
    delegate.d3_bf = d3_bf
    delegate.d3_lunch = d3_lunch
    delegate.d3_hitea = d3_hitea

    try:
        database.update_mm_delegate(delegate.id, delegate)
        return JSONResponse(
            status_code=201,
            content={"message": "Food updated successfully"},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


#####################################
# OC STUFF
#####################################


@app.post("/manual_verify", tags=["OC"], status_code=201)
def manual_verify(email: str):
    try:
        delegate = database.get_delegate_by_email(email)
        if not delegate:
            raise HTTPException(status_code=404, detail="Delegate not found")

        delegate.verified = True
        database.update_delegate_by_id(delegate.id, delegate)

        return JSONResponse(status_code=201, content={"message": "Email verified!"})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


#####################################
# DELETE USER ACCOUNT
#####################################


@app.delete("/account", tags=["Auth"], status_code=200)
def delete_user(user: models.Delegate | models.Admin = Depends(get_current_user)):
    if type(user) != models.Delegate:
        raise HTTPException(status_code=500, detail="You are an admin")
    try:
        database.delete_user(user.email)
        return JSONResponse(
            status_code=200, content={"message": "Account deleted successfully"}
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

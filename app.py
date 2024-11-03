from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, Response, FileResponse
from fastapi.security import OAuth2PasswordRequestForm
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from functools import lru_cache
from io import StringIO
import os, uuid, csv

from auth import get_current_user, create_access_token, verify_password, hash_password, check_verification_token
import config, database, models, mails

####################

# Initialization

limiter = Limiter(key_func=get_remote_address)

settings = config.get_settings()

app = FastAPI(
    title="MUNDRA - MUNSoc Delegate Resource Application",
    description="Named after Mundra Port, Kutch, Gujarat, MUNDRA - MUNSoc Delegate Resource Application is a centralized database designed to optimize event planning, streamline communication, and facilitate delegate management",
    version="1.0.0",
    docs_url=settings.docs_url,
    redoc_url=settings.redoc_url
)

app.mount("/static", StaticFiles(directory="static"), name="static")

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

database.init()


@app.get("/", tags=["Status"])
def status():
    return {"message": "Server is up and running"}

####################

# Auth

@app.post("/register", tags=["Auth"], status_code=201, responses={429: {"model": models.ErrorResponse}, 409: {"model": models.ErrorResponse}, 500: {"model": models.ErrorResponse}})
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
            delegate = database.add_delegate(models.Delegate(id=uid, firstname=user.firstname, lastname=user.lastname, email=user.email))
        database.add_user(user)

        try:
            await mails.send_verification_email(delegate)
            return JSONResponse(status_code=201, content={"message": "User created successfully. Please verify your email."})
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/login", tags=["Auth"], response_model=models.Token, responses={401: {"model": models.ErrorResponse}, 500: {"model": models.ErrorResponse}})
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

@app.get("/verify_email", tags=["Auth"], status_code=200, responses={500: {"model": models.ErrorResponse}})
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

@app.get("/resend_verification", tags=["Auth"], responses={404: {"model": models.ErrorResponse}, 409: {"model": models.ErrorResponse}, 500: {"model": models.ErrorResponse}})
@limiter.limit("10/minute")
async def resend_verification_email(request: Request, email: models.EmailStr):
    try:
        delegate = database.get_delegate_by_email(email)
        if not delegate:
            raise HTTPException(status_code=404, detail="Delegate not found")
        if delegate.verified:
            raise HTTPException(status_code=409, detail="Email already verified")
        await mails.send_verification_email(delegate)
        return JSONResponse(status_code=200, content={"message": "Verification email sent!"})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/forgot_password", tags=["Auth"], status_code=200, responses={403: {"model": models.ErrorResponse}, 404: {"model": models.ErrorResponse}, 500: {"model": models.ErrorResponse}})
@limiter.limit("1/minute")
async def forgot_password(request: Request, email: models.EmailStr):
    try:
        delegate = database.get_delegate_by_email(email)
        if not delegate:
            raise HTTPException(status_code=404, detail="User not found")
        if not delegate.verified:
            raise HTTPException(status_code=403, detail="User not verified")
        await mails.send_password_reset_email(delegate)
        return JSONResponse(status_code=200, content={"message": "Password reset email sent!"})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.patch("/change_pass", tags=["Auth"], responses={403: {"model": models.ErrorResponse}, 404: {"model": models.ErrorResponse}, 500: {"model": models.ErrorResponse}})
@limiter.limit("1/minute")
def change_password(request: Request, password: str, delegate: models.Delegate | models.Admin = Depends(get_current_user)):
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

@app.get("/hash_password", tags=["Admin"], responses={500: {"model": models.ErrorResponse}})
def get_hashed_password(password: str) -> str:
    try:
        return hash_password(password)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/backup", tags=["Admin"], response_class=FileResponse, responses={403: {"model": models.ErrorResponse}, 500: {"model": models.ErrorResponse}})
def backup_database(user: models.Delegate | models.Admin = Depends(get_current_user)):
    try:
        if type(user) != models.Admin:
            raise HTTPException(status_code=403, detail="Forbidden")
        database.backup_database()
        return FileResponse(os.path.join(os.path.dirname(__file__), "backups", "backup_db.zip"), media_type="application/zip")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/delegates", tags=["Admin"], response_model=list[models.Delegate], responses={403: {"model": models.ErrorResponse}, 404: {"model": models.ErrorResponse}, 500: {"model": models.ErrorResponse}})
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
                writer.writerow(['id', 'firstname', 'lastname', 'email', 'contact', 'dateofbirth', 'gender', 'pastmuns'])

                for delegate in data:
                    past_muns_info = []
                    for mun in delegate.pastmuns:
                        past_muns_info.append(f"{mun.name} | {mun.committee} | {mun.delegation} | {mun.year} | {mun.award}")
                    
                    writer.writerow([delegate.id, delegate.firstname, delegate.lastname, delegate.email, delegate.contact, delegate.dateofbirth, delegate.gender, " ; ".join(past_muns_info)])

                csv_data = output.getvalue()
                output.close()

                return Response(content=csv_data, media_type="text/csv")
            return data
        raise HTTPException(status_code=404, detail="Delegates not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

####################

# DELEGATE STUFF


@app.get("/delegates/me", tags=["Delegates"], response_model=models.Delegate, responses={500: {"model": models.ErrorResponse}})
def get_current_delegate(user: models.Delegate | models.Admin = Depends(get_current_user)):
    try:
        if type(user) == models.Admin:
            raise HTTPException(status_code=500, detail="You are an admin")
        return user
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/delegates/{id}", tags=["Delegates"], response_model=models.Delegate, responses={403: {"model": models.ErrorResponse}, 404: {"model": models.ErrorResponse}, 500: {"model": models.ErrorResponse}})
def get_delegate_by_id(id: str, user: models.Delegate | models.Admin = Depends(get_current_user)):
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

@app.post("/add_delegate", tags=["Delegates"], response_model=models.Delegate, status_code=201, responses={409: {"model": models.ErrorResponse}, 500: {"model": models.ErrorResponse}})
def add_delegate(delegate: models.newDelegate):
    try:
        delegate_exists = database.get_delegate_by_email(delegate.email)
        if delegate_exists:
            raise HTTPException(status_code=409, detail="Delegate already exists")
        uid = str(uuid.uuid4()).replace("-", "")
        new_delegate = models.Delegate(id=uid, firstname=delegate.firstname, lastname=delegate.lastname, email=delegate.email, contact=delegate.contact, dateofbirth=delegate.dateofbirth, gender=delegate.gender, pastmuns=delegate.pastmuns)
        return database.add_delegate(new_delegate)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.patch("/delegates/{id}", tags=["Delegates"], response_model=models.Delegate, responses={403: {"model": models.ErrorResponse}, 404: {"model": models.ErrorResponse}, 500: {"model": models.ErrorResponse}})
def update_delegate(id: str, user: models.Delegate | models.Admin = Depends(get_current_user), firstname: str = "", lastname: str = "", contact: str = "", dateofbirth: str = "", gender: str = "", pastmuns: list[models.MunExperience] = [], verified: bool = False):
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
#REGISTER STUFF
 @router.post("/register", tags=["Auth"], responses={400: {"model": models.ErrorResponse}, 500: {"model": models.ErrorResponse}})
async def register(request: Request, email: str, password: str):
    try:
        # Check if the user already exists in the database
        user = database.get_user_by_email(email)

        if user:
            #chk is user is delegate 
            delegate = database.get_delegate_by_email(user.email)

            # If user exists but is not a delegate, raise an exception
            if not delegate:
                raise HTTPException(status_code=400, detail="User exists but is not a delegate.")

            # If user is a delegate but not verified, send a verification email
            if not delegate.verified:
                await mails.send_verification_email(delegate)
                return JSONResponse(status_code=200, content={"message": "Verification email sent. Please verify your email to continue."})

            # If delegate is found in main database, confirm registration
            if database.check_mm_delegate(user.email):
                return JSONResponse(status_code=200, content={"message": "User already registered and verified as a delegate."})

        else:
           #if doesnt exist create new entries
            new_user = models.User(email=email, password=password)
            database.add_user(new_user)

            
            uid = str(uuid4()).replace("-", "")
            new_delegate = models.Delegate(
                id=uid,
                firstname=new_user.firstname,
                lastname=new_user.lastname,
                email=new_user.email,
                contact=None,
                dateofbirth=None,
                gender=None,
                pastmuns=None,
                verified=False
            )
            database.add_delegate(new_delegate)
            await mails.send_verification_email(new_delegate)

            # Confirm that the user and delegate have been added successfully
            return JSONResponse(status_code=201, content={"message": "User registered successfully. Please verify your email."})

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

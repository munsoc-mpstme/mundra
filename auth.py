import jwt, bcrypt, string, secrets, os
from datetime import datetime, timedelta, timezone
from jwt.exceptions import InvalidTokenError, ExpiredSignatureError
from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
import config, models, database

settings = config.get_settings()

SECRET_KEY = settings.secret_key
ALGORITHM = "HS256"
VERIFICATION_TOKEN_EXPIRE_MINUTES = settings.verification_token_expire_minutes

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/login")

async def get_current_user(token: str = Depends(oauth2_scheme)) -> models.Delegate | models.Admin:
    credentials_exception = HTTPException(
        status_code=403,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email = payload.get("sub")
        if email is None:
            raise credentials_exception
    except InvalidTokenError:
        raise credentials_exception
    admin = database.get_admin_by_email(email)
    if admin:
        return admin
    delegate = database.get_delegate_by_email(email)
    if not delegate:
        raise credentials_exception
    if not delegate.verified:
        raise HTTPException(status_code=401, detail="Please verify your email!")
    return delegate

def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(hours=3)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def create_refresh_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(weeks=1)
    to_encode.update({"exp": expire, "refresh": True})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def create_verification_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=VERIFICATION_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def hash_password(password: str) -> str:
    salt = bcrypt.gensalt()
    hashed_password = bcrypt.hashpw(password.encode('utf-8'), salt)
    return hashed_password.decode('utf-8')

def verify_password(password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(password.encode('utf-8'), hashed_password.encode('utf-8'))

async def check_verification_token(token: str = Depends(oauth2_scheme)) -> models.Delegate:
    credentials_exception = HTTPException(
        status_code=403,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email = payload.get("sub")
        if email is None:
            raise credentials_exception
    except ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Verification token expired")
    except InvalidTokenError:
        raise credentials_exception
    delegate = database.get_delegate_by_email(email)
    if not delegate:
        raise credentials_exception
    return delegate

def generate_password(length: int = 10) -> str:
    characters = string.ascii_letters.replace('l', '').replace('I', '') + string.digits.replace('1', '') + '!@#$%^&*()_+=-'
    password = ''.join(secrets.choice(characters) for _ in range(length))
    return password

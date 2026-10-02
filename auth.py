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
ACCESS_TOKEN_EXPIRE_MINUTES = settings.access_token_expire_minutes

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/login")

async def get_current_user(token: str = Depends(oauth2_scheme)) -> models.AuthUser:
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
        # A distinct 401 so the app can send the user back to the login screen (ADR 0003).
        raise HTTPException(
            status_code=401,
            detail="Session expired, please log in again",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except InvalidTokenError:
        raise credentials_exception
    # The role is read from the database on every request, not from the token, so a
    # demotion or a deleted account takes effect immediately.
    user = await database.get_auth_user(email)
    if not user:
        raise credentials_exception
    if not user.verified:
        raise HTTPException(status_code=401, detail="Please verify your email!")
    return user


def require_role(*roles: str):
    """Dependency that only lets users with one of the given roles through."""

    async def dependency(user: models.AuthUser = Depends(get_current_user)) -> models.AuthUser:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="Forbidden")
        return user

    return dependency


require_admin = require_role("admin")


def require_permission(permission: str):
    """Only let a caller who holds this permission through. An admin (the system role,
    ADR 0002) and a head both pass every check; otherwise the permission must come from
    one of the caller's active team memberships. Read fresh per request (ADR 0003)."""

    async def dependency(
        user: models.AuthUser = Depends(get_current_user),
    ) -> models.AuthUser:
        if user.role == "admin":
            return user
        is_head, perms, _ = await database.get_effective_access(user.email)
        if is_head or permission in perms:
            return user
        raise HTTPException(status_code=403, detail="Forbidden")

    return dependency


async def require_any_oc(
    user: models.AuthUser = Depends(get_current_user),
) -> models.AuthUser:
    """Let through anyone with OC standing: an admin, a head, or a holder of any active
    membership. Used for actions any OC member does on the ground, such as manual_verify."""
    if user.role == "admin":
        return user
    is_head, _, memberships = await database.get_effective_access(user.email)
    if is_head or memberships:
        return user
    raise HTTPException(status_code=403, detail="Forbidden")


async def require_head(
    user: models.AuthUser = Depends(get_current_user),
) -> models.AuthUser:
    """Only a head (or the system admin). For event-wide actions such as setting event
    dates or reading any roster."""
    if user.role == "admin":
        return user
    is_head, _, _ = await database.get_effective_access(user.email)
    if is_head:
        return user
    raise HTTPException(status_code=403, detail="Forbidden")


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    if expires_delta is not None:
        to_encode.update({"exp": datetime.now(timezone.utc) + expires_delta})
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
    delegate = await database.get_delegate_by_email(email)
    if not delegate:
        raise credentials_exception
    return delegate

def generate_password(length: int = 10) -> str:
    characters = string.ascii_letters.replace('l', '').replace('I', '') + string.digits.replace('1', '') + '!@#$%^&*()_+=-'
    password = ''.join(secrets.choice(characters) for _ in range(length))
    return password

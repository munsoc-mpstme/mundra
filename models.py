from pydantic import BaseModel, EmailStr, field_validator
from datetime import datetime, timedelta


class Token(BaseModel):
    access_token: str
    token_type: str


class ErrorResponse(BaseModel):
    error: str


class Admin(BaseModel):
    email: EmailStr
    password: str


class MunExperience(BaseModel):
    name: str
    committee: str = ""
    delegation: str = ""
    year: int
    award: str = ""


class newDelegate(BaseModel):
    firstname: str
    lastname: str
    email: EmailStr
    contact: str = ""
    dateofbirth: str = ""
    gender: str = ""
    pastmuns: list[MunExperience] = []
    verified: bool = False


class Delegate(newDelegate):
    id: str


class User(BaseModel):
    firstname: str
    lastname: str
    email: EmailStr
    password: str

    @field_validator("password")
    def validate_password(cls, v):
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters long")
        return v


# MUMBAI MUN STUFF


class MMDelegate(Delegate):
    country: str = ""
    committee: str = ""
    d1_bf: bool = True
    d1_lunch: bool = False
    d1_hitea: bool = False
    d2_bf: bool = False
    d2_lunch: bool = False
    d2_hitea: bool = False
    d3_bf: bool = False
    d3_lunch: bool = False
    d3_hitea: bool = False

################################################
# Changes related to forget password by Kartik #
################################################

class PasswordResetToken(BaseModel):
    email: EmailStr
    reset_token: str
    expiry: datetime = datetime.now() + timedelta(minutes=15)
from pydantic import BaseModel, EmailStr, field_validator

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


class newMMDelegate(BaseModel):
    del_id: str
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
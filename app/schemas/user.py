from pydantic import BaseModel, EmailStr, ConfigDict


class UserCreate(BaseModel):
    email: EmailStr
    password: str


class UserUpdate(BaseModel):
    password: str


class UserMe(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    email: str

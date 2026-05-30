from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    email: str
    password: str


class UserPublic(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    display_name: str = Field(serialization_alias="displayName")
    email: str
    permissions: list[str]


class UserMePublic(UserPublic):
    team_id: str = Field(serialization_alias="teamId")


class LoginResponseData(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    access_token: str = Field(serialization_alias="accessToken")
    expires_in: int = Field(serialization_alias="expiresIn")
    user: UserPublic


class RefreshResponseData(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    access_token: str = Field(serialization_alias="accessToken")
    expires_in: int = Field(serialization_alias="expiresIn")


class LogoutResponseData(BaseModel):
    success: bool = True

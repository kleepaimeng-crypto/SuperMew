from uuid import UUID

from fastapi import APIRouter, HTTPException, Response

from minimew.account_models.catalog import PRESETS
from minimew.account_models.schemas import (
    AccountSettingsView,
    ModelView,
    ModelWrite,
    PresetView,
    SelectionWrite,
)
from minimew.account_models.service import (
    ModelNotFound,
    delete_model,
    select_model,
    settings_view,
    write_model,
)
from minimew.api.auth import CurrentUser
from minimew.database import SessionDependency
from minimew.model_control import ModelConfigurationUnavailable
from minimew.settings import SettingsDependency

router = APIRouter(prefix="/v1/account/model-settings", tags=["account models"])


@router.get("/presets", response_model=list[PresetView])
async def presets(user: CurrentUser, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return PRESETS


@router.get("", response_model=AccountSettingsView)
async def read(
    user: CurrentUser, session: SessionDependency, settings: SettingsDependency, response: Response
):
    response.headers["Cache-Control"] = "no-store"
    return await settings_view(session, user.id, settings)


@router.put("/selection", status_code=204)
async def selection(data: SelectionWrite, user: CurrentUser, session: SessionDependency):
    try:
        await select_model(session, user.id, data)
    except ModelNotFound:
        raise HTTPException(404, "Model configuration not found") from None


async def save(identity, data, user, session, settings):
    try:
        return await write_model(session, user.id, data, settings, identity)
    except ModelNotFound:
        raise HTTPException(404, "Model configuration not found") from None
    except ModelConfigurationUnavailable:
        raise HTTPException(503, "MODEL_CONFIGURATION_UNAVAILABLE") from None
    except ValueError:
        raise HTTPException(422, "Invalid model configuration") from None


@router.post("/models", response_model=ModelView, status_code=201)
async def create(
    data: ModelWrite, user: CurrentUser, session: SessionDependency, settings: SettingsDependency
):
    return await save(None, data, user, session, settings)


@router.put("/models/{model_id}", response_model=ModelView)
async def edit(
    model_id: UUID,
    data: ModelWrite,
    user: CurrentUser,
    session: SessionDependency,
    settings: SettingsDependency,
):
    return await save(model_id, data, user, session, settings)


@router.delete("/models/{model_id}", status_code=204)
async def remove(model_id: UUID, user: CurrentUser, session: SessionDependency):
    try:
        await delete_model(session, user.id, model_id)
    except ModelNotFound:
        raise HTTPException(404, "Model configuration not found") from None

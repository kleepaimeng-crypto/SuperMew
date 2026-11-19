from fastapi import APIRouter, Depends, HTTPException

from minimew.api.auth import CurrentUser
from minimew.api.threads import no_store
from minimew.skills.catalog import SkillUnavailable, catalog
from minimew.skills.schemas import SkillSummary

router = APIRouter(prefix="/v1/skills", tags=["skills"], dependencies=[Depends(no_store)])


@router.get("", response_model=list[SkillSummary])
async def list_skills(user: CurrentUser):
    try:
        return catalog()
    except SkillUnavailable:
        raise HTTPException(503, detail={"code": "SKILL_CATALOG_UNAVAILABLE"}) from None

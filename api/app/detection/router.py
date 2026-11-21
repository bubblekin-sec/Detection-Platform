from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.detection.kql import to_defender_xdr_kql
from app.detection.sigma import parse_sigma

router = APIRouter(prefix="/api/detection", tags=["detection"])

MAX_SIGMA_BYTES = 64 * 1024


class ConvertRequest(BaseModel):
    sigma_yaml: str = Field(max_length=MAX_SIGMA_BYTES)


class ConvertResponse(BaseModel):
    sigma_valid: bool
    kql: str | None
    kql_error: str | None


@router.post("/convert", response_model=ConvertResponse)
def convert(request: ConvertRequest) -> ConvertResponse:
    parsed = parse_sigma(request.sigma_yaml)
    if parsed.rule is None:
        raise HTTPException(status_code=422, detail={"errors": parsed.errors})
    result = to_defender_xdr_kql(parsed.rule)
    return ConvertResponse(sigma_valid=True, kql=result.kql, kql_error=result.error)

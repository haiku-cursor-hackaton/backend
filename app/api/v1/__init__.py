from fastapi import APIRouter

from app.api.v1 import payment_authorizations

v1_router = APIRouter()
v1_router.include_router(payment_authorizations.router)

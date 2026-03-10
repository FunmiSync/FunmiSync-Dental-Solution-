from fastapi import APIRouter, Depends
from core.database import get_db
from sqlalchemy.orm import Session
from core.models import Users, RoleAssignment, ScopeType, Dso, RegisteredClinics
from core.schemas import my_workspaces_out
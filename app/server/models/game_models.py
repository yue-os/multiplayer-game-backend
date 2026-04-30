from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class VisibleRole(str, Enum):
    STUDENT = "Student"
    DOCTOR = "Doctor"
    VENDOR = "Vendor"
    CARETAKER = "Caretaker"
    GUARD = "Guard"


class ItemType(str, Enum):
    SNACKS = "Snacks"
    MEDICINES = "Medicines"
    MASKS = "Masks"
    SCHOOL_SUPPLIES = "School Supplies"


class LocationEvent(str, Enum):
    SCHOOL = "School"
    PARK = "Park"
    CANTEEN = "Canteen"
    CLINIC = "Clinic"
    MARKET = "Market"


class HealthStatus(str, Enum):
    HEALTHY = "Healthy"
    EXPOSED = "Exposed"
    INFECTED = "Infected"


class PlayerState(BaseModel):
    player_id: str
    visible_role: VisibleRole
    is_carrier: bool = False
    inventory: dict[ItemType, int] = Field(default_factory=dict)
    health_status: HealthStatus = HealthStatus.HEALTHY
    mission_completed: bool = False


class GameState(BaseModel):
    lobby_id: str
    current_event: LocationEvent
    players: list[PlayerState] = Field(default_factory=list)
    lockdown_meter: int = Field(ge=0)
    current_round: int = 0
    max_rounds: int = 10

from pydantic import BaseModel, Field
from typing import Optional

class UserMetadataRequest(BaseModel):
    user_id: str = Field(..., description="Unique User ID from betting site")
    
    # Fingerprints (Optional because sometimes a user might be on a new device/incognito)
    device_hash: Optional[str] = Field(None, description="Unique Device ID or Hash")
    ip_address: Optional[str] = Field(None, description="User's IP Address")
    canvas_hash: Optional[str] = Field(None, description="Browser Canvas Fingerprint")

    class Config:
        json_schema_extra = {
            "example": {
                "user_id": "raju_bhai",
                "device_hash": "android_uuid_550e8400-e29b",
                "ip_address": "192.168.1.1",
                "canvas_hash": "a1b2c3d4e5f6"
            }
        }

class IngestionResponse(BaseModel):
    status: str
    message: str


class EntityRiskResponse(BaseModel):
    user_id: str
    is_flagged: bool
    risk_score: float  # 0.0 to 1.0
    cluster_id: Optional[str] = None
    cluster_size: int = 0
    reason: Optional[str] = None

    class Config:
        json_schema_extra = {
            "example": {
                "user_id": "raju_bhai",
                "is_flagged": True,
                "risk_score": 0.95,
                "cluster_id": "Ring-shyam_lal",
                "cluster_size": 2,
                "reason": "Linked to High-Risk Cluster"
            }
        }
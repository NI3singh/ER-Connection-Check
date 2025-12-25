from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime

# ============================================================================
# INPUT SCHEMAS
# ============================================================================

class UserMetadataRequest(BaseModel):
    """Request for ingesting user metadata"""
    user_id: str = Field(..., description="Unique User ID from betting site")
    device_hash: Optional[str] = Field(None, description="Unique Device ID or Hash")
    ip_address: Optional[str] = Field(None, description="User's IP Address")
    canvas_hash: Optional[str] = Field(None, description="Browser Canvas Fingerprint")

    class Config:
        json_schema_extra = {
            "example": {
                "user_id": "user_12345",
                "device_hash": "android_550e8400-e29b",
                "ip_address": "103.45.67.89",
                "canvas_hash": "a1b2c3d4e5f6"
            }
        }


# ============================================================================
# OUTPUT SCHEMAS
# ============================================================================

class ClusterReviewRequest(BaseModel):
    """Request for manual cluster review"""
    decision: str = Field(..., pattern="^(CONFIRMED|FALSE_POSITIVE)$")
    reason: Optional[str] = Field(None, description="Reason for this decision")
    reviewer_id: Optional[str] = Field(None, description="Reviewer identifier")
    
    class Config:
        json_schema_extra = {
            "example": {
                "decision": "FALSE_POSITIVE",
                "reason": "Family members using same WiFi",
                "reviewer_id": "compliance_officer_123"
            }
        }

class IngestionResponse(BaseModel):
    """Response after metadata ingestion"""
    status: str
    message: str

    class Config:
        json_schema_extra = {
            "example": {
                "status": "success",
                "message": "Metadata created and queued for analysis"
            }
        }


class EntityRiskResponse(BaseModel):
    """Enhanced risk response with GARG-AML metrics"""
    user_id: str
    is_flagged: bool
    risk_score: float = Field(..., ge=0.0, le=1.0)
    risk_level: str = Field(..., description="SAFE, LOW, MEDIUM, HIGH, CRITICAL")
    cluster_id: Optional[str] = None
    cluster_size: int = 0
    density_score: Optional[float] = Field(None, ge=0.0, le=1.0)
    confidence_score: Optional[float] = Field(None, ge=0.0, le=1.0)
    reason: str

    class Config:
        json_schema_extra = {
            "example": {
                "user_id": "user_12345",
                "is_flagged": True,
                "risk_score": 0.92,
                "risk_level": "CRITICAL",
                "cluster_id": "Ring-user_67890",
                "cluster_size": 5,
                "density_score": 0.85,
                "confidence_score": 0.88,
                "reason": "User linked to CRITICAL risk cluster: Ring-user_67890"
            }
        }


class ClusterDetailsResponse(BaseModel):
    """Detailed cluster information for compliance review"""
    cluster_id: str
    size: int
    risk_score: float
    density_score: float
    confidence_score: float
    formation_date: datetime
    review_status: str
    members: List[Dict[str, Any]]
    connections: List[Dict[str, Any]]

    class Config:
        json_schema_extra = {
            "example": {
                "cluster_id": "Ring-user_67890",
                "size": 5,
                "risk_score": 0.92,
                "density_score": 0.85,
                "confidence_score": 0.88,
                "formation_date": "2025-12-10T10:30:00Z",
                "review_status": "PENDING",
                "members": [
                    {"user_id": "user_123", "risk_score": 0.92},
                    {"user_id": "user_456", "risk_score": 0.90}
                ],
                "connections": [
                    {
                        "user_a": "user_123",
                        "user_b": "user_456",
                        "confidence": 0.95,
                        "link_type": "PRIMARY",
                        "shared_signals": "[\"DEVICE\", \"CANVAS\"]"
                    }
                ]
            }
        }


class SystemHealthResponse(BaseModel):
    """System health and statistics"""
    status: str
    timestamp: str
    version: str
    worker_status: str
    last_analysis: Optional[str] = None
    statistics: Dict[str, Any] = {}

    class Config:
        json_schema_extra = {
            "example": {
                "status": "healthy",
                "timestamp": "2025-12-10T14:30:00Z",
                "version": "2.0.0",
                "worker_status": "IDLE",
                "last_analysis": "2025-12-10T14:29:00Z",
                "statistics": {
                    "total_metadata_records": 1523,
                    "active_entity_links": 234,
                    "active_clusters": 12,
                    "high_risk_clusters": 3,
                    "last_processed_id": 1523
                }
            }
        }
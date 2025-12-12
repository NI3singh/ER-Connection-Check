from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import UserMetadata, SmurfCluster
from app.schemas import UserMetadataRequest, IngestionResponse, EntityRiskResponse
import logging

# Configure Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="AML System - Entity Resolution Module (The Spider)")

@app.post("/api/v1/ingest/user-metadata", response_model=IngestionResponse)
def ingest_user_metadata(request: UserMetadataRequest, db: Session = Depends(get_db)):
    """
    Ingest user fingerprints (Device ID, IP, Canvas) for background analysis.
    This endpoint is FAST (Async Architecture). It just saves data.
    """
    try:
        # 1. Create the Metadata Record
        metadata_entry = UserMetadata(
            user_id=request.user_id,
            device_hash=request.device_hash,
            ip_address=request.ip_address,
            canvas_hash=request.canvas_hash
        )
        
        # 2. Save to "Memory" (Postgres)
        db.add(metadata_entry)
        db.commit()
        db.refresh(metadata_entry)
        
        logger.info(f"Metadata saved for user: {request.user_id}")
        
        return IngestionResponse(
            status="success",
            message="Metadata queued for analysis"
        )

    except Exception as e:
        logger.error(f"Ingestion failed: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")

@app.get("/api/v1/user/{user_id}/risk", response_model=EntityRiskResponse)
def get_user_entity_risk(user_id: str, db: Session = Depends(get_db)):
    """
    Check if a user is part of a detected Smurf Ring.
    Used by the betting site before processing withdrawals.
    """
    # 1. Check if user is in a known cluster
    cluster_record = db.query(SmurfCluster).filter(
        SmurfCluster.user_id == user_id, 
        SmurfCluster.is_active == True
    ).first()

    if cluster_record:
        # User is caught in a ring!
        return EntityRiskResponse(
            user_id=user_id,
            is_flagged=True,
            risk_score=cluster_record.risk_score,
            cluster_id=cluster_record.cluster_id,
            cluster_size=2, # In V2 (GARG-AML), we will query actual size. For now, it exists = >1
            reason=f"User is linked to Smurf Cluster: {cluster_record.cluster_id}"
        )
    
    # 2. User is clean (for now)
    return EntityRiskResponse(
        user_id=user_id,
        is_flagged=False,
        risk_score=0.0,
        cluster_id=None,
        cluster_size=0,
        reason="No known entity links found"
    )

@app.get("/")
def home():
    return {"message": "Entity Resolution Spider is Active"}
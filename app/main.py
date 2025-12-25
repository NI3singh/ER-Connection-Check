from fastapi import FastAPI, Depends, HTTPException, Query, Request, Response
from sqlalchemy.orm import Session
from sqlalchemy import func, and_
from app.database import get_db, init_database
from app.models import UserMetadata, SmurfCluster, EntityLink, SignalStrength, AnalysisState
from app.schemas import (
    UserMetadataRequest, 
    IngestionResponse, 
    EntityRiskResponse,
    ClusterDetailsResponse,
    SystemHealthResponse,
    ClusterReviewRequest
)
from datetime import datetime, timezone
import logging
from app.rate_limiter import limiter, rate_limit_exceeded_handler
from slowapi.middleware import SlowAPIMiddleware
from slowapi.errors import RateLimitExceeded

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="AML Entity Resolution Module (GARG-AML Enhanced)",
    version="2.0.0",
    description="Multi-layered graph-based fraud detection with confidence scoring"
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# Auto-create tables on startup
@app.on_event("startup")
def startup_event():
    """Initialize database tables on API startup"""
    logger.info("Checking database tables...")
    init_database()


# ============================================================================
# ENDPOINT 1: METADATA INGESTION (with UPSERT)
# ============================================================================

@app.post("/api/v1/ingest/user-metadata", response_model=IngestionResponse)
@limiter.limit("100/minute")
def ingest_user_metadata(request_data: UserMetadataRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    """
    Ingest user fingerprints with atomic UPSERT to prevent race conditions.
    """
    try:
        from sqlalchemy.dialects.postgresql import insert
        
        # Prepare values
        values = {
            'user_id': request_data.user_id,
            'device_hash': request_data.device_hash or 'NONE',
            'ip_address': request_data.ip_address or 'NONE',
            'canvas_hash': request_data.canvas_hash or 'NONE',
            'occurrence_count': 1,
            'first_seen': datetime.now(timezone.utc),
            'last_seen': datetime.now(timezone.utc)
        }
        
        # Atomic UPSERT using PostgreSQL's ON CONFLICT
        stmt = insert(UserMetadata).values(**values)
        stmt = stmt.on_conflict_do_update(
            constraint='uq_user_fingerprint',
            set_={
                'occurrence_count': UserMetadata.occurrence_count + 1,
                'last_seen': datetime.now(timezone.utc)
            }
        )
        
        result = db.execute(stmt)
        db.commit()
        
        # Check if it was insert or update
        if result.rowcount > 0:
            action = "created or updated"
            logger.info(f"Metadata ingested for user: {request_data.user_id}")
        
        return IngestionResponse(
            status="success",
            message=f"Metadata {action} and queued for analysis"
        )

    except Exception as e:
        logger.error(f"Ingestion failed: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Internal Server Error")

# ============================================================================
# ENDPOINT 2: USER RISK CHECK 
# ============================================================================

@app.get("/api/v1/user/{user_id}/risk", response_model=EntityRiskResponse)
def get_user_entity_risk(user_id: str, db: Session = Depends(get_db)):
    """
    Check if user is part of a detected smurf ring.
    Uses GARG-AML risk scoring for graduated response.
    """
    # Query active clusters
    cluster_records = db.query(SmurfCluster).filter(
        SmurfCluster.user_id == user_id,
        SmurfCluster.is_active == True
    ).all()
    
    if not cluster_records:
        return EntityRiskResponse(
            user_id=user_id,
            is_flagged=False,
            risk_score=0.0,
            risk_level="SAFE",
            cluster_id=None,
            cluster_size=0,
            reason="No entity links detected"
        )
    
    # User is in one or more clusters (take highest risk)
    highest_risk_cluster = max(cluster_records, key=lambda c: c.risk_score)
    
    # Determine risk level
    risk_score = highest_risk_cluster.risk_score
    if risk_score >= 0.85:
        risk_level = "CRITICAL"
        is_flagged = True
    elif risk_score >= 0.70:
        risk_level = "HIGH"
        is_flagged = True
    elif risk_score >= 0.50:
        risk_level = "MEDIUM"
        is_flagged = True
    else:
        risk_level = "LOW"
        is_flagged = False
    
    return EntityRiskResponse(
        user_id=user_id,
        is_flagged=is_flagged,
        risk_score=risk_score,
        risk_level=risk_level,
        cluster_id=highest_risk_cluster.cluster_id,
        cluster_size=highest_risk_cluster.cluster_size,
        density_score=highest_risk_cluster.density_score,
        confidence_score=highest_risk_cluster.confidence_score,
        reason=f"User linked to {risk_level} risk cluster: {highest_risk_cluster.cluster_id}"
    )


# ============================================================================
# ENDPOINT 3: CLUSTER DETAILS (New)
# ============================================================================

@app.get("/api/v1/cluster/{cluster_id}", response_model=ClusterDetailsResponse)
def get_cluster_details(cluster_id: str, db: Session = Depends(get_db)):
    """
    Get detailed information about a specific cluster.
    Used by compliance officers for investigation.
    """
    members = db.query(SmurfCluster).filter(
        SmurfCluster.cluster_id == cluster_id,
        SmurfCluster.is_active == True
    ).all()
    
    if not members:
        raise HTTPException(status_code=404, detail="Cluster not found")
    
    # Get all user IDs in cluster
    user_ids = [m.user_id for m in members]
    
    # Get entity links within cluster
    links = db.query(EntityLink).filter(
        EntityLink.user_a.in_(user_ids),
        EntityLink.user_b.in_(user_ids),
        EntityLink.is_active == True
    ).all()
    
    # Format response
    representative = members[0]
    
    return ClusterDetailsResponse(
        cluster_id=cluster_id,
        size=representative.cluster_size,
        risk_score=representative.risk_score,
        density_score=representative.density_score,
        confidence_score=representative.confidence_score,
        formation_date=representative.formation_date,
        review_status=representative.review_status,
        members=[
            {
                "user_id": m.user_id,
                "risk_score": m.risk_score
            }
            for m in members
        ],
        connections=[
            {
                "user_a": link.user_a,
                "user_b": link.user_b,
                "confidence": link.adjusted_confidence,
                "link_type": link.link_type,
                "shared_signals": link.shared_signals
            }
            for link in links
        ]
    )


# ============================================================================
# ENDPOINT 4: COMPLIANCE DASHBOARD
# ============================================================================

@app.get("/api/v1/compliance/clusters")
def get_flagged_clusters(
    min_risk: float = Query(0.7, ge=0.0, le=1.0),
    status: str = Query("PENDING"),
    limit: int = Query(50, le=200),
    db: Session = Depends(get_db)
):
    """
    Get clusters for compliance review.
    Filters by risk score and review status.
    """
    # Get unique clusters meeting criteria
    clusters = db.query(
        SmurfCluster.cluster_id,
        func.max(SmurfCluster.risk_score).label('max_risk'),
        func.max(SmurfCluster.cluster_size).label('size'),
        func.max(SmurfCluster.formation_date).label('formed')
    ).filter(
        SmurfCluster.is_active == True,
        SmurfCluster.risk_score >= min_risk,
        SmurfCluster.review_status == status
    ).group_by(
        SmurfCluster.cluster_id
    ).order_by(
        func.max(SmurfCluster.risk_score).desc()
    ).limit(limit).all()
    
    return {
        "count": len(clusters),
        "filters": {
            "min_risk": min_risk,
            "status": status
        },
        "clusters": [
            {
                "cluster_id": c.cluster_id,
                "risk_score": c.max_risk,
                "size": c.size,
                "formation_date": c.formed.isoformat()
            }
            for c in clusters
        ]
    }


# ============================================================================
# ENDPOINT 5: SYSTEM HEALTH
# ============================================================================

@app.get("/api/v1/health", response_model=SystemHealthResponse)
def health_check(db: Session = Depends(get_db)):
    """
    System health and statistics.
    """
    try:
        # Get analysis state
        state = db.query(AnalysisState).first()
        
        # Get statistics
        total_metadata = db.query(func.count(UserMetadata.id)).scalar()
        total_links = db.query(func.count(EntityLink.id)).filter(
            EntityLink.is_active == True
        ).scalar()
        total_clusters = db.query(func.count(func.distinct(SmurfCluster.cluster_id))).filter(
            SmurfCluster.is_active == True
        ).scalar()
        
        high_risk_clusters = db.query(func.count(func.distinct(SmurfCluster.cluster_id))).filter(
            SmurfCluster.is_active == True,
            SmurfCluster.risk_score >= 0.85
        ).scalar()
        
        return SystemHealthResponse(
            status="healthy",
            timestamp=datetime.now(timezone.utc).isoformat(),
            version="2.0.0",
            worker_status=state.worker_status if state else "UNKNOWN",
            last_analysis=state.last_analysis_time.isoformat() if state else None,
            statistics={
                "total_metadata_records": total_metadata,
                "active_entity_links": total_links,
                "active_clusters": total_clusters,
                "high_risk_clusters": high_risk_clusters,
                "last_processed_id": state.last_processed_metadata_id if state else 0
            }
        )
        
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return SystemHealthResponse(
            status="degraded",
            timestamp=datetime.now(timezone.utc).isoformat(),
            version="2.0.0",
            worker_status="ERROR",
            statistics={}
        )


# ============================================================================
# ENDPOINT 6: MANUAL REVIEW (Compliance Tool)
# ============================================================================

@app.post("/api/v1/compliance/review/{cluster_id}")
def review_cluster(
    cluster_id: str,
    request: ClusterReviewRequest = ...,  # Now using schema
    db: Session = Depends(get_db)
):
    """
    Mark cluster as reviewed by compliance officer with audit trail.
    """
    from app.models import ClusterReviewAudit
    from fastapi import Request as FastAPIRequest
    
    # Get current status before update
    current_cluster = db.query(SmurfCluster).filter(
        SmurfCluster.cluster_id == cluster_id
    ).first()
    
    if not current_cluster:
        raise HTTPException(status_code=404, detail="Cluster not found")
    
    previous_status = current_cluster.review_status
    
    # Update cluster status
    updated = db.query(SmurfCluster).filter(
        SmurfCluster.cluster_id == cluster_id
    ).update({
        "review_status": request.decision,
        "manually_reviewed": True
    })
    
    if updated == 0:
        raise HTTPException(status_code=404, detail="Cluster not found")
    
    # Create audit record
    audit = ClusterReviewAudit(
        cluster_id=cluster_id,
        reviewer_id=request.reviewer_id,
        reviewer_ip=None,  # Could extract from FastAPI Request if needed
        previous_status=previous_status,
        new_status=request.decision,
        reason=request.reason
    )
    db.add(audit)
    
    db.commit()
    
    logger.info(f"Cluster {cluster_id} reviewed: {previous_status} â†’ {request.decision} by {request.reviewer_id}")
    
    return {
        "status": "success",
        "cluster_id": cluster_id,
        "decision": request.decision,
        "previous_status": previous_status,
        "message": f"Cluster marked as {request.decision}"
    }


@app.get("/api/v1/compliance/audit/{cluster_id}")
def get_cluster_audit_history(cluster_id: str, db: Session = Depends(get_db)):
    """
    Get full audit history for a cluster.
    Shows all review decisions made over time.
    """
    from app.models import ClusterReviewAudit
    
    audits = db.query(ClusterReviewAudit).filter(
        ClusterReviewAudit.cluster_id == cluster_id
    ).order_by(ClusterReviewAudit.reviewed_at.desc()).all()
    
    return {
        "cluster_id": cluster_id,
        "audit_count": len(audits),
        "history": [
            {
                "reviewer_id": a.reviewer_id,
                "previous_status": a.previous_status,
                "new_status": a.new_status,
                "reason": a.reason,
                "reviewed_at": a.reviewed_at.isoformat()
            }
            for a in audits
        ]
    }


# ============================================================================
# ROOT
# ============================================================================

@app.get("/")
def home():
    return {
        "message": "Entity Resolution Engine (GARG-AML Enhanced)",
        "version": "2.0.0",
        "status": "operational",
        "features": [
            "Multi-layered confidence scoring",
            "Temporal decay analysis",
            "Signal strength weighting",
            "GARG-AML density metrics",
            "Incremental graph processing"
        ],
        "endpoints": {
            "ingest": "POST /api/v1/ingest/user-metadata",
            "risk_check": "GET /api/v1/user/{user_id}/risk",
            "cluster_details": "GET /api/v1/cluster/{cluster_id}",
            "compliance_dashboard": "GET /api/v1/compliance/clusters",
            "manual_review": "POST /api/v1/compliance/review/{cluster_id}",
            "health": "GET /api/v1/health"
        }
    }
from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, Index, UniqueConstraint
from sqlalchemy.sql import func
from app.db_base import Base

# ============================================================================
# LAYER 1: RAW SIGNALS (Evidence Collection)
# ============================================================================

class UserMetadata(Base):
    """
    Stores unique user-fingerprint combinations (UPSERT by composite key).
    One row per unique combination to prevent duplicates.
    """
    __tablename__ = "user_metadata"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, nullable=False)
    
    # Digital fingerprints
    device_hash = Column(String, nullable=True)
    ip_address = Column(String, nullable=True)
    canvas_hash = Column(String, nullable=True)
    
    # Temporal tracking
    first_seen = Column(DateTime(timezone=True), server_default=func.now())
    last_seen = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    occurrence_count = Column(Integer, default=1)  # How many times this combo seen
    
    # CRITICAL: Composite unique constraint prevents duplicates
    __table_args__ = (
        UniqueConstraint('user_id', 'device_hash', 'ip_address', 'canvas_hash', 
                        name='uq_user_fingerprint'),
        Index('idx_user_metadata_user_id', 'user_id'),
        Index('idx_user_metadata_device_hash', 'device_hash'),
        Index('idx_user_metadata_ip_address', 'ip_address'),
        Index('idx_user_metadata_canvas_hash', 'canvas_hash'),
        Index('idx_user_metadata_last_seen', 'last_seen'),
    )

    def __repr__(self):
        return f"<Metadata(user={self.user_id}, device={self.device_hash}, seen={self.occurrence_count}x)>"


# ============================================================================
# LAYER 2: SIGNAL AGGREGATION (Confidence Scoring)
# ============================================================================

class SignalStrength(Base):
    """
    Stores computed signal strengths between fingerprint pairs.
    Used for confidence-weighted graph construction.
    """
    __tablename__ = "signal_strengths"

    id = Column(Integer, primary_key=True, index=True)
    
    # What type of signal (DEVICE, IP, CANVAS)
    signal_type = Column(String, nullable=False, index=True)
    signal_value = Column(String, nullable=False)  # The actual hash/IP
    
    # How many users share this signal
    user_count = Column(Integer, default=0)
    
    # Confidence weight (0.0 - 1.0)
    # Low user_count = High confidence
    # High user_count = Low confidence (e.g., public WiFi)
    confidence_weight = Column(Float, default=1.0)
    
    # Last recalculation time
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    __table_args__ = (
        UniqueConstraint('signal_type', 'signal_value', name='uq_signal'),
        Index('idx_signal_strengths_signal_type', 'signal_type'),
    )

    def __repr__(self):
        return f"<Signal({self.signal_type}:{self.signal_value}, users={self.user_count}, conf={self.confidence_weight})>"


# ============================================================================
# LAYER 3: ENTITY LINKS (Weighted Connections)
# ============================================================================

class EntityLink(Base):
    """
    Stores weighted relationships between users with temporal decay.
    """
    __tablename__ = "entity_links"

    id = Column(Integer, primary_key=True, index=True)
    
    user_a = Column(String, nullable=False)
    user_b = Column(String, nullable=False)
    
    # Multi-signal tracking
    shared_signals = Column(String, nullable=False)  # JSON: ["DEVICE", "CANVAS"]
    link_type = Column(String, nullable=False)  # PRIMARY, SECONDARY, TERTIARY
    
    # Confidence scoring
    raw_confidence = Column(Float, default=0.0)      # Before temporal decay
    adjusted_confidence = Column(Float, default=0.0)  # After temporal decay
    
    # Temporal tracking
    first_linked = Column(DateTime(timezone=True), server_default=func.now())
    last_updated = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    # Status
    is_active = Column(Boolean, default=True)  # False if connection too old
    
    __table_args__ = (
        UniqueConstraint('user_a', 'user_b', name='uq_user_pair'),
        Index('idx_entity_links_user_pair', 'user_a', 'user_b'),
        Index('idx_entity_links_confidence', 'adjusted_confidence'),
        Index('idx_entity_links_active', 'is_active'),
    )

    def __repr__(self):
        return f"<Link({self.user_a} <-> {self.user_b}, conf={self.adjusted_confidence:.2f}, type={self.link_type})>"


# ============================================================================
# LAYER 4: CLUSTER DETECTION (Ring Identification)
# ============================================================================

class SmurfCluster(Base):
    """
    Stores detected fraud rings with GARG-AML density scoring.
    """
    __tablename__ = "smurf_clusters"

    id = Column(Integer, primary_key=True, index=True)
    
    cluster_id = Column(String, nullable=False, index=True)
    user_id = Column(String, nullable=False)
    
    # GARG-AML Risk Metrics
    cluster_size = Column(Integer, default=0)           # Number of members
    density_score = Column(Float, default=0.0)          # Graph density (0-1)
    confidence_score = Column(Float, default=0.0)       # Average link confidence
    risk_score = Column(Float, default=0.0)             # Final composite score
    
    # Cluster characteristics
    shared_signals = Column(String, nullable=True)      # JSON: signal types shared
    formation_date = Column(DateTime(timezone=True), server_default=func.now())
    
    # Status management
    is_active = Column(Boolean, default=True)
    manually_reviewed = Column(Boolean, default=False)  # For compliance team
    review_status = Column(String, default="PENDING")   # PENDING, CONFIRMED, FALSE_POSITIVE
    
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    __table_args__ = (
        UniqueConstraint('cluster_id', 'user_id', name='uq_cluster_member'),
        Index('idx_smurf_clusters_cluster_id', 'cluster_id'),
        Index('idx_smurf_clusters_user_id', 'user_id'),
        Index('idx_smurf_clusters_risk_score', 'risk_score'),
        Index('idx_smurf_clusters_active', 'is_active'),
        Index('idx_smurf_clusters_review_status', 'review_status'),
    )

    def __repr__(self):
        return f"<Cluster({self.cluster_id}, user={self.user_id}, risk={self.risk_score:.2f}, status={self.review_status})>"


# ============================================================================
# LAYER 5: ANALYSIS STATE (Worker Management)
# ============================================================================

class AnalysisState(Base):
    """
    Tracks worker processing state for incremental updates.
    """
    __tablename__ = "analysis_state"

    id = Column(Integer, primary_key=True)
    
    # Incremental processing
    last_processed_metadata_id = Column(Integer, default=0)
    last_analysis_time = Column(DateTime(timezone=True), server_default=func.now())
    
    # Performance metrics
    metadata_processed = Column(Integer, default=0)
    clusters_detected = Column(Integer, default=0)
    analysis_duration_seconds = Column(Float, default=0.0)
    
    # Status
    worker_status = Column(String, default="IDLE")  # IDLE, RUNNING, ERROR
    error_message = Column(String, nullable=True)
    
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<State(last_id={self.last_processed_metadata_id}, status={self.worker_status})>"
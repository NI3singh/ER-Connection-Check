from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, Index
from sqlalchemy.sql import func
from app.database import Base

# 1. RAW METADATA (The Evidence)
# Stores raw fingerprints sent by the betting site.
class UserMetadata(Base):
    __tablename__ = "user_metadata"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, index=True, nullable=False)
    
    # The fingerprints used to link users
    device_hash = Column(String, index=True, nullable=True)   # Unique Device ID
    ip_address = Column(String, index=True, nullable=True)    # IP Address
    canvas_hash = Column(String, index=True, nullable=True)   # Browser Fingerprint
    
    timestamp = Column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self):
        return f"<Metadata(user={self.user_id}, device={self.device_hash})>"

# 2. ENTITY LINKS (The Spiderweb Edges)
# Stores the connections found by our worker (e.g. Raju <-> Babu).
class EntityLink(Base):
    __tablename__ = "entity_links"

    id = Column(Integer, primary_key=True, index=True)
    
    # The two users being linked
    user_a = Column(String, index=True, nullable=False)
    user_b = Column(String, index=True, nullable=False)
    
    # Reason for the link (e.g., "SHARED_DEVICE_HASH")
    link_type = Column(String, nullable=False) 
    confidence_score = Column(Float, default=1.0) # 1.0 = Perfect Match
    
    timestamp = Column(DateTime(timezone=True), server_default=func.now())

    # Fast lookup to find all connections for a specific user pair
    __table_args__ = (
        Index('idx_user_pair', 'user_a', 'user_b'),
    )

    def __repr__(self):
        return f"<Link({self.user_a} <-> {self.user_b} via {self.link_type})>"

# 3. SMURF CLUSTERS (The Verdict)
# Stores the identified rings (e.g. "Cluster #101 contains 5 users")
class SmurfCluster(Base):
    __tablename__ = "smurf_clusters"

    id = Column(Integer, primary_key=True, index=True)
    
    # A unique ID for the ring (e.g., UUID)
    cluster_id = Column(String, index=True, nullable=False)
    
    # The user belonging to this ring
    user_id = Column(String, index=True, nullable=False)
    
    # GARG Density Score (How tightly connected is this ring?)
    risk_score = Column(Float, default=0.0) 
    
    # Is this ring currently active?
    is_active = Column(Boolean, default=True) 
    
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())

    def __repr__(self):
        return f"<Cluster({self.cluster_id} -> {self.user_id}, Score={self.risk_score})>"
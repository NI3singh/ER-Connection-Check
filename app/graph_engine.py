from importlib.metadata import metadata
import networkx as nx
from app.models import FailedMetadataProcessing, UserMetadata, EntityLink, SmurfCluster, SignalStrength, AnalysisState
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timedelta, timezone
import logging
import json

logger = logging.getLogger(__name__)

# ============================================================================
# GARG-AML CONFIGURATION
# ============================================================================

class GARGConfig:
    """GARG-AML Inspired Configuration"""
    
    # Signal Confidence Weights (Base)
    SIGNAL_WEIGHTS = {
        'DEVICE': 0.95,   # Device hash is very strong signal
        'CANVAS': 0.85,   # Canvas fingerprint is strong
        'IP': 0.30        # IP is weak (shared networks)
    }
    
    # Confidence Thresholds
    MIN_LINK_CONFIDENCE = 0.70      # Minimum to create link
    HIGH_CONFIDENCE_THRESHOLD = 0.85
    
    # Temporal Decay
    CONNECTION_MAX_AGE_DAYS = 180   # 6 months
    DECAY_START_DAYS = 90           # Start decay after 90 days
    
    # IP Sharing Thresholds
    MAX_USERS_PER_IP = 10           # If >10 users share IP, reduce confidence
    PUBLIC_IP_THRESHOLD = 50        # If >50 users, treat as public WiFi
    
    # Cluster Detection
    MIN_CLUSTER_SIZE = 2
    MIN_CLUSTER_DENSITY = 0.5       # 50% of possible connections must exist
    
    # Risk Scoring
    RISK_WEIGHTS = {
        'cluster_size': 0.30,
        'density': 0.40,
        'confidence': 0.30
    }


# ============================================================================
# LAYER 1: SIGNAL STRENGTH CALCULATOR
# ============================================================================

class SignalAnalyzer:
    """Analyzes signal strengths and calculates confidence weights"""
    
    def __init__(self, db: Session):
        self.db = db
        self.config = GARGConfig()
    
    def update_signal_strengths(self):
        """
        Recalculates confidence weights for all signals based on usage patterns.
        Low usage = High confidence, High usage = Low confidence
        """
        logger.info("Updating signal strength weights...")
        
        for signal_type in ['DEVICE', 'CANVAS', 'IP']:
            col_name = signal_type.lower() + '_hash' if signal_type != 'IP' else 'ip_address'
            
            # Query: Group by signal and count users
            results = self.db.query(
                getattr(UserMetadata, col_name).label('signal_value'),
                func.count(func.distinct(UserMetadata.user_id)).label('user_count')
            ).filter(
                getattr(UserMetadata, col_name).isnot(None)
            ).group_by(
                getattr(UserMetadata, col_name)
            ).all()
            
            for signal_value, user_count in results:
                # Calculate confidence weight
                base_weight = self.config.SIGNAL_WEIGHTS[signal_type]
                
                if signal_type == 'IP':
                    # Special handling for IPs
                    if user_count >= self.config.PUBLIC_IP_THRESHOLD:
                        weight = 0.05  # Public WiFi - almost no confidence
                    elif user_count >= self.config.MAX_USERS_PER_IP:
                        weight = 0.15  # Shared network - low confidence
                    else:
                        weight = base_weight
                else:
                    # Device/Canvas: Reduce weight if shared by many users
                    if user_count > 5:
                        weight = base_weight * (1 / (1 + user_count * 0.1))
                    else:
                        weight = base_weight
                
                # UPSERT signal strength
                existing = self.db.query(SignalStrength).filter_by(
                    signal_type=signal_type,
                    signal_value=signal_value
                ).first()
                
                if existing:
                    existing.user_count = user_count
                    existing.confidence_weight = weight
                else:
                    self.db.add(SignalStrength(
                        signal_type=signal_type,
                        signal_value=signal_value,
                        user_count=user_count,
                        confidence_weight=weight
                    ))
        
        self.db.commit()
        logger.info("Signal strengths updated.")

    def update_signal_strengths_incremental(self, new_metadata_ids: list):
        """
        Recalculates confidence weights only for signals in new metadata.
        Much faster than full recalculation.
        """
        if not new_metadata_ids:
            return
        
        logger.info(f"Updating signal strengths for {len(new_metadata_ids)} new records...")
        
        # Get unique signals from new metadata
        new_metadata = self.db.query(UserMetadata).filter(
            UserMetadata.id.in_(new_metadata_ids)
        ).all()
        
        affected_signals = set()
        for meta in new_metadata:
            if meta.device_hash and meta.device_hash != 'NONE':
                affected_signals.add(('DEVICE', meta.device_hash))
            if meta.canvas_hash and meta.canvas_hash != 'NONE':
                affected_signals.add(('CANVAS', meta.canvas_hash))
            if meta.ip_address and meta.ip_address != 'NONE':
                affected_signals.add(('IP', meta.ip_address))
        
        # Update only affected signals
        for signal_type, signal_value in affected_signals:
            col_name = signal_type.lower() + '_hash' if signal_type != 'IP' else 'ip_address'
            
            # Count users for this specific signal
            user_count = self.db.query(func.count(func.distinct(UserMetadata.user_id))).filter(
                getattr(UserMetadata, col_name) == signal_value
            ).scalar()
            
            # Calculate confidence weight
            base_weight = self.config.SIGNAL_WEIGHTS[signal_type]
            
            if signal_type == 'IP':
                if user_count >= self.config.PUBLIC_IP_THRESHOLD:
                    weight = 0.05
                elif user_count >= self.config.MAX_USERS_PER_IP:
                    weight = 0.15
                else:
                    weight = base_weight
            else:
                if user_count > 5:
                    weight = base_weight * (1 / (1 + user_count * 0.1))
                else:
                    weight = base_weight
            
            # UPSERT signal strength
            existing = self.db.query(SignalStrength).filter_by(
                signal_type=signal_type,
                signal_value=signal_value
            ).first()
            
            if existing:
                existing.user_count = user_count
                existing.confidence_weight = weight
            else:
                self.db.add(SignalStrength(
                    signal_type=signal_type,
                    signal_value=signal_value,
                    user_count=user_count,
                    confidence_weight=weight
                ))
        
        self.db.commit()
        logger.info(f"Updated {len(affected_signals)} signal strengths")


# ============================================================================
# LAYER 2: TEMPORAL LINK BUILDER
# ============================================================================

class LinkBuilder:
    """Builds weighted entity links with temporal decay"""

    def __init__(self, db: Session):
        self.db = db
        self.config = GARGConfig()
        # in-memory map to avoid duplicate inserts during one run:
        # key: (user_a, user_b) (ordered tuple), value: EntityLink ORM object
        self._pending_links = {}

    # def build_links_incremental(self, last_processed_id: int):
    #     logger.info(f"Building links from metadata ID {last_processed_id}...")

    #     new_metadata = self.db.query(UserMetadata).filter(
    #         UserMetadata.id > last_processed_id
    #     ).all()

    #     if not new_metadata:
    #         logger.info("No new metadata to process.")
    #         return

    #     self._pending_links = {}
    #     links_created = 0

    #     for new_meta in new_metadata:
    #         potential_links = self._find_shared_signals(new_meta)

    #         for other_user_id, shared_signals, confidence in potential_links:
    #             if confidence >= self.config.MIN_LINK_CONFIDENCE:
    #                 created_or_updated = self._create_or_update_link(
    #                     new_meta.user_id,
    #                     other_user_id,
    #                     shared_signals,
    #                     confidence
    #                 )
    #                 if created_or_updated:
    #                     links_created += 1

    #     # commit once after de-duplicated updates/adds
    #     self.db.commit()
    #     logger.info(f"Created/updated {links_created} links.")
    
    def build_links_incremental(self, last_processed_id: int):
        logger.info(f"Building links from metadata ID {last_processed_id}...")

        new_metadata = self.db.query(UserMetadata).filter(
            UserMetadata.id > last_processed_id
        ).all()

        if not new_metadata:
            logger.info("No new metadata to process.")
            return

        self._pending_links = {}
        links_created = 0
        failed_ids = []  # Dead letter queue

        for new_meta in new_metadata:
            try:
                potential_links = self._find_shared_signals(new_meta)

                for other_user_id, shared_signals, confidence in potential_links:
                    if confidence >= self.config.MIN_LINK_CONFIDENCE:
                        created_or_updated = self._create_or_update_link(
                            new_meta.user_id,
                            other_user_id,
                            shared_signals,
                            confidence
                        )
                        if created_or_updated:
                            links_created += 1
            
            except Exception as e:
                # Log error but continue processing other records
                logger.error(f"Failed to process metadata ID {new_meta.id} (user: {new_meta.user_id}): {e}", exc_info=True)
                failed_ids.append(new_meta.id)

                # Record in dead letter queue
                import traceback
                dlq_entry = FailedMetadataProcessing(
                    metadata_id=new_meta.id,
                    user_id=new_meta.user_id,
                    error_message=str(e)[:500],  # Limit length
                    error_traceback=traceback.format_exc()
                )
                self.db.add(dlq_entry)

                continue  # Don't let one bad record block everything

        # commit once after de-duplicated updates/adds
        try:
            self.db.commit()
        except Exception as e:
            logger.error(f"Failed to commit links: {e}")
            self.db.rollback()
            raise
        
        if failed_ids:
            logger.warning(f"Failed to process {len(failed_ids)} metadata records: {failed_ids}")
        
        logger.info(f"Created/updated {links_created} links. Failed: {len(failed_ids)} records.")

    # def _find_shared_signals(self, metadata: UserMetadata):
    #     """
    #     Find other users who share signals with this user.
    #     Returns: [(user_id, shared_signals, confidence)]
    #     """
    #     results = []
        
    #     # Query for users sharing device
    #     # if metadata.device_hash:
    #     if metadata.device_hash and metadata.device_hash != 'NONE':
    #         device_matches = self.db.query(UserMetadata).filter(
    #             UserMetadata.device_hash == metadata.device_hash,
    #             UserMetadata.user_id != metadata.user_id
    #         ).all()
            
    #         for match in device_matches:
    #             shared_signals = self._calculate_shared_signals(metadata, match)
    #             confidence = self._calculate_confidence(shared_signals, metadata, match)
    #             results.append((match.user_id, shared_signals, confidence))
        
    #     # Query for users sharing canvas
    #     # if metadata.canvas_hash:
    #     if metadata.canvas_hash and metadata.canvas_hash != 'NONE':
    #         canvas_matches = self.db.query(UserMetadata).filter(
    #             UserMetadata.canvas_hash == metadata.canvas_hash,
    #             UserMetadata.user_id != metadata.user_id
    #         ).all()
            
    #         for match in canvas_matches:
    #             if match.user_id not in [r[0] for r in results]:  # Avoid duplicates
    #                 shared_signals = self._calculate_shared_signals(metadata, match)
    #                 confidence = self._calculate_confidence(shared_signals, metadata, match)
    #                 results.append((match.user_id, shared_signals, confidence))
        
    #     # Skip IP-only matches (too many false positives)
        
    #     return results

    def _find_shared_signals(self, metadata: UserMetadata):
        """
        Find other users who share signals with this user.
        Returns: [(user_id, shared_signals, confidence)]
        """
        # Use dict to merge all signals for each user
        signal_map = {}  # {user_id: {UserMetadata object, set_of_signal_types}}
        
        # Query for users sharing device
        if metadata.device_hash and metadata.device_hash != 'NONE':
            device_matches = self.db.query(UserMetadata).filter(
                UserMetadata.device_hash == metadata.device_hash,
                UserMetadata.user_id != metadata.user_id
            ).all()
            
            for match in device_matches:
                if match.user_id not in signal_map:
                    signal_map[match.user_id] = {'metadata': match, 'signals': set()}
                signal_map[match.user_id]['signals'].add('DEVICE')
        
        # Query for users sharing canvas
        if metadata.canvas_hash and metadata.canvas_hash != 'NONE':
            canvas_matches = self.db.query(UserMetadata).filter(
                UserMetadata.canvas_hash == metadata.canvas_hash,
                UserMetadata.user_id != metadata.user_id
            ).all()
            
            for match in canvas_matches:
                if match.user_id not in signal_map:
                    signal_map[match.user_id] = {'metadata': match, 'signals': set()}
                signal_map[match.user_id]['signals'].add('CANVAS')
        
        # Query for users sharing IP (will be used only as confidence booster)
        if metadata.ip_address and metadata.ip_address != 'NONE':
            ip_matches = self.db.query(UserMetadata).filter(
                UserMetadata.ip_address == metadata.ip_address,
                UserMetadata.user_id != metadata.user_id
            ).all()
            
            for match in ip_matches:
                # Only add IP if user already has DEVICE or CANVAS match
                if match.user_id in signal_map:
                    signal_map[match.user_id]['signals'].add('IP')
        
        # Calculate confidence for each matched user with MERGED signals
        results = []
        for user_id, data in signal_map.items():
            shared_signals = list(data['signals'])
            match_metadata = data['metadata']
            confidence = self._calculate_confidence(shared_signals, metadata, match_metadata)
            results.append((user_id, shared_signals, confidence))
        
        return results
    
    def _calculate_shared_signals(self, meta1: UserMetadata, meta2: UserMetadata):
        """Identify which signals are shared"""
        shared = []
        if meta1.device_hash == meta2.device_hash and meta1.device_hash:
            shared.append('DEVICE')
        if meta1.canvas_hash == meta2.canvas_hash and meta1.canvas_hash:
            shared.append('CANVAS')
        if meta1.ip_address == meta2.ip_address and meta1.ip_address:
            shared.append('IP')
        return shared
    
    def _calculate_confidence(self, shared_signals, meta1, meta2):
        """
        Calculate weighted confidence score with additive boost for multiple signals.
        
        AML Logic:
        - Strong signals (DEVICE/CANVAS) = primary confidence
        - Weak signals (IP) = confidence booster (doesn't dilute)
        - Multiple strong signals = highest confidence
        """
        if not shared_signals:
            return 0.0
        
        signal_confidences = []
        
        for signal_type in shared_signals:
            if signal_type == 'DEVICE':
                signal_value = meta1.device_hash
            elif signal_type == 'CANVAS':
                signal_value = meta1.canvas_hash
            else:  # IP
                signal_value = meta1.ip_address
            
            # Lookup signal strength
            signal_strength = self.db.query(SignalStrength).filter_by(
                signal_type=signal_type,
                signal_value=signal_value
            ).first()
            
            weight = signal_strength.confidence_weight if signal_strength else self.config.SIGNAL_WEIGHTS[signal_type]
            signal_confidences.append((signal_type, weight))
        
        # Separate strong (DEVICE/CANVAS) and weak (IP) signals
        strong_signals = [(sig, conf) for sig, conf in signal_confidences if sig in ['DEVICE', 'CANVAS']]
        weak_signals = [(sig, conf) for sig, conf in signal_confidences if sig == 'IP']
        
        if strong_signals:
            # Take MAXIMUM of strong signals as base confidence
            base_confidence = max(conf for _, conf in strong_signals)
            
            # Add bonus for additional strong signals (10% boost per extra signal)
            if len(strong_signals) > 1:
                base_confidence = min(base_confidence * 1.10, 1.0)
            
            # Add bonus for weak signals (5% boost, don't exceed 1.0)
            if weak_signals:
                base_confidence = min(base_confidence * 1.05, 1.0)
            
            raw_confidence = base_confidence
        else:
            # Only weak signals (IP alone) - use original averaging
            # This case shouldn't happen due to skip in _find_shared_signals, but safety check
            raw_confidence = sum(conf for _, conf in signal_confidences) / len(signal_confidences)
        
        # Apply temporal decay
        age_days = (datetime.now(timezone.utc) - meta1.last_seen).days
        adjusted_confidence = self._apply_temporal_decay(raw_confidence, age_days)
    
        return adjusted_confidence
    
    def _apply_temporal_decay(self, confidence: float, age_days: int):
        """Apply temporal decay to confidence score"""
        if age_days < self.config.DECAY_START_DAYS:
            return confidence
        
        if age_days >= self.config.CONNECTION_MAX_AGE_DAYS:
            return 0.0  # Too old, no confidence
        
        # Linear decay from DECAY_START to MAX_AGE
        decay_window = self.config.CONNECTION_MAX_AGE_DAYS - self.config.DECAY_START_DAYS
        decay_amount = (age_days - self.config.DECAY_START_DAYS) / decay_window
        
        return confidence * (1 - decay_amount * 0.7)  # Max 70% decay
    
    def _create_or_update_link(self, user_a, user_b, shared_signals, confidence):
        # ensure ordering
        if user_a > user_b:
            user_a, user_b = user_b, user_a

        pair = (user_a, user_b)

        # 1) If we already created/updated this pair in this run, update that object
        if pair in self._pending_links:
            existing = self._pending_links[pair]
            # update fields
            # existing.shared_signals = json.dumps(shared_signals)
            existing.shared_signals = shared_signals
            existing.adjusted_confidence = confidence
            existing.raw_confidence = confidence
            existing.link_type = "PRIMARY" if confidence >= self.config.HIGH_CONFIDENCE_THRESHOLD else (
                "SECONDARY" if confidence >= self.config.MIN_LINK_CONFIDENCE else "TERTIARY"
            )
            existing.is_active = confidence >= self.config.MIN_LINK_CONFIDENCE
            return False  # not a new insert (count only new inserts if desired)

        # 2) Check DB for existing persisted link
        existing = self.db.query(EntityLink).filter_by(user_a=user_a, user_b=user_b).first()

        link_type = "PRIMARY" if confidence >= self.config.HIGH_CONFIDENCE_THRESHOLD else (
            "SECONDARY" if confidence >= self.config.MIN_LINK_CONFIDENCE else "TERTIARY"
        )

        if existing:
            # existing.shared_signals = json.dumps(shared_signals)
            existing.shared_signals = shared_signals
            existing.adjusted_confidence = confidence
            existing.raw_confidence = confidence
            existing.link_type = link_type
            existing.is_active = confidence >= self.config.MIN_LINK_CONFIDENCE
            # remember it so further updates in this run update the same object
            self._pending_links[pair] = existing
            return False

        # 3) Create new EntityLink and put it in pending map (avoid duplicate creation)
        new_link = EntityLink(
            user_a=user_a,
            user_b=user_b,
            # shared_signals=json.dumps(shared_signals),
            shared_signals=shared_signals,
            link_type=link_type,
            raw_confidence=confidence,
            adjusted_confidence=confidence,
            is_active=True
        )
        self.db.add(new_link)
        self._pending_links[pair] = new_link
        return True


# ============================================================================
# LAYER 3: GARG-AML CLUSTER DETECTOR
# ============================================================================

class GARGClusterDetector:
    """GARG-AML inspired cluster detection with density scoring"""
    
    def __init__(self, db: Session):
        self.db = db
        self.config = GARGConfig()
        self.graph = nx.Graph()
    
    # def build_graph_from_links(self):
    #     """Build graph from entity links (not raw metadata)"""
    #     logger.info("Building graph from entity links...")
        
    #     # Get only active, high-confidence links
    #     links = self.db.query(EntityLink).filter(
    #         EntityLink.is_active == True,
    #         EntityLink.adjusted_confidence >= self.config.MIN_LINK_CONFIDENCE
    #     ).all()
        
    #     for link in links:
    #         self.graph.add_edge(
    #             link.user_a,
    #             link.user_b,
    #             confidence=link.adjusted_confidence,
    #             signals=link.shared_signals
    #         )
        
    #     logger.info(f"Graph built: {self.graph.number_of_nodes()} nodes, {self.graph.number_of_edges()} edges")
    
    def build_graph_from_links(self):
        """Build graph from entity links with real-time temporal decay"""
        logger.info("Building graph from entity links...")
        
        # Get only active links
        links = self.db.query(EntityLink).filter(
            EntityLink.is_active == True
        ).all()
        
        for link in links:
            # Recalculate confidence with current temporal decay
            current_confidence = self._recalculate_temporal_decay(link)
            
            # Only add to graph if confidence still meets threshold
            if current_confidence >= self.config.MIN_LINK_CONFIDENCE:
                self.graph.add_edge(
                    link.user_a,
                    link.user_b,
                    confidence=current_confidence,
                    signals=link.shared_signals
                )
            else:
                # Mark link as inactive if it decayed too much
                link.is_active = False
        
        self.db.commit()
        
        logger.info(f"Graph built: {self.graph.number_of_nodes()} nodes, {self.graph.number_of_edges()} edges")

    def detect_clusters(self):
        """
        Detect clusters using connected components.
        Returns clusters with GARG-AML metrics.
        """
        clusters = []
        
        components = list(nx.connected_components(self.graph))
        
        for component in components:
            users = list(component)
            
            if len(users) >= self.config.MIN_CLUSTER_SIZE:
                # Calculate GARG-AML metrics
                metrics = self._calculate_garg_metrics(users)
                
                if metrics['density'] >= self.config.MIN_CLUSTER_DENSITY:
                    clusters.append({
                        'users': users,
                        'size': len(users),
                        'density': metrics['density'],
                        'avg_confidence': metrics['avg_confidence'],
                        'risk_score': metrics['risk_score'],
                        'shared_signals': metrics['shared_signals']
                    })
        
        return clusters
    
    def _calculate_garg_metrics(self, users):
        """Calculate GARG-AML density and risk metrics"""
        subgraph = self.graph.subgraph(users)
        
        # 1. Density: Actual edges / Possible edges
        n = len(users)
        possible_edges = (n * (n - 1)) / 2
        actual_edges = subgraph.number_of_edges()
        density = actual_edges / possible_edges if possible_edges > 0 else 0
        
        # 2. Average Confidence
        confidences = [data['confidence'] for _, _, data in subgraph.edges(data=True)]
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0
        
        # 3. Shared Signals Analysis
        all_signals = set()
        for _, _, data in subgraph.edges(data=True):
            # signals = json.loads(data['signals'])
            signals = data['signals']
            signals = data['signals'] if isinstance(data['signals'], list) else json.loads(data['signals'])
            all_signals.update(signals)
        
        # 4. Risk Score (GARG-AML Formula)
        size_score = min(n / 10, 1.0)  # Normalize to 0-1
        risk_score = (
            self.config.RISK_WEIGHTS['cluster_size'] * size_score +
            self.config.RISK_WEIGHTS['density'] * density +
            self.config.RISK_WEIGHTS['confidence'] * avg_confidence
        )
        
        return {
            'density': density,
            'avg_confidence': avg_confidence,
            'risk_score': risk_score,
            'shared_signals': list(all_signals)
        }
    
    def _recalculate_temporal_decay(self, link: EntityLink) -> float:
        """
        Recalculate temporal decay for a link based on current time.
        """
        age_days = (datetime.now(timezone.utc) - link.first_linked).days
        
        if age_days < self.config.DECAY_START_DAYS:
            return link.raw_confidence
        
        if age_days >= self.config.CONNECTION_MAX_AGE_DAYS:
            return 0.0
        
        # Linear decay from DECAY_START to MAX_AGE
        decay_window = self.config.CONNECTION_MAX_AGE_DAYS - self.config.DECAY_START_DAYS
        decay_amount = (age_days - self.config.DECAY_START_DAYS) / decay_window
        
        return link.raw_confidence * (1 - decay_amount * 0.7)  # Max 70% decay
        
    def save_clusters(self, clusters):
        """Save detected clusters to database"""
        logger.info(f"Saving {len(clusters)} clusters...")
        
        # Deactivate old clusters
        self.db.query(SmurfCluster).filter(
            SmurfCluster.is_active == True
        ).update({"is_active": False}, synchronize_session=False)
        
        # Step 2: UPSERT each cluster member
        for cluster in clusters:
            # cluster_id = f"Ring-{cluster['users'][0]}"
            cluster_id = f"Ring-{sorted(cluster['users'])[0]}"
            
            for user_id in cluster['users']:
                # Check if this (cluster_id, user_id) already exists
                existing = self.db.query(SmurfCluster).filter_by(
                    cluster_id=cluster_id,
                    user_id=user_id
                ).first()
                
                if existing:
                    # UPDATE: Reactivate and update metrics
                    existing.is_active = True
                    existing.cluster_size = cluster['size']
                    existing.density_score = cluster['density']
                    existing.confidence_score = cluster['avg_confidence']
                    existing.risk_score = cluster['risk_score']
                    # existing.shared_signals = json.dumps(cluster['shared_signals'])
                    existing.shared_signals = cluster['shared_signals']

                    # Keep formation_date (historical record)
                    # Keep manually_reviewed and review_status (compliance decision)
                else:
                    # INSERT: New cluster member
                    self.db.add(SmurfCluster(
                        cluster_id=cluster_id,
                        user_id=user_id,
                        cluster_size=cluster['size'],
                        density_score=cluster['density'],
                        confidence_score=cluster['avg_confidence'],
                        risk_score=cluster['risk_score'],
                        # shared_signals=json.dumps(cluster['shared_signals']),
                        shared_signals=cluster['shared_signals'],
                        is_active=True,
                        review_status="PENDING"
                    ))
        
        self.db.commit()
        logger.info("Clusters saved with UPSERT logic.")


# ============================================================================
# MAIN ANALYZER (Orchestrator)
# ============================================================================

class GraphAnalyzer:
    """Main orchestrator for multi-layered analysis"""
    
    def __init__(self, db: Session):
        self.db = db
        self.signal_analyzer = SignalAnalyzer(db)
        self.link_builder = LinkBuilder(db)
        self.cluster_detector = GARGClusterDetector(db)
    
    def run_full_analysis(self):
        """Execute complete GARG-AML pipeline"""
        start_time = datetime.now(timezone.utc)
        
        try:
            # Get last processed state
            state = self.db.query(AnalysisState).first()
            if not state:
                state = AnalysisState(last_processed_metadata_id=0)
                self.db.add(state)
                self.db.commit()
            
            state.worker_status = "RUNNING"
            self.db.commit()
            
            last_id = state.last_processed_metadata_id
            
            # # Layer 1: Update signal strengths
            # self.signal_analyzer.update_signal_strengths()
            
            # # Layer 2: Build links incrementally
            # self.link_builder.build_links_incremental(last_id)

            # Get new metadata IDs
            latest_id = self.db.query(func.max(UserMetadata.id)).scalar() or 0
            new_metadata_ids = list(range(last_id + 1, latest_id + 1))

            # Layer 1: Update signal strengths (incremental)
            self.signal_analyzer.update_signal_strengths_incremental(new_metadata_ids)

            # Layer 2: Build links incrementally
            self.link_builder.build_links_incremental(last_id)
            
            # Layer 3: Detect clusters
            self.cluster_detector.build_graph_from_links()
            clusters = self.cluster_detector.detect_clusters()
            
            # Layer 4: Save results
            self.cluster_detector.save_clusters(clusters)
            
            # Update state
            latest_id = self.db.query(func.max(UserMetadata.id)).scalar() or 0
            state.last_processed_metadata_id = latest_id
            state.clusters_detected = len(clusters)
            state.analysis_duration_seconds = (datetime.now(timezone.utc) - start_time).total_seconds()
            state.worker_status = "IDLE"
            state.error_message = None
            
            self.db.commit()
            
            logger.info(f"Analysis complete. Processed up to ID {latest_id}, found {len(clusters)} clusters.")
            
        except Exception as e:
            logger.error(f"Analysis failed: {e}", exc_info=True)
            # Ensure the DB session is not left in a failed state
            try:
                self.db.rollback()
            except Exception:
                # ignore rollback errors but log them
                logger.exception("Rollback failed or session already closed.")

            # Try to persist the error state in a fresh transaction
            try:
                state_obj = self.db.query(AnalysisState).first()
                if not state_obj:
                    state_obj = AnalysisState(last_processed_metadata_id=last_id if 'last_id' in locals() else 0)
                    self.db.add(state_obj)

                state_obj.worker_status = "ERROR"
                state_obj.error_message = str(e)
                self.db.commit()
            except Exception:
                # if persisting state fails, avoid masking original exception
                logger.exception("Failed to persist error state after rollback.")
            raise
#!/usr/bin/env python3
"""
Entity Resolution Worker (GARG-AML Enhanced)
Multi-layered incremental analysis with confidence scoring.
"""

import time
import logging
from app.database import get_db
from app.graph_engine import GraphAnalyzer
from datetime import datetime, timezone

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - GARG-WORKER - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============================================================================
# CONFIGURATION
# ============================================================================

class WorkerConfig:
    """Worker configuration parameters"""
    ANALYSIS_INTERVAL_SECONDS = 60  # Run every 1 minute
    MIN_METADATA_BATCH = 1          # Only run if at least 1 new records
    MAX_RETRIES = 3                 # Retry on failure
    BACKOFF_MULTIPLIER = 2          # Exponential backoff


# ============================================================================
# WORKER LOGIC
# ============================================================================

def run_analysis_cycle():
    """
    Execute one complete GARG-AML analysis cycle.
    
    Layers:
    1. Signal Strength Update
    2. Incremental Link Building
    3. Cluster Detection
    4. Risk Scoring & Storage
    """
    db_gen = get_db()
    db = next(db_gen)
    
    try:
        logger.info("=" * 70)
        logger.info("Starting GARG-AML Analysis Cycle")
        logger.info("=" * 70)
        
        start_time = datetime.now(timezone.utc)
        
        # Initialize analyzer
        analyzer = GraphAnalyzer(db)
        
        # Run full multi-layered analysis
        analyzer.run_full_analysis()
        
        duration = (datetime.now(timezone.utc) - start_time).total_seconds()
        
        logger.info("=" * 70)
        logger.info(f"Analysis Cycle Complete (Duration: {duration:.2f}s)")
        logger.info("=" * 70)
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Analysis cycle failed: {e}", exc_info=True)
        return False
        
    finally:
        db.close()


def should_run_analysis(db) -> bool:
    """
    Determine if analysis should run based on data availability.
    
    Returns:
        bool: True if there's enough new data to warrant analysis
    """
    from app.models import AnalysisState, UserMetadata
    from sqlalchemy import func
    
    try:
        state = db.query(AnalysisState).first()
        last_id = state.last_processed_metadata_id if state else 0
        
        # Count new metadata records
        latest_id = db.query(func.max(UserMetadata.id)).scalar() or 0
        new_records = latest_id - last_id
        
        if new_records >= WorkerConfig.MIN_METADATA_BATCH:
            logger.info(f"✓ {new_records} new metadata records detected. Running analysis...")
            return True
        else:
            logger.debug(f"Only {new_records} new records. Skipping analysis.")
            return False
            
    except Exception as e:
        logger.error(f"Error checking analysis conditions: {e}", exc_info=True)
        return False


def run_worker_with_backoff():
    """
    Worker with exponential backoff on failures.
    """
    config = WorkerConfig()
    retry_count = 0
    
    while True:
        try:
            # Check if analysis is needed
            db_gen = get_db()
            db = next(db_gen)
            
            should_run = should_run_analysis(db)
            db.close()
            
            if should_run:
                success = run_analysis_cycle()
                
                if success:
                    retry_count = 0  # Reset on success
                else:
                    retry_count += 1
                    
                    if retry_count >= config.MAX_RETRIES:
                        logger.error(f"⚠️  Max retries ({config.MAX_RETRIES}) reached. Backing off...")
                        time.sleep(config.ANALYSIS_INTERVAL_SECONDS * config.BACKOFF_MULTIPLIER ** retry_count)
            
            # Wait before next cycle
            logger.info(f"Next analysis in {config.ANALYSIS_INTERVAL_SECONDS}s...")
            time.sleep(config.ANALYSIS_INTERVAL_SECONDS)
            
        except KeyboardInterrupt:
            logger.info("🛑 Worker stopped by user")
            break
            
        except Exception as e:
            logger.error(f"❌ Worker error: {e}", exc_info=True)
            retry_count += 1
            
            if retry_count >= config.MAX_RETRIES:
                logger.error("⚠️  Max retries reached. Longer backoff...")
                time.sleep(config.ANALYSIS_INTERVAL_SECONDS * config.BACKOFF_MULTIPLIER ** 3)
            else:
                time.sleep(config.ANALYSIS_INTERVAL_SECONDS)


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    logger.info("=" * 70)
    logger.info("🕷️  GARG-AML Entity Resolution Worker Starting...")
    logger.info("=" * 70)
    logger.info("Configuration:")
    logger.info(f"  - Analysis Interval: {WorkerConfig.ANALYSIS_INTERVAL_SECONDS}s")
    logger.info(f"  - Min Batch Size: {WorkerConfig.MIN_METADATA_BATCH} records")
    logger.info(f"  - Max Retries: {WorkerConfig.MAX_RETRIES}")
    logger.info("=" * 70)
    logger.info("")
    
    run_worker_with_backoff()
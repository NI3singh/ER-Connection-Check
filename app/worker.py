import time
import logging
from app.database import get_db
from app.graph_engine import GraphAnalyzer

# Setup Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - SPIDER-WORKER - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def run_analysis_cycle():
    """
    One complete cycle of fetching data -> building graph -> saving alerts.
    """
    db_gen = get_db()
    db = next(db_gen)
    
    try:
        logger.info("--- Starting Analysis Cycle ---")
        
        # 1. Initialize Engine
        analyzer = GraphAnalyzer(db)
        
        # 2. Load Data from DB to RAM
        analyzer.build_graph_from_db()
        
        # 3. Find Rings
        clusters = analyzer.find_clusters()
        
        # 4. Save Alerts
        if clusters:
            analyzer.save_results(clusters)
        else:
            logger.info("No Smurf Clusters found in this cycle.")
            
        logger.info("--- Cycle Complete ---")
        
    except Exception as e:
        logger.error(f"Worker Error: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    logger.info("🕷️ Entity Resolution Spider Started...")
    
    while True:
        run_analysis_cycle()
        
        # Wait for 30 seconds before next check
        # In MNC production, we would use Celery/Kafka triggers, 
        # but for 500 users, this loop is efficient and robust.
        time.sleep(30)

from sqlalchemy import create_engine, text, inspect
from sqlalchemy.orm import sessionmaker
import os
import sys
from dotenv import load_dotenv
from app.db_base import Base
import importlib

# load env
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    print("❌ ERROR: DATABASE_URL not found in .env file")
    sys.exit(1)

# Create engine (set echo=True temporarily if you want to see SQL)
engine = create_engine(DATABASE_URL, pool_pre_ping=True)

# SessionLocal class
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    """Dependency for FastAPI"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def reset_sequences():
    """
    Reset all PostgreSQL sequences to match current max IDs.
    Call this if ID numbering gets out of sync.
    """
    print("🔧 Resetting database sequences...")

    tables = ['user_metadata', 'entity_links', 'signal_strengths', 
              'smurf_clusters', 'analysis_state']
    
    for table in tables:
        try:
            with engine.connect() as conn:
                # Reset user_metadata sequence
                conn.execute(text("""
                    SELECT setval(
                        pg_get_serial_sequence('user_metadata', 'id'),
                        COALESCE((SELECT MAX(id) FROM user_metadata), 0) + 1,
                        false
                    );
                """))
                
                # Reset entity_links sequence
                conn.execute(text("""
                    SELECT setval(
                        pg_get_serial_sequence('entity_links', 'id'),
                        COALESCE((SELECT MAX(id) FROM entity_links), 0) + 1,
                        false
                    );
                """))
                
                # Reset signal_strengths sequence
                conn.execute(text("""
                    SELECT setval(
                        pg_get_serial_sequence('signal_strengths', 'id'),
                        COALESCE((SELECT MAX(id) FROM signal_strengths), 0) + 1,
                        false
                    );
                """))
                
                # Reset smurf_clusters sequence
                conn.execute(text("""
                    SELECT setval(
                        pg_get_serial_sequence('smurf_clusters', 'id'),
                        COALESCE((SELECT MAX(id) FROM smurf_clusters), 0) + 1,
                        false
                    );
                """))
                
                # Reset analysis_state sequence
                conn.execute(text("""
                    SELECT setval(
                        pg_get_serial_sequence('analysis_state', 'id'),
                        COALESCE((SELECT MAX(id) FROM analysis_state), 0) + 1,
                        false
                    );
                """))
                
                conn.commit()
                
            print("✅ All sequences reset successfully")
            return True
            
        except Exception as e:
            print(f"❌ Failed to reset sequences: {e}")
            return False

def init_database():
    """
    Initialize database: import models (so classes register on Base),
    then create all tables if they don't exist.
    """
    print("🔧 Initializing database...")
    try:
        # Test connection
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        print("✅ Database connection successful")

        # Import the models module (this registers classes on Base.metadata)
        try:
            importlib.import_module("app.models")
            print("✅ app.models imported")
        except Exception as e:
            print("❌ Failed to import app.models:", e)
            raise

        # Debug: what SQLAlchemy metadata knows about (tables to create)
        print("🧾 SQLAlchemy knows these tables (metadata):", sorted(Base.metadata.tables.keys()))

        # Create all tables known to Base
        Base.metadata.create_all(bind=engine)
        print("✅ Database tables created/verified (create_all executed)")

        # Inspect actual DB tables
        inspector = inspect(engine)
        print("📋 Tables found in database (public):", inspector.get_table_names(schema="public"))
        print("📋 Tables found (all):", inspector.get_table_names())

        # Reset sequences to fix ID numbering
        reset_sequences()

        # Initialize analysis state if not exists
        from app.models import AnalysisState  # import after models registered

        db = SessionLocal()
        try:
            state = db.query(AnalysisState).first()
            if not state:
                state = AnalysisState(last_processed_metadata_id=0, worker_status="IDLE")
                db.add(state)
                db.commit()
                print("✅ Analysis state initialized")
            else:
                print("✅ Analysis state already exists")
        finally:
            db.close()

        print("\n✅ Database initialization complete!")
        return True

    except Exception as e:
        print(f"\n❌ Database initialization failed: {e}")
        return False


if __name__ == "__main__":
    print("="*60)
    print("Entity Resolution Module - Database Setup")
    print("="*60)
    print()

    success = init_database()

    print()
    print("="*60)
    if success:
        print("✅ Ready to start API and Worker!")
        print()
        print("Next steps:")
        print("  1. uvicorn main:app --host 0.0.0.0 --port 8001")
        print("  2. python worker.py")
    else:
        print("❌ Please fix errors above and try again")
    print("="*60)
    sys.exit(0 if success else 1)
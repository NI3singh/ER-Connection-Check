# app/db_base.py
from sqlalchemy.orm import declarative_base

# Single shared Base for all models and the DB layer
Base = declarative_base()

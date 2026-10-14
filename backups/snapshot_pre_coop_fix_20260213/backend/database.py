"""
Supabase Client Configuration
"""
import os
from supabase import create_client, Client

# Supabase Configuration
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://luboqqopfjpxbuphyitw.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imx1Ym9xcW9wZmpweGJ1cGh5aXR3Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjExNjIxMTgsImV4cCI6MjA3NjczODExOH0.1ZQS254D-_sfjxiisy1R9QT-LBx93vTbp_Rj0y0cuws")

# Create Supabase Client
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def get_db():
    """Dependency for FastAPI routes"""
    return supabase

def init_db():
    """
    Initialize database tables.
    With Supabase HTTP API, we cannot execute DDL SQL directly.
    The user must run the init_schema.sql in the Supabase Dashboard.
    """
    # Evita emoji aqui porque em alguns ambientes Windows (cp1252) pode causar UnicodeEncodeError no startup.
    print("Database initialization skipped. Please run init_schema.sql in Supabase Dashboard.")

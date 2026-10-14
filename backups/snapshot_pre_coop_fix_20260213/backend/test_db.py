import psycopg2
import socket
import os

poolers = [
    "aws-0-sa-east-1.pooler.supabase.com",
    "aws-0-us-east-1.pooler.supabase.com",
    "aws-0-eu-central-1.pooler.supabase.com"
]

for pooler in poolers:
    try:
        ip = socket.gethostbyname(pooler)
        print(f"Resolved {pooler} to {ip}")
        
        # Try to connect
        user = "postgres.luboqqopfjpxbuphyitw"
        password = "adminfluxfarma"
        url = f"postgresql://{user}:{password}@{pooler}:6543/postgres"
        print(f"Testing Pooler URL: {url}")
        try:
            conn = psycopg2.connect(url, sslmode='require', connect_timeout=5)
            print(f"SUCCESS: Connected to {pooler}!")
            conn.close()
            break
        except Exception as e:
            print(f"Connection failed to {pooler}: {e}")
            
    except Exception as e:
        print(f"DNS failed for {pooler}: {e}")

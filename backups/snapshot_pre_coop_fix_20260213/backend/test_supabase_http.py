import os
from supabase import create_client, Client

url = "https://luboqqopfjpxbuphyitw.supabase.co"
key = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imx1Ym9xcW9wZmpweGJ1cGh5aXR3Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjExNjIxMTgsImV4cCI6MjA3NjczODExOH0.1ZQS254D-_sfjxiisy1R9QT-LBx93vTbp_Rj0y0cuws"

print(f"Connecting to Supabase HTTP API: {url}")

try:
    supabase: Client = create_client(url, key)
    # Try to select from a table (even if empty or non-existent, it should return error or empty list)
    # 'users' table might not exist yet if SQL wasn't run, but let's try.
    # Or just check health? Supabase client doesn't have explicit ping.
    # We can try to list buckets or something.
    
    print("Client created.")
    
    # Try to sign in (mock) or just query
    response = supabase.table("users").select("*").limit(1).execute()
    print(f"Query result: {response}")
    
except Exception as e:
    print(f"Supabase HTTP Error: {e}")

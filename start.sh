#!/bin/sh
set -e

# Preload data into Supabase from the bundled Excel file if available
# if [ -f "/app/data/relatorio_flux.xlsx" ]; then
#   echo "Running initial ETL from /app/data/relatorio_flux.xlsx"
#   python etl/process_data.py /app/data/relatorio_flux.xlsx || echo "ETL preload failed; continuing without cached data"
# else
#   echo "No /app/data/relatorio_flux.xlsx found; skipping preload."
# fi

# Start FastAPI, honoring the port provided by Cloud Run
PORT="${PORT:-8080}"
echo "Starting API on port ${PORT}"
exec uvicorn backend.main:app --host 0.0.0.0 --port "${PORT}"

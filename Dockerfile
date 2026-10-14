# Stage 1: Build Frontend
FROM node:18-alpine as builder
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm install
COPY frontend/ .
RUN npm run build

# Stage 2: Run Backend
FROM python:3.9-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1

# Install dependencies
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy Backend Code
COPY backend/ backend/

# Copy ETL Code
COPY etl/ ./etl/

# Copy data assets (Excel input used by ETL)
COPY data ./data

# Copy Frontend Build from Stage 1
COPY --from=builder /app/frontend/dist ./dist

# Expose Port (Cloud Run will override with $PORT)
EXPOSE 8080

# Startup script
COPY start.sh ./start.sh
RUN apt-get update && apt-get install -y dos2unix && dos2unix ./start.sh && apt-get clean && rm -rf /var/lib/apt/lists/*
RUN chmod +x ./start.sh

# Command
CMD ["./start.sh"]

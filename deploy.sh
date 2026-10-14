#!/bin/bash
set -e

# Configurações
PROJECT_ID="rh-coopmob-bot"
REGION="us-central1"
SERVICE_NAME="dre-flux"
IMAGE_NAME="us-central1-docker.pkg.dev/${PROJECT_ID}/panel-services/${SERVICE_NAME}:latest"

# Variáveis de ambiente
SUPABASE_URL="https://luboqqopfjpxbuphyitw.supabase.co"
SUPABASE_KEY="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imx1Ym9xcW9wZmpweGJ1cGh5aXR3Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjExNjIxMTgsImV4cCI6MjA3NjczODExOH0.1ZQS254D-_sfjxiisy1R9QT-LBx93vTbp_Rj0y0cuws"

echo "🚀 Iniciando deploy do DRE Flux para Cloud Run..."

# 1. Build da imagem
echo "📦 Construindo imagem Docker..."
docker build -t ${IMAGE_NAME} .

# 2. Push para Artifact Registry
echo "⬆️  Enviando imagem para Artifact Registry..."
docker push ${IMAGE_NAME}

# 3. Deploy no Cloud Run
echo "☁️  Fazendo deploy no Cloud Run..."
gcloud run deploy ${SERVICE_NAME} \
  --image ${IMAGE_NAME} \
  --region ${REGION} \
  --platform managed \
  --allow-unauthenticated \
  --port 8080 \
  --cpu 1 \
  --memory 1Gi \
  --max-instances 1 \
  --set-env-vars SUPABASE_URL=${SUPABASE_URL},SUPABASE_KEY=${SUPABASE_KEY}

echo "✅ Deploy concluído!"
echo "🌐 Acesse sua aplicação em:"
gcloud run services describe ${SERVICE_NAME} --region ${REGION} --format='value(status.url)'

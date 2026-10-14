$ErrorActionPreference = "Stop"

# Configurações
$PROJECT_ID = "rh-coopmob-bot"
$REGION = "us-central1"
$SERVICE_NAME = "dre-flux"
$IMAGE_NAME = "us-central1-docker.pkg.dev/rh-coopmob-bot/panel-services/dre-flux:latest"

# Variáveis de ambiente
$SUPABASE_URL = "https://luboqqopfjpxbuphyitw.supabase.co"
$SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imx1Ym9xcW9wZmpweGJ1cGh5aXR3Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjExNjIxMTgsImV4cCI6MjA3NjczODExOH0.1ZQS254D-_sfjxiisy1R9QT-LBx93vTbp_Rj0y0cuws"

Write-Host "Iniciando deploy do DRE Flux para Cloud Run..." -ForegroundColor Cyan

# 1. Build da imagem
Write-Host "Construindo imagem Docker..." -ForegroundColor Yellow
docker build -t $IMAGE_NAME .
if ($LASTEXITCODE -ne 0) { Write-Error "Falha no build do Docker"; exit 1 }

# 2. Push para Artifact Registry
Write-Host "Enviando imagem para Artifact Registry..." -ForegroundColor Yellow
docker push $IMAGE_NAME
if ($LASTEXITCODE -ne 0) { Write-Error "Falha no push da imagem"; exit 1 }

# 3. Deploy no Cloud Run
Write-Host "Fazendo deploy no Cloud Run..." -ForegroundColor Yellow
$deployArgs = @(
    "run", "deploy", $SERVICE_NAME,
    "--image", $IMAGE_NAME,
    "--region", $REGION,
    "--platform", "managed",
    "--allow-unauthenticated",
    "--port", "8080",
    "--cpu", "1",
    "--memory", "1Gi",
    "--max-instances", "1",
    "--set-env-vars", "SUPABASE_URL=$SUPABASE_URL,SUPABASE_KEY=$SUPABASE_KEY"
)
& gcloud @deployArgs

if ($LASTEXITCODE -ne 0) { Write-Error "Falha no deploy"; exit 1 }

Write-Host "Deploy concluido!" -ForegroundColor Green
Write-Host "Acesse sua aplicacao em:"
gcloud run services describe $SERVICE_NAME --region $REGION --format='value(status.url)'

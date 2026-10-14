# Deploy para Google Cloud Run

## Pré-requisitos

1. **Google Cloud SDK** instalado e configurado
2. **Docker** instalado
3. **Autenticação** configurada:
   ```bash
   gcloud auth login
   gcloud config set project rh-coopmob-bot
   gcloud auth configure-docker us-central1-docker.pkg.dev
   ```

## Opção 1: Deploy Manual (Recomendado para primeira vez)

### Passo 1: Build e Push da Imagem

```bash
# Build da imagem
docker build -t us-central1-docker.pkg.dev/rh-coopmob-bot/panel-services/dre-flux:latest .

# Push para Artifact Registry
docker push us-central1-docker.pkg.dev/rh-coopmob-bot/panel-services/dre-flux:latest
```

### Passo 2: Deploy no Cloud Run

```bash
gcloud run deploy dre-flux \
  --image us-central1-docker.pkg.dev/rh-coopmob-bot/panel-services/dre-flux:latest \
  --region us-central1 \
  --platform managed \
  --allow-unauthenticated \
  --port 8080 \
  --cpu 1 \
  --memory 1Gi \
  --max-instances 1 \
  --set-env-vars SUPABASE_URL=https://luboqqopfjpxbuphyitw.supabase.co,SUPABASE_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imx1Ym9xcW9wZmpweGJ1cGh5aXR3Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjExNjIxMTgsImV4cCI6MjA3NjczODExOH0.1ZQS254D-_sfjxiisy1R9QT-LBx93vTbp_Rj0y0cuws
```

### Usando o Script de Deploy

```bash
chmod +x deploy.sh
./deploy.sh
```

## Opção 2: Deploy Automatizado via Cloud Build

### Configurar Trigger no Cloud Build

```bash
gcloud builds submit --config cloudbuild.yaml
```

Ou configure um trigger automático no repositório Git.

## Verificação Pós-Deploy

Após o deploy, você receberá uma URL como:
```
https://dre-flux-XXXXXXXXXX-uc.a.run.app
```

Teste os endpoints:
```bash
# Health check
curl https://dre-flux-XXXXXXXXXX-uc.a.run.app/api/health

# DRE Financeiro
curl https://dre-flux-XXXXXXXXXX-uc.a.run.app/api/dre/financeiro
```

## Configurações do Cloud Run

- **Região:** us-central1
- **CPU:** 1 vCPU
- **Memória:** 1 GiB
- **Porta:** 8080
- **Instâncias máximas:** 1
- **Autenticação:** Desabilitada (público)

## Variáveis de Ambiente

As seguintes variáveis são configuradas automaticamente:
- `SUPABASE_URL`: URL do projeto Supabase
- `SUPABASE_KEY`: Chave anônima do Supabase
- `PORT`: Porta dinâmica do Cloud Run (gerenciada automaticamente)

## Troubleshooting

### Erro de autenticação do Docker
```bash
gcloud auth configure-docker us-central1-docker.pkg.dev
```

### Verificar logs do Cloud Run
```bash
gcloud run services logs read dre-flux --region us-central1
```

### Atualizar variáveis de ambiente
```bash
gcloud run services update dre-flux \
  --region us-central1 \
  --update-env-vars SUPABASE_URL=nova_url
```

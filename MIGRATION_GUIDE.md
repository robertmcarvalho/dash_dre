# Migração para Supabase PostgreSQL - Guia de Implementação

## Arquivos Criados

### 1. Backend
- ✅ `backend/requirements.txt` - Dependências atualizadas (Supabase, SQLAlchemy, psycopg2)
- ✅ `backend/database.py` - Conexão com PostgreSQL
- ✅ `backend/models.py` - Models SQLAlchemy (User, DREFinanceiro, DREGerencial, PredictiveData)
- ✅ `backend/db_helpers.py` - Funções auxiliares para queries
- ✅ `backend/init_schema.sql` - Script SQL para criar tabelas

### 2. ETL
- ✅ `etl/process_data.py` - Reescrito para usar PostgreSQL (bulk insert)

## Próximos Passos

### 1. Executar SQL no Supabase
Acesse: https://supabase.com/dashboard/project/luboqqopfjpxbuphyitw/editor

Cole e execute o conteúdo de `backend/init_schema.sql`

### 2. Atualizar main.py

O arquivo `backend/main.py` precisa ser adaptado manualmente. Principais mudanças:

**Importações:**
```python
from sqlalchemy.orm import Session
from backend.database import get_db
from backend.db_helpers import *
```

**Remover:**
- Código de conexão Redis
- `import redis`

**Atualizar rotas (exemplo):**
```python
@app.get("/api/dre/financeiro")
def get_dre_financeiro(db: Session = Depends(get_db)):
    try:
        data = get_dre_financeiro_data(db)
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
```

**Rotas a atualizar:**
- `/api/dre/financeiro` - usar `get_dre_financeiro_data(db)`
- `/api/dre/gerencial` - usar `get_dre_gerencial_data(db)`
- `/api/predictive/forecast` - usar `get_predictive_data(db)`
- `/api/valuation/data` - usar `get_predictive_data(db)`
- `/api/users` (GET) - usar `list_users(db)`
- `/api/users` (POST) - usar `create_user(db, nome, senha)`
- `/api/users/{username}` (DELETE) - usar `delete_user(db, username)`
- `/api/login` - usar `get_user(db, username)`

### 3. Variáveis de Ambiente

Adicionar no Cloud Run:
```
SUPABASE_DB_URL=postgresql://postgres:adminfluxfarma@db.luboqqopfjpxbuphyitw.supabase.co:5432/postgres
```

### 4. Rebuild e Deploy

```bash
# Local test
docker-compose down
docker-compose build --no-cache
docker-compose up -d

# Deploy to Cloud Run
gcloud builds submit --tag gcr.io/PROJECT_ID/dre-flux-farma
gcloud run deploy dre-flux-farma \
  --image gcr.io/PROJECT_ID/dre-flux-farma \
  --set-env-vars SUPABASE_DB_URL=postgresql://...
```

### 5. Testar ETL

```bash
docker exec dash_dre_flux-api-1 python etl/process_data.py
```

## Verificação

1. Tabelas criadas no Supabase
2. ETL executa sem erros
3. Dados aparecem nas tabelas
4. APIs retornam dados corretamente
5. Upload de arquivo funciona
6. Gestão de usuários funciona

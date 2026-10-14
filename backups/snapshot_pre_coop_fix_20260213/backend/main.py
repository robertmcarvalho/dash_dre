from fastapi import FastAPI, HTTPException, Depends, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from supabase import Client
from typing import Optional
import os
import json
import shutil
import subprocess
import pandas as pd
import unicodedata

print(">>> CARREGANDO MAIN.PY ATUALIZADO COM DEBUG ENDPOINT <<<")
# Database imports
from backend.database import get_db, init_db
from backend.db_helpers import *

app = FastAPI()

# Configuração de CORS
origins = [
    "http://localhost:5173",
    "http://localhost:3000",
    "http://127.0.0.1:5173",
    "*"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize database on startup
import logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Helper function for text normalization
def norm(text):
    text = "" if text is None else str(text).strip()
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).upper()

@app.on_event("startup")
async def startup_event():
    init_db()
    logger.info("Database initialized (Client ready)")
    logger.info(f"Routes: {[route.path for route in app.routes]}")

# Models
class LoginRequest(BaseModel):
    username: str
    password: str

class LoginResponse(BaseModel):
    token: str
    perfil: str
    perfil: str

class UserCreate(BaseModel):
    nome: str
    senha: str

@app.get("/api/health")
def health_check(db: Client = Depends(get_db)):
    try:
        # Test connection by listing dre_financeiro (limit 1)
        db.table("dre_financeiro").select("id").limit(1).execute()
        db_status = "connected"
    except Exception as e:
        print(f"Health check failed: {e}")
        db_status = "disconnected"
    
    return {"status": "ok", "database": db_status}

@app.get("/api/dre/financeiro")
def get_dre_financeiro(context: str = "flux", db: Client = Depends(get_db)):
    try:
        if context == "coop":
            return get_dre_financeiro_coop(db)
        return get_dre_financeiro_data(db)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/dre/gerencial")
def get_dre_gerencial(context: str = "flux", db: Client = Depends(get_db)):
    try:
        if context == "coop":
            return get_dre_gerencial_coop(db)
        return get_dre_gerencial_data(db)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/ranking_farmacias")
def get_ranking_farmacias(context: str = "flux", debug: bool = False, db: Client = Depends(get_db)):
    """
    Retorna ranking de farmacias com metricas calculadas (apenas cenario competencia).
    - Receita: entradas por farmacia/mes.
    - Despesa rateada: despesas operacionais do mes (sem Simples) rateadas proporcionalmente a receita do mes.
    - Imposto rateado: Simples pago no mes seguinte rateado proporcionalmente a receita do mes; se ausente, estimado pela aliquota media historica.
    """
    try:
        if context == "coop":
            financeiro = get_dre_financeiro_coop(db)
        else:
            financeiro = get_dre_financeiro_data(db)
        if not financeiro:
            return []

        df_fin = pd.DataFrame(financeiro)

        rename_map = {
            "entrada_saida": "entrada_saida",
            "valor": "valor",
            "centro_custo": "centro_custo",
            "tipo_custo": "tipo_custo",
            "plano_contas": "plano_contas",
            "mes_ano": "mes_ano",
            "mes_nome": "mes_nome",
        }
        df_fin = df_fin.rename(columns=rename_map)



        # Base de impostos para rateio (Flux: Simples; Coop: ISS)
        if context == "coop":
            imposto_mask = df_fin["plano_contas"].str.contains("ISS", na=False, case=False)
        else:
            imposto_mask = df_fin["plano_contas"].str.contains("SIMPLES NACIONAL", na=False, case=False)
        df_imposto_base = df_fin[imposto_mask & (df_fin["entrada_saida"].apply(norm) == "SAIDA")]

        # Normalizacoes
        df_fin["tipo_custo_norm"] = df_fin["tipo_custo"].apply(norm)
        df_fin["plano_norm"] = df_fin["plano_contas"].apply(norm)
        df_fin["entrada_norm"] = df_fin["entrada_saida"].apply(norm)
        df_fin["data_mov_dt"] = pd.to_datetime(df_fin.get("data_mov"), errors="coerce")
        df_fin["mes_ano"] = df_fin["mes_ano"].apply(lambda x: str(x).strip() if x is not None else x)

        df_base = df_fin.copy()
        df_base = df_base[~df_base["tipo_custo_norm"].isin(["NAO OPERACIONAL", "DISTRIBUICAO", "TRANSFERENCIA"])]
        df_base["mes_ano"] = df_base["mes_ano"].apply(lambda x: str(x).strip() if x is not None else x)

        # Base completa para despesas (mantém NAO ESPECIFICADO)
        df_base_all = df_base.copy()

        # Receitas: filtra centros elegíveis (entradas com ENTREGAS) e remove NAO ESPECIFICADO apenas para receitas
        df_fin = df_base[df_base["centro_custo"].apply(lambda x: norm(x) != "NAO ESPECIFICADO")]
        entrada_mask = df_fin["entrada_norm"] == "ENTRADA"
        entregas_mask = df_fin["plano_contas"].apply(norm).str.contains("ENTREGAS", na=False)
        centros_elegiveis = set(df_fin[entrada_mask & entregas_mask]["centro_custo"].tolist())
        df_fin = df_fin[df_fin["centro_custo"].isin(centros_elegiveis)]

        # Receita por mês (para rateio)
        receitas_por_mes = df_fin[df_fin["entrada_norm"] == "ENTRADA"]
        receita_total_mes = receitas_por_mes.groupby("mes_ano")["valor"].sum().to_dict()

        # Despesas operacionais do mês (sem Simples)
        # TODAS as despesas são da empresa (não têm centro específico)
        # VARIÁVEIS: rateadas proporcionalmente ao faturamento
        # FIXAS: rateadas proporcionalmente ao faturamento
        # Excluir categorias que começam com "RECEITAS " (classificação incorreta no Excel)
        df_sem_imposto_all = df_base_all[~df_base_all["plano_norm"].str.contains("SIMPLES NACIONAL|ISS", na=False)]
        df_despesas_all = df_sem_imposto_all[
            (df_sem_imposto_all["entrada_norm"] == "SAIDA") &
            (~df_sem_imposto_all["plano_norm"].str.startswith("RECEITAS "))
        ]
        
        # Variáveis: de toda a empresa (serão rateadas proporcionalmente)
        despesas_var_mes = (
            df_despesas_all[df_despesas_all["tipo_custo_norm"] == "VARIAVEL"]
            .groupby("mes_ano")["valor"].sum().abs().to_dict()
        )
        
        # Fixas: de toda a empresa (serão rateadas igualmente)
        despesas_fix_mes = (
            df_despesas_all[df_despesas_all["tipo_custo_norm"] == "FIXO"]
            .groupby("mes_ano")["valor"].sum().abs().to_dict()
        )

        if debug:
            logger.info(f"[ranking] receita_total_mes: {receita_total_mes}")
            logger.info(f"[ranking] despesas_var_mes: {despesas_var_mes}")
            logger.info(f"[ranking] despesas_fix_mes: {despesas_fix_mes}")

        # Impostos pagos por mes de pagamento (caixa)
        impostos_mes_pagamento = df_imposto_base.groupby("mes_ano")["valor"].sum().abs().to_dict()

        # Impostos de competencia: mes anterior ao pagamento
        def prev_month_str(mes_ano: str) -> str:
            try:
                from datetime import datetime, timedelta

                dt = datetime.strptime(mes_ano + "-01", "%Y-%m-%d")
                prev = dt.replace(day=1) - timedelta(days=1)
                return prev.strftime("%Y-%m")
            except Exception:
                return mes_ano

        impostos_competencia = {}
        for row in df_imposto_base.to_dict(orient="records"):
            m_pag = row.get("mes_ano")
            valor = abs(row.get("valor", 0) or 0)
            m_comp = prev_month_str(m_pag)
            impostos_competencia[m_comp] = impostos_competencia.get(m_comp, 0) + valor
        if debug:
            logger.info(f"[ranking] impostos_competencia (mes -> valor): {impostos_competencia}")

        # Aliquota media historica (pagamento -> competencia anterior)
        # Importante: ignorar meses de competencia sem receita no dataset, para nao inflar a aliquota.
        total_imp = 0.0
        total_rec_comp = 0.0
        for comp_mes, imp in impostos_competencia.items():
            rec = float(receita_total_mes.get(comp_mes, 0) or 0)
            if rec > 0:
                total_imp += float(imp or 0)
                total_rec_comp += rec
        aliquota_media = (total_imp / total_rec_comp) if total_rec_comp else 0

        total_receita_geral = df_fin[df_fin["entrada_norm"] == "ENTRADA"]["valor"].sum()

        ranking = []
        grouped = df_fin.groupby(["centro_custo", "mes_ano", "mes_nome"])
        for (farmacia, mes_ano, mes_nome), df_farm in grouped:
            receita_bruta = df_farm[df_farm["entrada_norm"] == "ENTRADA"]["valor"].sum()
            # Se não houve receita no mês, não entra no ranking nem no rateio (evita distorções)
            if receita_bruta <= 0:
                continue
            receita_mes_total = receita_total_mes.get(mes_ano, 0)

            peso = (receita_bruta / receita_mes_total) if receita_mes_total > 0 else 0

            # Despesas rateadas: variáveis e fixas proporcionais ao faturamento
            despesa_var_rateada = despesas_var_mes.get(mes_ano, 0) * peso
            despesa_fix_rateada = despesas_fix_mes.get(mes_ano, 0) * peso

            # Imposto de competencia: pagamento do mes seguinte; se nao houver, estimar pela aliquota media historica
            imposto_comp_mes = impostos_competencia.get(mes_ano, 0)
            imposto_estimado = False
            if imposto_comp_mes == 0 and aliquota_media and receita_mes_total > 0:
                imposto_comp_mes = receita_mes_total * aliquota_media
                imposto_estimado = True

            imposto_comp = 0
            if peso > 0 and imposto_comp_mes > 0:
                imposto_comp = imposto_comp_mes * peso

            total_despesas = despesa_var_rateada + despesa_fix_rateada + imposto_comp

            margem_contribuicao = receita_bruta - (despesa_var_rateada + despesa_fix_rateada)
            margem_contribuicao_pct = (margem_contribuicao / receita_bruta * 100) if receita_bruta else 0
            rol_comp = receita_bruta - total_despesas
            rentabilidade_comp = (rol_comp / receita_bruta * 100) if receita_bruta else 0
            participacao_lucro = (receita_bruta / total_receita_geral * 100) if total_receita_geral else 0

            ranking.append(
                {
                    "farmacia": farmacia,
                    "mes_nome": mes_nome,
                    "mes_ano": mes_ano,
                    "receita_bruta": float(receita_bruta),
                    "despesa_variavel_rateada": float(despesa_var_rateada),
                    "despesa_fixa_rateada": float(despesa_fix_rateada),
                    "despesas_rateadas_operacionais": float(despesa_var_rateada + despesa_fix_rateada),
                    "despesa_rateada_total": float(total_despesas),
                    "despesa_total_competencia": float(total_despesas),
                    "despesa_total": float(total_despesas),
                    "imposto_rateado_competencia": float(imposto_comp),
                    "imposto_rateado": float(imposto_comp),
                    "imposto_estimado": bool(imposto_estimado),
                    "margem_contribuicao": float(margem_contribuicao),
                    "margem_contribuicao_pct": float(margem_contribuicao_pct),
                    "rol": float(rol_comp),
                    "rol_competencia": float(rol_comp),
                    "rentabilidade": float(rentabilidade_comp),
                    "rentabilidade_competencia": float(rentabilidade_comp),
                    "participacao_lucro": float(participacao_lucro),
                }
            )

        ranking.sort(key=lambda x: x["receita_bruta"], reverse=True)

        if debug:
            return {
                "ranking": ranking,
                "debug": {
                    "receita_total_mes": receita_total_mes,
                    "despesas_var_mes": despesas_var_mes,
                    "despesas_fix_mes": despesas_fix_mes,
                    "impostos_competencia": impostos_competencia,
                    "aliquota_media": aliquota_media,
                },
            }

        return ranking

    except Exception as e:
        print(f"Error in ranking: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/ranking_farmacias_consolidado")
def get_ranking_farmacias_consolidado(debug: bool = False, db: Client = Depends(get_db)):
    """
    Consolida Flux + Cooperativa por farmácia e mês, usando rateio por operação:
    - Flux é calculado pelo endpoint /api/ranking_farmacias?context=flux
    - Coop é calculado pelo endpoint /api/ranking_farmacias?context=coop
    O "Total" é a soma dos componentes já rateados por operação.
    """
    try:
        flux = get_ranking_farmacias(context="flux", debug=False, db=db) or []
        coop = get_ranking_farmacias(context="coop", debug=False, db=db) or []

        def to_float(x):
            try:
                return float(x or 0)
            except Exception:
                return 0.0

        def get_op_fields(row):
            # Campos mais estáveis no retorno atual do ranking
            receita = to_float(row.get("receita_bruta"))
            desp_op = to_float(row.get("despesas_rateadas_operacionais"))
            imposto = to_float(row.get("imposto_rateado"))
            imposto_estimado = bool(row.get("imposto_estimado"))
            return receita, desp_op, imposto, imposto_estimado

        merged = {}

        def upsert(op_key: str, row: dict):
            farmacia_raw = row.get("farmacia") or ""
            mes_ano = row.get("mes_ano") or ""
            mes_nome = row.get("mes_nome") or ""
            # Unificar nomes com/sem acento entre operacoes (ex.: FARMACIA vs FARMÁCIA)
            farmacia_key = norm(farmacia_raw)
            k = (farmacia_key, mes_ano)
            if k not in merged:
                merged[k] = {
                    "farmacia": farmacia_raw,
                    "mes_ano": mes_ano,
                    "mes_nome": mes_nome,
                    # Flux
                    "receita_flux": 0.0,
                    "despesas_op_flux": 0.0,
                    "imposto_flux": 0.0,
                    "imposto_estimado_flux": False,
                    # Coop
                    "receita_coop": 0.0,
                    "despesas_op_coop": 0.0,
                    "imposto_coop": 0.0,
                    "imposto_estimado_coop": False,
                }
            # Preferir mes_nome não vazio (ex.: quando uma operação não tem)
            if mes_nome and not merged[k].get("mes_nome"):
                merged[k]["mes_nome"] = mes_nome
            # Preferir label da cooperativa (normalmente vem com acentos e melhor para exibir)
            if op_key == "coop" and farmacia_raw:
                merged[k]["farmacia"] = farmacia_raw

            receita, desp_op, imposto, imposto_estimado = get_op_fields(row)
            if op_key == "flux":
                merged[k]["receita_flux"] = receita
                merged[k]["despesas_op_flux"] = desp_op
                merged[k]["imposto_flux"] = imposto
                merged[k]["imposto_estimado_flux"] = imposto_estimado
            else:
                merged[k]["receita_coop"] = receita
                merged[k]["despesas_op_coop"] = desp_op
                merged[k]["imposto_coop"] = imposto
                merged[k]["imposto_estimado_coop"] = imposto_estimado

        for r in flux:
            upsert("flux", r)
        for r in coop:
            upsert("coop", r)

        rows = []
        for (farmacia, mes_ano), r in merged.items():
            receita_total = to_float(r.get("receita_flux")) + to_float(r.get("receita_coop"))
            despesas_op_total = to_float(r.get("despesas_op_flux")) + to_float(r.get("despesas_op_coop"))
            imposto_total = to_float(r.get("imposto_flux")) + to_float(r.get("imposto_coop"))
            total_despesas = despesas_op_total + imposto_total
            rol = receita_total - total_despesas
            rentabilidade = (rol / receita_total * 100) if receita_total else 0.0
            margem_contrib = receita_total - despesas_op_total
            margem_contrib_pct = (margem_contrib / receita_total * 100) if receita_total else 0.0

            rows.append(
                {
                    **r,
                    "receita_total": float(receita_total),
                    "despesas_op_total": float(despesas_op_total),
                    "imposto_total": float(imposto_total),
                    "despesa_total": float(total_despesas),
                    "rol": float(rol),
                    "rentabilidade": float(rentabilidade),
                    "margem_contribuicao": float(margem_contrib),
                    "margem_contribuicao_pct": float(margem_contrib_pct),
                    "imposto_estimado": bool(r.get("imposto_estimado_flux") or r.get("imposto_estimado_coop")),
                }
            )

        total_receita_geral = sum(to_float(x.get("receita_total")) for x in rows) or 0.0
        for r in rows:
            r["participacao_receita"] = (
                (to_float(r.get("receita_total")) / total_receita_geral * 100) if total_receita_geral else 0.0
            )

        rows.sort(key=lambda x: x.get("receita_total", 0.0), reverse=True)

        if debug:
            return {
                "ranking": rows,
                "debug": {
                    "count_flux": len(flux),
                    "count_coop": len(coop),
                    "count_merged": len(rows),
                },
            }

        return rows
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/predictive/forecast")
def get_forecast(context: str = "flux", db: Client = Depends(get_db)):
    """Retorna séries de receita e despesa projetadas"""
    try:
        predictive_data = get_predictive_data_coop(db) if context == "coop" else get_predictive_data(db)
        
        if not predictive_data:
            return {
                "mrr_historico": {},
                "mrr_forecast": None,
                "despesa_forecast": None,
                "receitas_por_tipo": []
            }
        
        return {
            "mrr_historico": predictive_data.get('mrr_historico', {}),
            "mrr_forecast": predictive_data.get('mrr_forecast'),
            "despesa_forecast": predictive_data.get('despesa_forecast'),
            "receitas_por_tipo": predictive_data.get('receitas_por_tipo', [])
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/valuation/data")
def get_valuation(context: str = "flux", db: Client = Depends(get_db)):
    """Retorna dados de valuation"""
    try:
        predictive_data = get_predictive_data_coop(db) if context == "coop" else get_predictive_data(db)
        
        if not predictive_data:
            return {
                "arr_current": 0,
                "arr_projected": 0,
                "scenarios": {}
            }
        
        return predictive_data.get('valuation', {})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ===== UPLOAD DE DADOS =====
@app.post("/api/upload")
async def upload_file(
    file: UploadFile = File(...),
    context: str = "flux",
    db: Client = Depends(get_db),
):
    """Upload de arquivo Excel para atualizar dados do dashboard (Flux ou Cooperativa)."""
    try:
        # Validar extensão
        if not file.filename.endswith(('.xlsx', '.xls')):
            raise HTTPException(status_code=400, detail="Apenas arquivos Excel (.xlsx, .xls) são permitidos")
        
        base_dir = os.getcwd()
        data_dir = os.path.join(base_dir, "data")
        os.makedirs(data_dir, exist_ok=True)
        file_path = os.path.join(data_dir, file.filename)
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        # Escolhe ETL conforme contexto
        if context == "coop":
            etl_path = os.path.join(base_dir, "etl", "process_coop.py")
        else:
            etl_path = os.path.join(base_dir, "etl", "process_data.py")

        result = subprocess.run(["python", etl_path, file_path], capture_output=True, text=True)
        if result.returncode != 0:
            raise HTTPException(status_code=500, detail=f"Erro ao processar dados: {result.stderr}")
        
        return {"message": "Arquivo enviado e processado com sucesso", "context": context}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ===== GESTÃO DE USUÁRIOS =====
@app.post("/api/users")
async def create_user_endpoint(user: UserCreate, db: Client = Depends(get_db)):
    """Criar novo usuário (apenas admin)"""
    try:
        existing = get_user(db, user.nome)
        if existing:
            raise HTTPException(status_code=400, detail="Usuário já existe")

        perfil = getattr(user, "perfil", "operacional")
        created = create_user(db, user.nome, user.senha, perfil)

        return {"message": "Usuário criado com sucesso", "user": created}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/users")
async def list_users_endpoint(db: Client = Depends(get_db)):
    """Listar usuários (apenas admin)"""
    try:
        users = list_users(db)
        return {"users": users}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/users/{username}")
async def delete_user_endpoint(username: str, db: Client = Depends(get_db)):
    """Excluir usuário (apenas admin)"""
    try:
        success = delete_user(db, username)
        if not success:
            raise HTTPException(status_code=404, detail="Usuário não encontrado")
        
        return {"message": f"Usuário {username} excluído com sucesso"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/login", response_model=LoginResponse)
def login(request: LoginRequest, db: Client = Depends(get_db)):
    # Mock Auth - In production, use proper authentication
    user = get_user(db, request.username)
    
    if user and user['senha'] == request.password:
        perfil = user.get("perfil", "operacional")
        return LoginResponse(token="mock-jwt-token-12345", perfil=perfil)
    else:
        raise HTTPException(status_code=401, detail="Credenciais inválidas")

# Mount Static Files (Frontend Build)
# The Dockerfile copies frontend build to /app/dist; fallback to local ./dist when rodando local.
static_dir = "/app/dist"
local_dist = os.path.join(os.path.dirname(__file__), "..", "dist")
frontend_dist = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")

def has_assets(path: str) -> bool:
    return os.path.exists(os.path.join(path, "assets"))

if has_assets(static_dir):
    pass
elif has_assets(local_dist):
    static_dir = local_dist
elif has_assets(frontend_dist):
    static_dir = frontend_dist
else:
    static_dir = None

if static_dir:
    app.mount("/assets", StaticFiles(directory=os.path.join(static_dir, "assets")), name="assets")

@app.get("/api/debug_ranking")
def debug_ranking(db: Client = Depends(get_db)):
    try:
        financeiro = get_dre_financeiro_data(db)
        df_fin = pd.DataFrame(financeiro)
        
        # Normalizações básicas
        cols_map = {
            "DATA MOV.": "data_mov",
            "DATA": "data_mov",
            "VALOR": "valor",
            "PARCEIRO": "parceiro",
            "CENTRO DE CUSTO": "centro_custo",
            "PLANO DE CONTAS": "plano_contas",
            "TIPO_CUSTO": "tipo_custo",
            "ENTRADA/SAIDA": "entrada_saida"
        }
        df_fin = df_fin.rename(columns=lambda c: cols_map.get(norm(c), c))
        
        # Normaliza colunas chave
        df_fin["tipo_custo_norm"] = df_fin["tipo_custo"].apply(norm)
        df_fin["entrada_norm"] = df_fin["entrada_saida"].apply(norm)
        df_fin["plano_norm"] = df_fin["plano_contas"].apply(norm)
        df_fin["mes_ano"] = df_fin["mes_ano"].apply(lambda x: str(x).strip() if x is not None else x)
        
        # Base completa
        df_base_all = df_fin.copy()
        df_base_all = df_base_all[~df_base_all["tipo_custo_norm"].isin(["NAO OPERACIONAL", "DISTRIBUICAO", "TRANSFERENCIA"])]
        
        # Análise de Despesas Fixas
        df_sem_simples = df_base_all[~df_base_all["plano_norm"].str.contains("SIMPLES NACIONAL", na=False)]
        df_despesas = df_sem_simples[df_sem_simples["entrada_norm"] == "SAIDA"]
        
        despesas_fixas = df_despesas[df_despesas["tipo_custo_norm"] == "FIXO"]
        despesas_fix_mes = despesas_fixas.groupby("mes_ano")["valor"].sum().abs().to_dict()
        
        # Amostra de registros fixos
        amostra_fixos = despesas_fixas[["data_mov", "valor", "plano_contas", "centro_custo", "mes_ano"]].head(5).to_dict(orient="records")
        
        # Verificação de chaves de mês
        meses_disponiveis = sorted(list(despesas_fix_mes.keys()))
        
        return {
            "status": "debug",
            "total_registros_brutos": len(df_fin),
            "total_despesas_operacionais": len(df_despesas),
            "total_despesas_fixas": len(despesas_fixas),
            "soma_despesas_fixas_por_mes": despesas_fix_mes,
            "meses_com_despesas_fixas": meses_disponiveis,
            "amostra_registros_fixos": amostra_fixos,
            "exemplo_mes_ano_formato": df_fin["mes_ano"].iloc[0] if not df_fin.empty else "N/A"
        }
    except Exception as e:
        import traceback
        return {"error": str(e), "traceback": traceback.format_exc()}

@app.get("/api/adiantamentos")
def get_adiantamentos(context: str = "flux", db: Client = Depends(get_db)):
    """
    Retorna saldos de adiantamentos (apenas cooperativa).
    """
    if context != "coop":
        return []
    try:
        return get_adiantamentos_coop(db)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/coop/resumo")
def get_coop_resumo(ano: Optional[str] = None, db: Client = Depends(get_db)):
    """
    Indicadores de capital e adiantamentos da Cooperativa.
    """
    try:
        fin = get_dre_financeiro_coop(db)
        df_fin = pd.DataFrame(fin)
        if not df_fin.empty:
            if ano:
                df_fin = df_fin[df_fin["mes_ano"].str.startswith(str(ano))]
            df_fin["plano_norm"] = df_fin["plano_contas"].apply(norm)
            cota_mask = df_fin["plano_norm"].str.contains("COTA|ENTRADA DE CAPITAL", na=False)
            entradas_cota = float(
                df_fin[cota_mask & (df_fin["entrada_saida"].str.upper() == "ENTRADA")]["valor"].sum()
            )
            saidas_cota = float(
                df_fin[cota_mask & (df_fin["entrada_saida"].str.upper().str.startswith("SA"))]["valor"].sum()
            )
        else:
            entradas_cota = saidas_cota = 0.0
        saldo_cota = entradas_cota + saidas_cota

        adi = get_adiantamentos_coop(db)
        df_adi = pd.DataFrame(adi)
        if not df_adi.empty:
            if ano:
                df_adi = df_adi[df_adi["mes_ano"].str.startswith(str(ano))]
            adiantado = float(df_adi.get("adiantado", pd.Series(dtype=float)).sum())
            reembolsado = float(df_adi.get("reembolsado", pd.Series(dtype=float)).sum())
            saldo_adi = float(df_adi.get("saldo", pd.Series(dtype=float)).sum())
        else:
            adiantado = reembolsado = saldo_adi = 0.0

        return {
            "entradas_cota": entradas_cota,
            "saidas_cota": saidas_cota,
            "saldo_cota": saldo_cota,
            "adiantado": adiantado,
            "reembolsado": reembolsado,
            "saldo_adiantamentos": saldo_adi,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/{full_path:path}")
async def serve_react_app(full_path: str):
    if full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="API endpoint not found")
    
    if os.path.exists(static_dir):
        file_path = os.path.join(static_dir, full_path)
        if os.path.exists(file_path) and os.path.isfile(file_path):
            return FileResponse(file_path)
        return FileResponse(os.path.join(static_dir, "index.html"))
    return {"message": "Frontend not built or dist directory missing"}

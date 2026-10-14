import io

from fastapi import FastAPI, HTTPException, Depends, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
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
from datetime import datetime, timedelta

if os.getenv("DEBUG_STARTUP") == "1":
    print("DEBUG: carregando backend/main.py")
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

COOP_CONCILIACAO_AUDIT_SELECT = ",".join(
    [
        "semana_inicio",
        "semana_fim",
        "periodo_inicio",
        "periodo_fim",
        "data_mov",
        "data_pag",
        "entrada_saida",
        "plano_contas",
        "parceiro",
        "referente",
        "numero_docto",
        "registro_movimento",
        "registro_compromisso",
        "centro_custo_raw",
        "centro_custo_final",
        "valor_mov",
        "valor_rateado",
        "linha_tipo",
    ]
)

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

        # Referência Flux (apenas quando context=coop): garante consistência por mês (ENTREGAS)
        # na lista de lojas "que faturaram" entre Flux e Cooperativa.
        allowed_pairs_flux = None
        if context == "coop":
            try:
                flux_ref = get_dre_financeiro_data(db) or []
                df_flux = pd.DataFrame(flux_ref)
                if not df_flux.empty:
                    df_flux = df_flux.rename(columns=rename_map)
                    df_flux["plano_norm"] = df_flux["plano_contas"].apply(norm)
                    df_flux["entrada_norm"] = df_flux["entrada_saida"].apply(norm)
                    df_flux["centro_norm"] = df_flux["centro_custo"].apply(norm)
                    df_flux["mes_ano"] = df_flux["mes_ano"].apply(lambda x: str(x).strip() if x is not None else x)
                    allowed_pairs_flux = df_flux[
                        (df_flux["entrada_norm"] == "ENTRADA")
                        & (df_flux["plano_norm"].str.contains("ENTREGAS", na=False))
                        & (df_flux["centro_norm"] != "NAO ESPECIFICADO")
                    ][["centro_custo", "mes_ano", "mes_nome"]].drop_duplicates()
            except Exception as e:
                logger.warning(f"[ranking] Falha ao carregar referência Flux para context=coop: {e}")

        # Receitas: filtra centros elegíveis (entradas com ENTREGAS) e remove NAO ESPECIFICADO apenas para receitas
        df_fin = df_base[df_base["centro_custo"].apply(lambda x: norm(x) != "NAO ESPECIFICADO")]
        entrada_mask = df_fin["entrada_norm"] == "ENTRADA"
        entregas_mask = df_fin["plano_contas"].apply(norm).str.contains("ENTREGAS", na=False)
        centros_elegiveis = set(df_fin[entrada_mask & entregas_mask]["centro_custo"].tolist())
        df_fin = df_fin[df_fin["centro_custo"].isin(centros_elegiveis)]

        if allowed_pairs_flux is not None and not allowed_pairs_flux.empty:
            allowed_centros = set(allowed_pairs_flux["centro_custo"].tolist())
            centros_elegiveis = centros_elegiveis.intersection(allowed_centros)
            df_fin = df_fin[df_fin["centro_custo"].isin(centros_elegiveis)]

        # Receita por mês (para rateio): usar apenas ENTREGAS (centro de custo é confiável só para esse plano)
        receitas_entregas = df_fin[
            (df_fin["entrada_norm"] == "ENTRADA")
            & (df_fin["plano_norm"].str.contains("ENTREGAS", na=False))
        ].copy()
        if allowed_pairs_flux is not None and not allowed_pairs_flux.empty:
            # Basear a lista (farmácia, mês) no Flux e preencher faltantes do Coop com 0
            coop_sum = (
                receitas_entregas.groupby(["centro_custo", "mes_ano"])["valor"]
                .sum()
                .reset_index()
            )
            base_pairs = allowed_pairs_flux[["centro_custo", "mes_ano", "mes_nome"]].copy()
            receitas_entregas = base_pairs.merge(coop_sum, on=["centro_custo", "mes_ano"], how="left")
            receitas_entregas["valor"] = receitas_entregas["valor"].fillna(0)
        receita_total_mes = receitas_entregas.groupby("mes_ano")["valor"].sum().to_dict()

        # Despesas operacionais do mês (sem imposto): separar em
        # - Diretas (com centro de custo elegível): entram 100% na farmácia
        # - Overhead (sem centro ou não elegível): rateado proporcionalmente à receita do mês
        # Excluir categorias que começam com "RECEITAS " (classificação incorreta no Excel)
        df_sem_imposto_all = df_base_all[~df_base_all["plano_norm"].str.contains("SIMPLES NACIONAL|ISS", na=False)]
        df_despesas_all = df_sem_imposto_all[
            (df_sem_imposto_all["entrada_norm"] == "SAIDA")
            & (~df_sem_imposto_all["plano_norm"].str.startswith("RECEITAS "))
        ].copy()

        def is_centro_elegivel(x):
            return (norm(x) != "NAO ESPECIFICADO") and (x in centros_elegiveis)

        despesas_diretas_mask = df_despesas_all["centro_custo"].apply(is_centro_elegivel)
        df_despesas_diretas = df_despesas_all[despesas_diretas_mask].copy()
        df_despesas_overhead = df_despesas_all[~despesas_diretas_mask].copy()

        overhead_var_mes = (
            df_despesas_overhead[df_despesas_overhead["tipo_custo_norm"] == "VARIAVEL"]
            .groupby("mes_ano")["valor"]
            .sum()
            .abs()
            .to_dict()
        )
        overhead_fix_mes = (
            df_despesas_overhead[df_despesas_overhead["tipo_custo_norm"] == "FIXO"]
            .groupby("mes_ano")["valor"]
            .sum()
            .abs()
            .to_dict()
        )

        df_despesas_diretas["valor_abs"] = df_despesas_diretas["valor"].abs()
        diretas_var = (
            df_despesas_diretas[df_despesas_diretas["tipo_custo_norm"] == "VARIAVEL"]
            .groupby(["centro_custo", "mes_ano"])["valor_abs"]
            .sum()
            .to_dict()
        )
        diretas_fix = (
            df_despesas_diretas[df_despesas_diretas["tipo_custo_norm"] == "FIXO"]
            .groupby(["centro_custo", "mes_ano"])["valor_abs"]
            .sum()
            .to_dict()
        )

        if debug:
            logger.info(f"[ranking] receita_total_mes: {receita_total_mes}")
            logger.info(f"[ranking] overhead_var_mes: {overhead_var_mes}")
            logger.info(f"[ranking] overhead_fix_mes: {overhead_fix_mes}")
            logger.info(f"[ranking] diretas_var (sample): {list(diretas_var.items())[:10]}")
            logger.info(f"[ranking] diretas_fix (sample): {list(diretas_fix.items())[:10]}")

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

        total_receita_geral = receitas_entregas["valor"].sum()

        ranking = []
        grouped = receitas_entregas.groupby(["centro_custo", "mes_ano", "mes_nome"])
        for (farmacia, mes_ano, mes_nome), df_farm in grouped:
            receita_bruta = df_farm["valor"].sum()
            if receita_bruta <= 0:
                # No contexto cooperativa, mantemos a linha para manter a mesma lista de lojas do Flux por mês
                # (o valor pode ficar 0 quando não houver rateio no relatório da cooperativa).
                if not (context == "coop" and allowed_pairs_flux is not None and not allowed_pairs_flux.empty):
                    continue
            receita_mes_total = receita_total_mes.get(mes_ano, 0)

            peso = (receita_bruta / receita_mes_total) if receita_mes_total > 0 else 0

            # Despesas: diretas + overhead rateado (proporcional à receita)
            desp_dir_var = float(diretas_var.get((farmacia, mes_ano), 0) or 0)
            desp_dir_fix = float(diretas_fix.get((farmacia, mes_ano), 0) or 0)
            desp_ov_var = float(overhead_var_mes.get(mes_ano, 0) or 0) * peso
            desp_ov_fix = float(overhead_fix_mes.get(mes_ano, 0) or 0) * peso

            despesa_var_total = desp_dir_var + desp_ov_var
            despesa_fix_total = desp_dir_fix + desp_ov_fix

            # Imposto de competencia: pagamento do mes seguinte; se nao houver, estimar pela aliquota media historica
            imposto_comp_mes = impostos_competencia.get(mes_ano, 0)
            imposto_estimado = False
            if imposto_comp_mes == 0 and aliquota_media and receita_mes_total > 0:
                imposto_comp_mes = receita_mes_total * aliquota_media
                imposto_estimado = True

            imposto_comp = 0
            if peso > 0 and imposto_comp_mes > 0:
                imposto_comp = imposto_comp_mes * peso

            despesas_operacionais = despesa_var_total + despesa_fix_total
            total_despesas = despesas_operacionais + imposto_comp

            # Margem de contribuição: receita - despesas variáveis (não inclui fixas)
            margem_contribuicao = receita_bruta - despesa_var_total
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
                    "despesa_variavel_rateada": float(despesa_var_total),
                    "despesa_fixa_rateada": float(despesa_fix_total),
                    "despesas_rateadas_operacionais": float(despesas_operacionais),
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
                    "overhead_var_mes": overhead_var_mes,
                    "overhead_fix_mes": overhead_fix_mes,
                    "diretas_var_count": len(diretas_var),
                    "diretas_fix_count": len(diretas_fix),
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


def _iso_date(s: str) -> str:
    try:
        return datetime.strptime(s, "%Y-%m-%d").date().isoformat()
    except Exception:
        raise HTTPException(status_code=400, detail="Data inválida, use YYYY-MM-DD")


def _week_bounds_from_monday(monday_iso: str) -> tuple[str, str]:
    d = datetime.strptime(monday_iso, "%Y-%m-%d").date()
    if d.weekday() != 0:
        raise HTTPException(status_code=400, detail="week_start deve ser uma segunda-feira (YYYY-MM-DD)")
    return d.isoformat(), (d + timedelta(days=6)).isoformat()


@app.get("/api/coop/conciliacao_entregas/weeks")
def coop_conciliacao_weeks(db: Client = Depends(get_db)):
    """
    Lista semanas disponíveis (Seg-Dom) para conciliação na Cooperativa.
    Base: tabela coop_conciliacao_entregas.
    """
    try:
        # Se a tabela não existir, dar mensagem clara
        try:
            db.table("coop_conciliacao_entregas").select("id").limit(1).execute()
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Tabela coop_conciliacao_entregas inexistente. Execute backend/coop_conciliacao_entregas.sql no Supabase. Erro: {e}",
            )

        # Buscar semanas únicas. A tabela tem muitas linhas por semana, então paginamos e
        # coletamos as primeiras N semanas distintas (ordenando pelas mais recentes).
        unique = set()
        out = []
        start = 0
        chunk = 1000
        max_weeks = 120
        while len(out) < max_weeks:
            batch = (
                db.table("coop_conciliacao_entregas")
                .select("semana_inicio, semana_fim")
                .not_.is_("semana_fim", "null")
                .order("semana_inicio", desc=True)
                .range(start, start + chunk - 1)
                .execute()
                .data
            ) or []
            if not batch:
                break
            for r in batch:
                si = r.get("semana_inicio")
                sf = r.get("semana_fim")
                if not si or not sf:
                    continue
                k = f"{si}|{sf}"
                if k in unique:
                    continue
                unique.add(k)
                out.append({"semana_inicio": si, "semana_fim": sf})
                if len(out) >= max_weeks:
                    break
            start += chunk

        out.sort(key=lambda x: (x.get("semana_inicio") or ""))
        return out
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/coop/conciliacao_entregas")
def coop_conciliacao_entregas(week_start: str, farmacias: Optional[str] = None, only_divergent: bool = False, db: Client = Depends(get_db)):
    """
    Conciliação semanal (Seg-Dom) para Cooperativa:
    - Receita: ENTRADA + RECEITAS COM ENTREGAS (somente RATEIO)
    - A pagar: SAÍDA + PAGAMENTO DE ENTREGADORES (somente RATEIO)
    - Controle: total MOV (VALOR MOV.CONTA) vs soma RATEIO para pagamento de entregadores
    Semana é definida pelo fim do intervalo em REFERENTE (ETL já grava semana_inicio/semana_fim).
    """
    try:
        ws = _iso_date(week_start)
        ws, we = _week_bounds_from_monday(ws)

        # Base query: semana
        q = (
            db.table("coop_conciliacao_entregas")
            .select(
                "semana_inicio,semana_fim,entrada_saida,plano_contas,centro_custo_final,valor_mov,valor_rateado,linha_tipo"
            )
            .eq("semana_inicio", ws)
            .eq("semana_fim", we)
        )

        if farmacias:
            farm_list = [f.strip() for f in farmacias.split(",") if f.strip()]
            if farm_list:
                q = q.in_("centro_custo_final", farm_list)

        rows = q.execute().data or []
        df = pd.DataFrame(rows)
        if df.empty:
            return {"week_start": ws, "week_end": we, "rows": []}

        df["plano_norm"] = df["plano_contas"].apply(norm)
        df["entrada_norm"] = df["entrada_saida"].apply(norm)
        df["linha_norm"] = df["linha_tipo"].apply(norm)
        df["centro"] = df["centro_custo_final"].fillna("Não Especificado")

        # Receita entregas (rateio)
        df_rec = df[
            (df["entrada_norm"] == "ENTRADA")
            & (df["plano_norm"] == "RECEITAS COM ENTREGAS")
            & (df["linha_norm"] == "RATEIO")
            & (df["centro"].apply(norm) != "NAO ESPECIFICADO")
        ].copy()
        rec_por_farm = df_rec.groupby("centro")["valor_rateado"].sum().to_dict()

        # Pagamento entregadores (rateio)
        df_pay_rate = df[
            (df["entrada_norm"] == "SAIDA")
            & (df["plano_norm"] == "PAGAMENTO DE ENTREGADORES")
            & (df["linha_norm"] == "RATEIO")
            & (df["centro"].apply(norm) != "NAO ESPECIFICADO")
        ].copy()
        pay_por_farm = df_pay_rate.groupby("centro")["valor_rateado"].sum().to_dict()

        # Controle do pagamento: movimento (mov) vs rateio
        df_pay_mov = df[
            (df["entrada_norm"] == "SAIDA")
            & (df["plano_norm"] == "PAGAMENTO DE ENTREGADORES")
            & (df["linha_norm"] == "MOV")
        ].copy()
        total_mov = float(df_pay_mov["valor_mov"].fillna(0).sum() or 0)
        total_rateado = float(df_pay_rate["valor_rateado"].fillna(0).sum() or 0)
        diff_rateio = total_mov - total_rateado

        # Base de farmácias: união entre receitas e pagamentos
        farms = set(rec_por_farm.keys()) | set(pay_por_farm.keys())
        out_rows = []
        for f in sorted(farms):
            receita = float(rec_por_farm.get(f, 0) or 0)
            pago = float(pay_por_farm.get(f, 0) or 0)
            saldo = receita - pago
            pct = (pago / receita * 100) if receita else 0.0
            out_rows.append(
                {
                    "farmacia": f,
                    "receita_entregas": receita,
                    "pagamento_entregadores": pago,
                    "saldo": saldo,
                    "pct_repasse": pct,
                }
            )

        if only_divergent:
            out_rows = [r for r in out_rows if abs((r["saldo"] or 0)) > 0.01]

        out_rows.sort(key=lambda x: x.get("receita_entregas", 0.0), reverse=True)

        return {
            "week_start": ws,
            "week_end": we,
            "controle": {
                "pagamento_mov_total": total_mov,
                "pagamento_rateado_total": total_rateado,
                "pagamento_diff": diff_rateio,
            },
            "rows": out_rows,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _fetch_all_filtered(db: Client, table: str, select: str, build_query_fn, chunk: int = 1000, max_rows: int = 200000):
    all_rows = []
    start = 0
    while True:
        q = build_query_fn(db.table(table).select(select))
        batch = q.range(start, start + chunk - 1).execute().data or []
        all_rows.extend(batch)
        if len(batch) < chunk:
            break
        start += chunk
        if start >= max_rows:
            break
    return all_rows


@app.get("/api/coop/conciliacao_entregas/cash_weeks")
def coop_conciliacao_cash_weeks(db: Client = Depends(get_db)):
    """
    Lista semanas (Seg-Dom) disponíveis para a visão de CAIXA:
    - usa DATA MOVIMENTO das ENTRADAS de RECEITAS COM ENTREGAS (RATEIO).
    """
    try:
        rows = _fetch_all_filtered(
            db,
            "coop_conciliacao_entregas",
            "data_mov",
            lambda q: q.eq("entrada_saida", "ENTRADA")
            .eq("plano_contas", "RECEITAS COM ENTREGAS")
            .eq("linha_tipo", "RATEIO")
            .not_.is_("data_mov", "null")
            .order("data_mov", desc=True),
            chunk=1000,
            max_rows=50000,
        )
        df = pd.DataFrame(rows)
        if df.empty:
            return []
        df["data_mov"] = pd.to_datetime(df["data_mov"], errors="coerce")
        df = df.dropna(subset=["data_mov"])
        if df.empty:
            return []

        # gerar semanas Seg-Dom a partir das datas existentes
        unique_weeks = set()
        out = []
        for d in sorted(df["data_mov"].dt.date.unique()):
            monday = d - timedelta(days=d.weekday())
            sunday = monday + timedelta(days=6)
            k = f"{monday.isoformat()}|{sunday.isoformat()}"
            if k in unique_weeks:
                continue
            unique_weeks.add(k)
            out.append({"week_start": monday.isoformat(), "week_end": sunday.isoformat()})
        out.sort(key=lambda x: x["week_start"])
        return out
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/coop/conciliacao_entregas/pay_days")
def coop_conciliacao_pay_days(month: Optional[str] = None, db: Client = Depends(get_db)):
    """
    Lista dias de pagamento (rodadas) disponíveis:
    - usa DATA MOVIMENTO das SAÍDAS de PAGAMENTO DE ENTREGADORES (MOV) para evitar duplicidades.
    """
    try:
        month_start = None
        month_end = None
        if month:
            try:
                y, m = month.split("-")
                y = int(y)
                m = int(m)
                if m < 1 or m > 12:
                    raise ValueError("mês inválido")
                from calendar import monthrange

                last_day = monthrange(y, m)[1]
                month_start = datetime(y, m, 1).date().isoformat()
                month_end = datetime(y, m, last_day).date().isoformat()
            except Exception:
                raise HTTPException(status_code=400, detail="Parâmetro month inválido. Use YYYY-MM (ex.: 2025-01).")

        unique = set()
        out = []
        start = 0
        chunk = 1000
        # Não limitar em 180 dias: precisamos mostrar desde o início do ano (ex.: janeiro/2025).
        # Observação: filtros com "SAÍDA" podem falhar por encoding em query params; como
        # plano_contas + linha_tipo já isolam o dataset, omitimos esse filtro.
        max_rows = 200000
        while True:
            def build(q):
                q = (
                    q.select("data_mov")
                    .eq("plano_contas", "PAGAMENTO DE ENTREGADORES")
                    .eq("linha_tipo", "MOV")
                    .order("data_mov", desc=True)
                )
                if month_start and month_end:
                    q = q.gte("data_mov", month_start).lte("data_mov", month_end)
                return q

            batch = (
                build(db.table("coop_conciliacao_entregas"))
                .range(start, start + chunk - 1)
                .execute()
                .data
            ) or []
            if not batch:
                break
            for r in batch:
                d = r.get("data_mov")
                if not d:
                    continue
                if d in unique:
                    continue
                unique.add(d)
                out.append({"pay_day": d})
            start += chunk
            if start >= max_rows:
                break
        out.sort(key=lambda x: x["pay_day"])
        return out
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/coop/conciliacao_entregas/pay_months")
def coop_conciliacao_pay_months(db: Client = Depends(get_db)):
    """
    Lista meses (YYYY-MM) que possuem rodadas de pagamento registradas.
    Base: DATA MOVIMENTO das SAÍDAS de PAGAMENTO DE ENTREGADORES (MOV).
    """
    try:
        rows = _fetch_all_filtered(
            db,
            "coop_conciliacao_entregas",
            "data_mov",
            lambda q: q.eq("plano_contas", "PAGAMENTO DE ENTREGADORES")
            .eq("linha_tipo", "MOV")
            .not_.is_("data_mov", "null")
            .order("data_mov", desc=True),
            chunk=1000,
            max_rows=200000,
        )
        if not rows:
            return []
        df = pd.DataFrame(rows)
        if df.empty or "data_mov" not in df.columns:
            return []
        df["data_mov"] = pd.to_datetime(df["data_mov"], errors="coerce")
        df = df.dropna(subset=["data_mov"])
        if df.empty:
            return []
        months = sorted(set(df["data_mov"].dt.strftime("%Y-%m").tolist()))
        return [{"month": m} for m in months]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/coop/conciliacao_entregas/report")
def coop_conciliacao_report(
    cash_week_start: str,
    pay_day: str,
    mode: Optional[str] = None,
    farmacias: Optional[str] = None,
    only_divergent: bool = False,
    db: Client = Depends(get_db),
):
    """
    Relatório operacional para aprovação na quinta:
    - Receita (Caixa): o que entrou na semana Seg-Dom (DATA MOVIMENTO), RATEIO de ENTREGAS.
    - Pagamento (Rodada): o que será pago no dia selecionado (DATA MOVIMENTO), RATEIO de PAGAMENTO DE ENTREGADORES.
    - Mostra também quais ciclos (periodo_inicio/fim do REFERENTE) estão incluídos na rodada.
    """
    try:
        ws = _iso_date(cash_week_start)
        ws, we = _week_bounds_from_monday(ws)
        pd_day = _iso_date(pay_day)
        mode_norm = norm(mode) if mode else "CICLO"
        if mode_norm not in ("CICLO", "CAIXA"):
            mode_norm = "CICLO"

        farm_list = None
        if farmacias:
            farm_list = [f.strip() for f in farmacias.split(",") if f.strip()]

        def apply_farm_filter(q):
            if farm_list:
                return q.in_("centro_custo_final", farm_list)
            return q

        # Receita (caixa semana): ENTRADA + ENTREGAS + RATEIO, por data_mov no intervalo
        rec_rows = _fetch_all_filtered(
            db,
            "coop_conciliacao_entregas",
            "centro_custo_final,valor_rateado",
            lambda q: apply_farm_filter(q)
            .eq("entrada_saida", "ENTRADA")
            .eq("plano_contas", "RECEITAS COM ENTREGAS")
            .eq("linha_tipo", "RATEIO")
            .gte("data_mov", ws)
            .lte("data_mov", we),
            chunk=1000,
            max_rows=200000,
        )
        df_rec = pd.DataFrame(rec_rows)
        if not df_rec.empty:
            df_rec["centro"] = df_rec["centro_custo_final"].fillna("Não Especificado")
            df_rec = df_rec[df_rec["centro"].apply(norm) != "NAO ESPECIFICADO"]
            df_rec["valor_rateado"] = pd.to_numeric(df_rec["valor_rateado"], errors="coerce").fillna(0.0)
        rec_por_farm = (df_rec.groupby("centro")["valor_rateado"].sum().to_dict() if not df_rec.empty else {})

        # Pagamento (rodada): SAÍDA + PAGAMENTO DE ENTREGADORES + RATEIO, por data_mov == pd_day
        pay_rate_rows = _fetch_all_filtered(
            db,
            "coop_conciliacao_entregas",
            "centro_custo_final,valor_rateado,periodo_inicio,periodo_fim",
            lambda q: apply_farm_filter(q)
            .eq("plano_contas", "PAGAMENTO DE ENTREGADORES")
            .eq("linha_tipo", "RATEIO")
            .eq("data_mov", pd_day),
            chunk=1000,
            max_rows=200000,
        )
        df_pay = pd.DataFrame(pay_rate_rows)
        if not df_pay.empty:
            df_pay["centro"] = df_pay["centro_custo_final"].fillna("Não Especificado")
            df_pay = df_pay[df_pay["centro"].apply(norm) != "NAO ESPECIFICADO"]
            df_pay["valor_rateado"] = pd.to_numeric(df_pay["valor_rateado"], errors="coerce").fillna(0.0)
            df_pay["pi"] = pd.to_datetime(df_pay["periodo_inicio"].astype(str).str.slice(0, 10), errors="coerce").dt.date
            df_pay["pf"] = pd.to_datetime(df_pay["periodo_fim"].astype(str).str.slice(0, 10), errors="coerce").dt.date

        # Excluir ciclos FUTUROS (periodo_inicio/fim > pay_day) do cálculo principal.
        # Eles representam antecipações/lançamentos fora do ciclo e serão listados separadamente.
        df_pay_valid = df_pay
        df_pay_future = pd.DataFrame()
        if not df_pay.empty and "pi" in df_pay.columns and "pf" in df_pay.columns:
            pd_day_d = pd.to_datetime(str(pd_day)[:10], errors="coerce").date()
            mask_future = (df_pay["pi"] > pd_day_d) | (df_pay["pf"] > pd_day_d)
            df_pay_future = df_pay[mask_future].copy()
            df_pay_valid = df_pay[~mask_future].copy()

        pay_por_farm = (df_pay_valid.groupby("centro")["valor_rateado"].sum().to_dict() if not df_pay_valid.empty else {})
        pay_por_farm_future = (
            df_pay_future.groupby("centro")["valor_rateado"].sum().to_dict() if not df_pay_future.empty else {}
        )

        ciclos_por_farm = {}
        ciclos_futuros_por_farm = {}
        if not df_pay_valid.empty:
            for f, g in df_pay_valid.groupby("centro"):
                ciclos = (
                    g[["periodo_inicio", "periodo_fim"]]
                    .dropna()
                    .drop_duplicates()
                    .to_dict(orient="records")
                )
                ciclos_por_farm[f] = ciclos
        if not df_pay_future.empty:
            for f, g in df_pay_future.groupby("centro"):
                ciclos = (
                    g[["periodo_inicio", "periodo_fim"]]
                    .dropna()
                    .drop_duplicates()
                    .to_dict(orient="records")
                )
                ciclos_futuros_por_farm[f] = ciclos

        wanted_ranges_by_farm = {}
        wanted_ranges_by_farm_future = {}
        all_ranges = set()
        all_ranges_future = set()
        min_pi_d = None
        max_pf_d = None
        for f, ciclos in ciclos_por_farm.items():
            wanted = set()
            for c in ciclos or []:
                pi = c.get("periodo_inicio")
                pf = c.get("periodo_fim")
                if not pi or not pf:
                    continue
                di = pd.to_datetime(str(pi)[:10], errors="coerce")
                df_ = pd.to_datetime(str(pf)[:10], errors="coerce")
                if pd.isna(di) or pd.isna(df_):
                    continue
                di = di.date()
                df_ = df_.date()
                if di > df_:
                    di, df_ = df_, di
                wanted.add((di, df_))
            if wanted:
                wanted_ranges_by_farm[f] = wanted
                all_ranges |= wanted
                fmin = min(r[0] for r in wanted)
                fmax = max(r[1] for r in wanted)
                min_pi_d = fmin if min_pi_d is None else min(min_pi_d, fmin)
                max_pf_d = fmax if max_pf_d is None else max(max_pf_d, fmax)

        # Ciclos FUTUROS (auditoria): não entram no cálculo principal, mas são exibidos separadamente.
        for f, ciclos in ciclos_futuros_por_farm.items():
            wanted = set()
            for c in ciclos or []:
                pi = c.get("periodo_inicio")
                pf = c.get("periodo_fim")
                if not pi or not pf:
                    continue
                di = pd.to_datetime(str(pi)[:10], errors="coerce")
                df_ = pd.to_datetime(str(pf)[:10], errors="coerce")
                if pd.isna(di) or pd.isna(df_):
                    continue
                di = di.date()
                df_ = df_.date()
                if di > df_:
                    di, df_ = df_, di
                wanted.add((di, df_))
            if wanted:
                wanted_ranges_by_farm_future[f] = wanted
                all_ranges_future |= wanted
                # ampliar janela de busca (para conseguir auditar diárias/receitas futuras),
                # mantendo o filtro final por ranges.
                fmin = min(r[0] for r in wanted)
                fmax = max(r[1] for r in wanted)
                min_pi_d = fmin if min_pi_d is None else min(min_pi_d, fmin)
                max_pf_d = fmax if max_pf_d is None else max(max_pf_d, fmax)

        def date_in_any_range(d, ranges):
            if not d or not ranges:
                return False
            for a, b in ranges:
                if a <= d <= b:
                    return True
            return False

        # Receita (ciclos apurados pagos na rodada): soma ENTRADAS (ENTREGAS/RATEIO) dentro dos
        # períodos (periodo_inicio/fim) que apareceram no pagamento do dia, por farmácia.
        # Importante: para bater com o relatório base, usamos o PERÍODO APURADO (REFERENTE -> periodo_inicio/fim)
        # nas linhas de RECEITAS, e não o DATA MOVIMENTO (que pode ocorrer dias depois).
        receita_ciclos_por_farm: dict[str, float] = {}
        if ciclos_por_farm:
            df_cycles = df_pay_valid[["centro", "periodo_inicio", "periodo_fim"]].dropna()
            if not df_cycles.empty:
                df_cycles["pi"] = pd.to_datetime(df_cycles["periodo_inicio"].astype(str).str.slice(0, 10), errors="coerce")
                df_cycles["pf"] = pd.to_datetime(df_cycles["periodo_fim"].astype(str).str.slice(0, 10), errors="coerce")
                df_cycles = df_cycles.dropna(subset=["pi", "pf"])

            if df_cycles is not None and not df_cycles.empty:
                min_pi = df_cycles["pi"].dt.date.min().isoformat()
                max_pf = df_cycles["pf"].dt.date.max().isoformat()

                rec_cycle_rows = _fetch_all_filtered(
                    db,
                    "coop_conciliacao_entregas",
                    "centro_custo_final,valor_rateado,periodo_inicio,periodo_fim",
                    lambda q: apply_farm_filter(q)
                    .eq("plano_contas", "RECEITAS COM ENTREGAS")
                    .eq("linha_tipo", "RATEIO")
                    .not_.is_("periodo_inicio", "null")
                    .not_.is_("periodo_fim", "null")
                    .gte("periodo_fim", min_pi)
                    .lte("periodo_fim", max_pf),
                    chunk=1000,
                    max_rows=400000,
                )

                df_rec_cycle = pd.DataFrame(rec_cycle_rows)
                if not df_rec_cycle.empty:
                    df_rec_cycle["centro"] = df_rec_cycle["centro_custo_final"].fillna("Não Especificado")
                    df_rec_cycle = df_rec_cycle[df_rec_cycle["centro"].apply(norm) != "NAO ESPECIFICADO"]
                    df_rec_cycle["valor_rateado"] = pd.to_numeric(df_rec_cycle["valor_rateado"], errors="coerce").fillna(0.0)
                    df_rec_cycle["pi"] = pd.to_datetime(df_rec_cycle["periodo_inicio"].astype(str).str.slice(0, 10), errors="coerce")
                    df_rec_cycle["pf"] = pd.to_datetime(df_rec_cycle["periodo_fim"].astype(str).str.slice(0, 10), errors="coerce")
                    df_rec_cycle = df_rec_cycle.dropna(subset=["pi", "pf"])
                    df_rec_cycle["pi"] = df_rec_cycle["pi"].dt.date
                    df_rec_cycle["pf"] = df_rec_cycle["pf"].dt.date

                for f, ciclos in ciclos_por_farm.items():
                    if df_rec_cycle.empty:
                        receita_ciclos_por_farm[f] = 0.0
                        continue
                    df_farm = df_rec_cycle[df_rec_cycle["centro"] == f]
                    if df_farm.empty:
                        receita_ciclos_por_farm[f] = 0.0
                        continue

                    wanted = set()
                    for c in ciclos or []:
                        pi = c.get("periodo_inicio")
                        pf = c.get("periodo_fim")
                        if not pi or not pf:
                            continue
                        di = pd.to_datetime(str(pi)[:10], errors="coerce")
                        df_ = pd.to_datetime(str(pf)[:10], errors="coerce")
                        if pd.isna(di) or pd.isna(df_):
                            continue
                        di = di.date()
                        df_ = df_.date()
                        if di > df_:
                            di, df_ = df_, di
                        wanted.add((di, df_))

                    if not wanted:
                        receita_ciclos_por_farm[f] = 0.0
                        continue

                    keys = list(zip(df_farm["pi"].tolist(), df_farm["pf"].tolist()))
                    mask = pd.Series(keys).isin(wanted).values
                    receita_ciclos_por_farm[f] = float(df_farm.loc[mask, "valor_rateado"].sum())

        # Diárias do ciclo (custo adicional): SAÍDA + DIARIA DE MOTOBOY + RATEIO
        # Preferência: match por (periodo_inicio/fim); fallback: data_mov dentro do range do ciclo.
        diarias_por_farm: dict[str, float] = {}
        diarias_futuras_por_farm: dict[str, float] = {}
        df_diaria = pd.DataFrame()
        if min_pi_d and max_pf_d and (wanted_ranges_by_farm or wanted_ranges_by_farm_future):
            # Prioriza o período apurado (periodo_inicio/fim) para casar com os ciclos do pagamento.
            diaria_rows_periodo = _fetch_all_filtered(
                db,
                "coop_conciliacao_entregas",
                "centro_custo_final,valor_rateado,periodo_inicio,periodo_fim,data_mov,registro_movimento,registro_compromisso,numero_docto",
                lambda q: apply_farm_filter(q)
                .eq("entrada_saida", "SAÍDA")
                .eq("plano_contas", "DIARIA DE MOTOBOY")
                .eq("linha_tipo", "RATEIO")
                .not_.is_("periodo_inicio", "null")
                .not_.is_("periodo_fim", "null")
                .gte("periodo_fim", str(min_pi_d))
                .lte("periodo_fim", str(max_pf_d)),
                chunk=1000,
                max_rows=200000,
            )

            # Fallback: somente quando não houver período apurado.
            diaria_rows_sem_periodo_a = _fetch_all_filtered(
                db,
                "coop_conciliacao_entregas",
                "centro_custo_final,valor_rateado,periodo_inicio,periodo_fim,data_mov,registro_movimento,registro_compromisso,numero_docto",
                lambda q: apply_farm_filter(q)
                .eq("entrada_saida", "SAÍDA")
                .eq("plano_contas", "DIARIA DE MOTOBOY")
                .eq("linha_tipo", "RATEIO")
                .is_("periodo_inicio", "null")
                .gte("data_mov", str(min_pi_d))
                .lte("data_mov", str(max_pf_d)),
                chunk=1000,
                max_rows=200000,
            )
            diaria_rows_sem_periodo_b = _fetch_all_filtered(
                db,
                "coop_conciliacao_entregas",
                "centro_custo_final,valor_rateado,periodo_inicio,periodo_fim,data_mov,registro_movimento,registro_compromisso,numero_docto",
                lambda q: apply_farm_filter(q)
                .eq("entrada_saida", "SAÍDA")
                .eq("plano_contas", "DIARIA DE MOTOBOY")
                .eq("linha_tipo", "RATEIO")
                .is_("periodo_fim", "null")
                .gte("data_mov", str(min_pi_d))
                .lte("data_mov", str(max_pf_d)),
                chunk=1000,
                max_rows=200000,
            )

            diaria_rows = (diaria_rows_periodo or []) + (diaria_rows_sem_periodo_a or []) + (diaria_rows_sem_periodo_b or [])
            df_diaria = pd.DataFrame(diaria_rows)
            if not df_diaria.empty:
                df_diaria["centro"] = df_diaria["centro_custo_final"].fillna("Não Especificado")
                df_diaria = df_diaria[df_diaria["centro"].apply(norm) != "NAO ESPECIFICADO"]
                df_diaria["valor_rateado"] = pd.to_numeric(df_diaria["valor_rateado"], errors="coerce").fillna(0.0)
                df_diaria["pi"] = pd.to_datetime(df_diaria["periodo_inicio"].astype(str).str.slice(0, 10), errors="coerce").dt.date
                df_diaria["pf"] = pd.to_datetime(df_diaria["periodo_fim"].astype(str).str.slice(0, 10), errors="coerce").dt.date
                df_diaria["dm"] = pd.to_datetime(df_diaria["data_mov"].astype(str).str.slice(0, 10), errors="coerce").dt.date

                # Dedup defensivo para evitar recontagem por reprocessamento/linhas repetidas
                df_diaria["dedup_key"] = (
                    df_diaria.get("centro_custo_final").astype(str)
                    + "|"
                    + df_diaria.get("registro_movimento").astype(str)
                    + "|"
                    + df_diaria.get("registro_compromisso").astype(str)
                    + "|"
                    + df_diaria.get("numero_docto").astype(str)
                    + "|"
                    + df_diaria.get("valor_rateado").astype(str)
                    + "|"
                    + df_diaria.get("periodo_inicio").astype(str)
                    + "|"
                    + df_diaria.get("periodo_fim").astype(str)
                )
                df_diaria = df_diaria.drop_duplicates(subset=["dedup_key"])

            for f, wanted in wanted_ranges_by_farm.items():
                if df_diaria.empty:
                    diarias_por_farm[f] = 0.0
                    continue
                df_farm = df_diaria[df_diaria["centro"] == f]
                if df_farm.empty:
                    diarias_por_farm[f] = 0.0
                    continue
                keys = list(zip(df_farm["pi"].tolist(), df_farm["pf"].tolist()))
                mask_pair = pd.Series(keys).isin(wanted).values
                total = float(df_farm.loc[mask_pair, "valor_rateado"].sum())
                df_no_pair = df_farm.loc[~mask_pair].copy()
                if not df_no_pair.empty:
                    # Fallback por data_mov SOMENTE quando pi/pf estiverem ausentes (nunca quando existir período e ele não casar)
                    df_missing_period = df_no_pair[df_no_pair["pi"].isna() | df_no_pair["pf"].isna()]
                    if not df_missing_period.empty:
                        total += float(
                            df_missing_period[
                                df_missing_period["dm"].apply(lambda d: date_in_any_range(d, wanted))
                            ]["valor_rateado"].sum()
                        )
                diarias_por_farm[f] = total

            for f, wanted in wanted_ranges_by_farm_future.items():
                if df_diaria.empty:
                    diarias_futuras_por_farm[f] = 0.0
                    continue
                df_farm = df_diaria[df_diaria["centro"] == f]
                if df_farm.empty:
                    diarias_futuras_por_farm[f] = 0.0
                    continue
                keys = list(zip(df_farm["pi"].tolist(), df_farm["pf"].tolist()))
                mask_pair = pd.Series(keys).isin(wanted).values
                total = float(df_farm.loc[mask_pair, "valor_rateado"].sum())
                df_no_pair = df_farm.loc[~mask_pair].copy()
                if not df_no_pair.empty:
                    df_missing_period = df_no_pair[df_no_pair["pi"].isna() | df_no_pair["pf"].isna()]
                    if not df_missing_period.empty:
                        total += float(
                            df_missing_period[
                                df_missing_period["dm"].apply(lambda d: date_in_any_range(d, wanted))
                            ]["valor_rateado"].sum()
                        )
                diarias_futuras_por_farm[f] = total

        # Desconto de adiantamento (rodada): ENTRADA + ENTRADA DE REEMBOLSO ADIANTAMENTO (MOV) no dia selecionado
        desconto_adiantamento_rodada = 0.0
        try:
            disc_rows_a = _fetch_all_filtered(
                db,
                "coop_conciliacao_entregas",
                "valor_mov,registro_movimento,registro_compromisso,numero_docto,data_mov,data_pag",
                lambda q: q.eq("plano_contas", "ENTRADA DE REEMBOLSO ADIANTAMENTO")
                .eq("linha_tipo", "MOV")
                .eq("entrada_saida", "ENTRADA")
                .eq("data_mov", pd_day),
                chunk=1000,
                max_rows=200000,
            )
            disc_rows_b = _fetch_all_filtered(
                db,
                "coop_conciliacao_entregas",
                "valor_mov,registro_movimento,registro_compromisso,numero_docto,data_mov,data_pag",
                lambda q: q.eq("plano_contas", "ENTRADA DE REEMBOLSO ADIANTAMENTO")
                .eq("linha_tipo", "MOV")
                .eq("entrada_saida", "ENTRADA")
                .eq("data_pag", pd_day),
                chunk=1000,
                max_rows=200000,
            )
            df_disc = pd.DataFrame((disc_rows_a or []) + (disc_rows_b or []))
            if not df_disc.empty:
                df_disc["key"] = (
                    df_disc.get("registro_movimento").astype(str)
                    + "|"
                    + df_disc.get("registro_compromisso").astype(str)
                    + "|"
                    + df_disc.get("numero_docto").astype(str)
                    + "|"
                    + df_disc.get("valor_mov").astype(str)
                )
                df_disc = df_disc.drop_duplicates(subset=["key"])
                desconto_adiantamento_rodada = float(pd.to_numeric(df_disc.get("valor_mov"), errors="coerce").fillna(0.0).sum())
        except Exception:
            desconto_adiantamento_rodada = 0.0

        # Totais de adiantamentos no ciclo filtrado (global), por data_mov dentro dos ranges
        adiantado_ciclo = 0.0
        reembolsado_ciclo = 0.0
        if min_pi_d and max_pf_d and all_ranges:
            try:
                out_adi_rows = _fetch_all_filtered(
                    db,
                    "coop_conciliacao_entregas",
                    "data_mov,valor_mov",
                    lambda q: q.eq("plano_contas", "SOLICITACAO DE ADIANTAMENTO")
                    .eq("linha_tipo", "MOV")
                    .eq("entrada_saida", "SAÍDA")
                    .gte("data_mov", str(min_pi_d))
                    .lte("data_mov", str(max_pf_d)),
                    chunk=1000,
                    max_rows=200000,
                )
                df_out = pd.DataFrame(out_adi_rows)
                if not df_out.empty:
                    df_out["dm"] = pd.to_datetime(df_out["data_mov"].astype(str).str.slice(0, 10), errors="coerce").dt.date
                    df_out = df_out.dropna(subset=["dm"])
                    df_out = df_out[df_out["dm"].apply(lambda d: date_in_any_range(d, all_ranges))]
                    adiantado_ciclo = float(pd.to_numeric(df_out.get("valor_mov"), errors="coerce").fillna(0.0).abs().sum())

                in_adi_rows = _fetch_all_filtered(
                    db,
                    "coop_conciliacao_entregas",
                    "data_mov,valor_mov",
                    lambda q: q.eq("plano_contas", "ENTRADA DE REEMBOLSO ADIANTAMENTO")
                    .eq("linha_tipo", "MOV")
                    .eq("entrada_saida", "ENTRADA")
                    .gte("data_mov", str(min_pi_d))
                    .lte("data_mov", str(max_pf_d)),
                    chunk=1000,
                    max_rows=200000,
                )
                df_in = pd.DataFrame(in_adi_rows)
                if not df_in.empty:
                    df_in["dm"] = pd.to_datetime(df_in["data_mov"].astype(str).str.slice(0, 10), errors="coerce").dt.date
                    df_in = df_in.dropna(subset=["dm"])
                    df_in = df_in[df_in["dm"].apply(lambda d: date_in_any_range(d, all_ranges))]
                    reembolsado_ciclo = float(pd.to_numeric(df_in.get("valor_mov"), errors="coerce").fillna(0.0).sum())
            except Exception:
                pass

        # Controle do pagamento: MOV total vs RATEIO total no dia
        pay_mov_rows = _fetch_all_filtered(
            db,
            "coop_conciliacao_entregas",
            "valor_mov",
            lambda q: q.eq("plano_contas", "PAGAMENTO DE ENTREGADORES")
            .eq("linha_tipo", "MOV")
            .eq("data_mov", pd_day),
            chunk=1000,
            max_rows=200000,
        )
        df_mov = pd.DataFrame(pay_mov_rows)
        mov_total = float(pd.to_numeric(df_mov.get("valor_mov"), errors="coerce").fillna(0).sum() if not df_mov.empty else 0.0)
        rate_total = float(pd.to_numeric(df_pay.get("valor_rateado"), errors="coerce").fillna(0).sum() if not df_pay.empty else 0.0)
        rate_valid_total = float(
            pd.to_numeric(df_pay_valid.get("valor_rateado"), errors="coerce").fillna(0).sum() if not df_pay_valid.empty else 0.0
        )
        rate_future_total = float(
            pd.to_numeric(df_pay_future.get("valor_rateado"), errors="coerce").fillna(0).sum() if not df_pay_future.empty else 0.0
        )
        diff = mov_total - rate_total

        farms = set(rec_por_farm.keys()) | set(pay_por_farm.keys()) | set(pay_por_farm_future.keys())
        out_rows = []
        for f in sorted(farms):
            receita_caixa = float(rec_por_farm.get(f, 0) or 0)
            receita_ciclos = float(receita_ciclos_por_farm.get(f, 0) or 0)
            pago = float(pay_por_farm.get(f, 0) or 0)
            diaria = float(diarias_por_farm.get(f, 0) or 0)
            pago_futuro = float(pay_por_farm_future.get(f, 0) or 0)
            diaria_futura = float(diarias_futuras_por_farm.get(f, 0) or 0)
            custo_bruto = pago + diaria
            saldo_caixa = receita_caixa - custo_bruto
            saldo_ciclos = receita_ciclos - custo_bruto
            pct_caixa = (custo_bruto / receita_caixa * 100) if receita_caixa else None
            pct_ciclos = (custo_bruto / receita_ciclos * 100) if receita_ciclos else None
            out_rows.append(
                {
                    "farmacia": f,
                    "receita_semana_caixa": receita_caixa,
                    "receita_ciclos_apurados": receita_ciclos,
                    "pagamento_quinta": pago,
                    "diarias_ciclo": diaria,
                    "custo_bruto": custo_bruto,
                    "saldo": saldo_ciclos,
                    "saldo_caixa": saldo_caixa,
                    "pct_repasse": pct_ciclos,
                    "pct_repasse_caixa": pct_caixa,
                    "ciclos": ciclos_por_farm.get(f, []),
                    "pagamento_futuro": pago_futuro,
                    "diarias_futuro": diaria_futura,
                    "ciclos_futuros": ciclos_futuros_por_farm.get(f, []),
                }
            )

        if only_divergent:
            out_rows = [r for r in out_rows if abs((r["saldo"] or 0)) > 0.01]

        out_rows.sort(key=lambda x: x.get("receita_ciclos_apurados", 0.0), reverse=True)

        totals = {
            "receita_semana_caixa": float(sum(rec_por_farm.values()) if rec_por_farm else 0.0),
            "receita_ciclos_apurados": float(sum(receita_ciclos_por_farm.values()) if receita_ciclos_por_farm else 0.0),
            "pagamento_quinta": float(sum(pay_por_farm.values()) if pay_por_farm else 0.0),
            "diarias_ciclo": float(sum(diarias_por_farm.values()) if diarias_por_farm else 0.0),
            "pagamento_futuro": float(sum(pay_por_farm_future.values()) if pay_por_farm_future else 0.0),
            "diarias_futuro": float(sum(diarias_futuras_por_farm.values()) if diarias_futuras_por_farm else 0.0),
            "desconto_adiantamento_rodada": float(desconto_adiantamento_rodada or 0.0),
            "adiantado_ciclo": float(adiantado_ciclo or 0.0),
            "reembolsado_ciclo": float(reembolsado_ciclo or 0.0),
        }
        totals["custo_bruto"] = totals["pagamento_quinta"] + totals["diarias_ciclo"]
        totals["custo_bruto_futuro"] = totals["pagamento_futuro"] + totals["diarias_futuro"]
        totals["custo_bruto_total_com_futuro"] = totals["custo_bruto"] + totals["custo_bruto_futuro"]
        totals["pagamento_liquido"] = totals["custo_bruto"] - totals["desconto_adiantamento_rodada"]
        totals["saldo"] = totals["receita_ciclos_apurados"] - totals["custo_bruto"]
        totals["saldo_caixa"] = totals["receita_semana_caixa"] - totals["custo_bruto"]
        totals["saldo_liquido"] = totals["receita_ciclos_apurados"] - totals["pagamento_liquido"]
        totals["saldo_caixa_liquido"] = totals["receita_semana_caixa"] - totals["pagamento_liquido"]
        totals["adiantamento_liquido_ciclo"] = totals["adiantado_ciclo"] - totals["reembolsado_ciclo"]

        future_rows = [
            {
                "farmacia": r.get("farmacia"),
                "pagamento_futuro": r.get("pagamento_futuro"),
                "diarias_futuro": r.get("diarias_futuro"),
                "ciclos_futuros": r.get("ciclos_futuros"),
            }
            for r in out_rows
            if float(r.get("pagamento_futuro") or 0) > 0 or float(r.get("diarias_futuro") or 0) > 0
        ]
        future_rows.sort(
            key=lambda x: float(x.get("pagamento_futuro") or 0) + float(x.get("diarias_futuro") or 0),
            reverse=True,
        )

        return {
            "cash_week_start": ws,
            "cash_week_end": we,
            "pay_day": pd_day,
            "mode": "caixa" if mode_norm == "CAIXA" else "ciclo",
            "totals": totals,
            "controle": {
                "pagamento_mov_total": mov_total,
                "pagamento_rateado_total": rate_total,
                "pagamento_rateado_valid_total": rate_valid_total,
                "pagamento_rateado_futuro_total": rate_future_total,
                "pagamento_diff": diff,
            },
            "future_rows": future_rows,
            "rows": out_rows,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/coop/conciliacao_entregas/export_xlsx")
def coop_conciliacao_export_xlsx(
    cash_week_start: str,
    pay_day: str,
    mode: Optional[str] = None,
    farmacia: Optional[str] = None,
    farmacias: Optional[str] = None,
    db: Client = Depends(get_db),
):
    """
    Exporta um .xlsx com os registros usados na conciliação (auditoria).
    - Receita (ciclo): RECEITAS COM ENTREGAS (RATEIO) por periodo_inicio/fim dos ciclos pagos.
    - Receita (caixa): RECEITAS COM ENTREGAS (RATEIO) por data_mov na semana Seg-Dom.
    - Pagamento (rodada): PAGAMENTO DE ENTREGADORES (RATEIO) por data_mov == pay_day.
    - Diárias (ciclo): DIARIA DE MOTOBOY (RATEIO) vinculadas aos mesmos ciclos.
    - Ajustes: descontos de adiantamento e adiantamentos (MOV) no período do ciclo (global).
    """
    try:
        ws = _iso_date(cash_week_start)
        ws, we = _week_bounds_from_monday(ws)
        pd_day = _iso_date(pay_day)
        mode_norm = norm(mode) if mode else "CICLO"
        if mode_norm not in ("CICLO", "CAIXA"):
            mode_norm = "CICLO"

        farm_list = None
        if farmacias:
            farm_list = [f.strip() for f in farmacias.split(",") if f.strip()]
        elif farmacia:
            farm_list = [farmacia]

        def apply_farm_filter(q):
            if farm_list:
                return q.in_("centro_custo_final", farm_list)
            return q

        # Pagamento (rodada) - base para ciclos incluídos
        pay_rate_rows = _fetch_all_filtered(
            db,
            "coop_conciliacao_entregas",
            COOP_CONCILIACAO_AUDIT_SELECT,
            lambda q: apply_farm_filter(q)
            .eq("plano_contas", "PAGAMENTO DE ENTREGADORES")
            .eq("linha_tipo", "RATEIO")
            .eq("data_mov", pd_day),
            chunk=1000,
            max_rows=200000,
        )
        df_pay = pd.DataFrame(pay_rate_rows)
        if not df_pay.empty:
            df_pay["centro"] = df_pay["centro_custo_final"].fillna("Não Especificado")
            df_pay = df_pay[df_pay["centro"].apply(norm) != "NAO ESPECIFICADO"]
            df_pay["valor_rateado"] = pd.to_numeric(df_pay["valor_rateado"], errors="coerce").fillna(0.0)
            df_pay["pi"] = pd.to_datetime(df_pay["periodo_inicio"].astype(str).str.slice(0, 10), errors="coerce").dt.date
            df_pay["pf"] = pd.to_datetime(df_pay["periodo_fim"].astype(str).str.slice(0, 10), errors="coerce").dt.date

        # Separar ciclos futuros (auditoria) para manter consistência com o relatório principal.
        df_pay_valid = df_pay
        df_pay_future = pd.DataFrame()
        if not df_pay.empty and "pi" in df_pay.columns and "pf" in df_pay.columns:
            pd_day_d = pd.to_datetime(str(pd_day)[:10], errors="coerce").date()
            mask_future = (df_pay["pi"] > pd_day_d) | (df_pay["pf"] > pd_day_d)
            df_pay_future = df_pay[mask_future].copy()
            df_pay_valid = df_pay[~mask_future].copy()

        # MOV (controle de rateio) - mesma rodada (para auditoria)
        pay_mov_rows = _fetch_all_filtered(
            db,
            "coop_conciliacao_entregas",
            COOP_CONCILIACAO_AUDIT_SELECT,
            lambda q: q.eq("plano_contas", "PAGAMENTO DE ENTREGADORES").eq("linha_tipo", "MOV").eq("data_mov", pd_day),
            chunk=1000,
            max_rows=200000,
        )
        df_pay_mov = pd.DataFrame(pay_mov_rows)
        if not df_pay_mov.empty:
            df_pay_mov["valor_mov"] = pd.to_numeric(df_pay_mov["valor_mov"], errors="coerce").fillna(0.0)

        ciclos = (
            df_pay_valid[["periodo_inicio", "periodo_fim"]].dropna().drop_duplicates().to_dict(orient="records")
            if not df_pay_valid.empty
            else []
        )
        wanted = set()
        ciclos_futuros = (
            df_pay_future[["periodo_inicio", "periodo_fim"]].dropna().drop_duplicates().to_dict(orient="records")
            if not df_pay_future.empty
            else []
        )
        wanted_future = set()
        min_pi_d = None
        max_pf_d = None
        for c in ciclos:
            pi = c.get("periodo_inicio")
            pf = c.get("periodo_fim")
            if not pi or not pf:
                continue
            di = pd.to_datetime(str(pi)[:10], errors="coerce")
            df_ = pd.to_datetime(str(pf)[:10], errors="coerce")
            if pd.isna(di) or pd.isna(df_):
                continue
            di = di.date()
            df_ = df_.date()
            if di > df_:
                di, df_ = df_, di
            wanted.add((di, df_))
            min_pi_d = di if min_pi_d is None else min(min_pi_d, di)
            max_pf_d = df_ if max_pf_d is None else max(max_pf_d, df_)

        min_pi_future = None
        max_pf_future = None
        for c in ciclos_futuros:
            pi = c.get("periodo_inicio")
            pf = c.get("periodo_fim")
            if not pi or not pf:
                continue
            di = pd.to_datetime(str(pi)[:10], errors="coerce")
            df_ = pd.to_datetime(str(pf)[:10], errors="coerce")
            if pd.isna(di) or pd.isna(df_):
                continue
            di = di.date()
            df_ = df_.date()
            if di > df_:
                di, df_ = df_, di
            wanted_future.add((di, df_))
            min_pi_future = di if min_pi_future is None else min(min_pi_future, di)
            max_pf_future = df_ if max_pf_future is None else max(max_pf_future, df_)

        def date_in_any_range(d):
            if not d or not wanted:
                return False
            for a, b in wanted:
                if a <= d <= b:
                    return True
            return False

        # Receita (caixa semana)
        rec_cash_rows = _fetch_all_filtered(
            db,
            "coop_conciliacao_entregas",
            COOP_CONCILIACAO_AUDIT_SELECT,
            lambda q: apply_farm_filter(q)
            .eq("entrada_saida", "ENTRADA")
            .eq("plano_contas", "RECEITAS COM ENTREGAS")
            .eq("linha_tipo", "RATEIO")
            .gte("data_mov", ws)
            .lte("data_mov", we),
            chunk=1000,
            max_rows=200000,
        )
        df_rec_cash = pd.DataFrame(rec_cash_rows)
        if not df_rec_cash.empty:
            df_rec_cash["centro"] = df_rec_cash["centro_custo_final"].fillna("Não Especificado")
            df_rec_cash = df_rec_cash[df_rec_cash["centro"].apply(norm) != "NAO ESPECIFICADO"]
            df_rec_cash["valor_rateado"] = pd.to_numeric(df_rec_cash["valor_rateado"], errors="coerce").fillna(0.0)

        # Receita (ciclo - ciclos pagos)
        df_rec_cycle = pd.DataFrame()
        df_rec_cycle_future = pd.DataFrame()
        if min_pi_d and max_pf_d and wanted:
            rec_cycle_rows = _fetch_all_filtered(
                db,
                "coop_conciliacao_entregas",
                COOP_CONCILIACAO_AUDIT_SELECT,
                lambda q: apply_farm_filter(q)
                .eq("entrada_saida", "ENTRADA")
                .eq("plano_contas", "RECEITAS COM ENTREGAS")
                .eq("linha_tipo", "RATEIO")
                .not_.is_("periodo_inicio", "null")
                .not_.is_("periodo_fim", "null")
                .gte("periodo_fim", str(min_pi_d))
                .lte("periodo_fim", str(max_pf_d)),
                chunk=1000,
                max_rows=400000,
            )
            df_rec_cycle = pd.DataFrame(rec_cycle_rows)
            if not df_rec_cycle.empty:
                df_rec_cycle["centro"] = df_rec_cycle["centro_custo_final"].fillna("Não Especificado")
                df_rec_cycle = df_rec_cycle[df_rec_cycle["centro"].apply(norm) != "NAO ESPECIFICADO"]
                df_rec_cycle["valor_rateado"] = pd.to_numeric(df_rec_cycle["valor_rateado"], errors="coerce").fillna(0.0)
                df_rec_cycle["pi"] = pd.to_datetime(df_rec_cycle["periodo_inicio"].astype(str).str.slice(0, 10), errors="coerce").dt.date
                df_rec_cycle["pf"] = pd.to_datetime(df_rec_cycle["periodo_fim"].astype(str).str.slice(0, 10), errors="coerce").dt.date
                keys = list(zip(df_rec_cycle["pi"].tolist(), df_rec_cycle["pf"].tolist()))
                ks = pd.Series(keys)
                if wanted:
                    df_rec_cycle = df_rec_cycle[ks.isin(wanted).values]
                else:
                    df_rec_cycle = df_rec_cycle.iloc[0:0].copy()

        # Receita (ciclo futuro - apenas auditoria)
        if min_pi_future and max_pf_future and wanted_future:
            rec_cycle_future_rows = _fetch_all_filtered(
                db,
                "coop_conciliacao_entregas",
                COOP_CONCILIACAO_AUDIT_SELECT,
                lambda q: apply_farm_filter(q)
                .eq("entrada_saida", "ENTRADA")
                .eq("plano_contas", "RECEITAS COM ENTREGAS")
                .eq("linha_tipo", "RATEIO")
                .not_.is_("periodo_inicio", "null")
                .not_.is_("periodo_fim", "null")
                .gte("periodo_fim", str(min_pi_future))
                .lte("periodo_fim", str(max_pf_future)),
                chunk=1000,
                max_rows=400000,
            )
            df_rec_cycle_future = pd.DataFrame(rec_cycle_future_rows)
            if not df_rec_cycle_future.empty:
                df_rec_cycle_future["centro"] = df_rec_cycle_future["centro_custo_final"].fillna("Nao Especificado")
                df_rec_cycle_future = df_rec_cycle_future[df_rec_cycle_future["centro"].apply(norm) != "NAO ESPECIFICADO"]
                df_rec_cycle_future["valor_rateado"] = pd.to_numeric(df_rec_cycle_future["valor_rateado"], errors="coerce").fillna(0.0)
                df_rec_cycle_future["pi"] = pd.to_datetime(
                    df_rec_cycle_future["periodo_inicio"].astype(str).str.slice(0, 10), errors="coerce"
                ).dt.date
                df_rec_cycle_future["pf"] = pd.to_datetime(
                    df_rec_cycle_future["periodo_fim"].astype(str).str.slice(0, 10), errors="coerce"
                ).dt.date
                keys_f = list(zip(df_rec_cycle_future["pi"].tolist(), df_rec_cycle_future["pf"].tolist()))
                ks_f = pd.Series(keys_f)
                df_rec_cycle_future = df_rec_cycle_future[ks_f.isin(wanted_future).values].copy()

        # Diárias (ciclo)
        df_diaria = pd.DataFrame()
        df_diaria_future = pd.DataFrame()
        if min_pi_d and max_pf_d and wanted:
            # Prioriza o período apurado (periodo_inicio/fim) para casar com os ciclos.
            diaria_rows_periodo = _fetch_all_filtered(
                db,
                "coop_conciliacao_entregas",
                COOP_CONCILIACAO_AUDIT_SELECT,
                lambda q: apply_farm_filter(q)
                .eq("entrada_saida", "SAÍDA")
                .eq("plano_contas", "DIARIA DE MOTOBOY")
                .eq("linha_tipo", "RATEIO")
                .not_.is_("periodo_inicio", "null")
                .not_.is_("periodo_fim", "null")
                .gte("periodo_fim", str(min_pi_d))
                .lte("periodo_fim", str(max_pf_d)),
                chunk=1000,
                max_rows=200000,
            )
            diaria_rows_sem_periodo_a = _fetch_all_filtered(
                db,
                "coop_conciliacao_entregas",
                COOP_CONCILIACAO_AUDIT_SELECT,
                lambda q: apply_farm_filter(q)
                .eq("entrada_saida", "SAÍDA")
                .eq("plano_contas", "DIARIA DE MOTOBOY")
                .eq("linha_tipo", "RATEIO")
                .is_("periodo_inicio", "null")
                .gte("data_mov", str(min_pi_d))
                .lte("data_mov", str(max_pf_d)),
                chunk=1000,
                max_rows=200000,
            )
            diaria_rows_sem_periodo_b = _fetch_all_filtered(
                db,
                "coop_conciliacao_entregas",
                COOP_CONCILIACAO_AUDIT_SELECT,
                lambda q: apply_farm_filter(q)
                .eq("entrada_saida", "SAÍDA")
                .eq("plano_contas", "DIARIA DE MOTOBOY")
                .eq("linha_tipo", "RATEIO")
                .is_("periodo_fim", "null")
                .gte("data_mov", str(min_pi_d))
                .lte("data_mov", str(max_pf_d)),
                chunk=1000,
                max_rows=200000,
            )
            df_diaria = pd.DataFrame((diaria_rows_periodo or []) + (diaria_rows_sem_periodo_a or []) + (diaria_rows_sem_periodo_b or []))
            if not df_diaria.empty:
                df_diaria["centro"] = df_diaria["centro_custo_final"].fillna("Não Especificado")
                df_diaria = df_diaria[df_diaria["centro"].apply(norm) != "NAO ESPECIFICADO"]
                df_diaria["valor_rateado"] = pd.to_numeric(df_diaria["valor_rateado"], errors="coerce").fillna(0.0)
                df_diaria["pi"] = pd.to_datetime(df_diaria["periodo_inicio"].astype(str).str.slice(0, 10), errors="coerce").dt.date
                df_diaria["pf"] = pd.to_datetime(df_diaria["periodo_fim"].astype(str).str.slice(0, 10), errors="coerce").dt.date
                df_diaria["dm"] = pd.to_datetime(df_diaria["data_mov"].astype(str).str.slice(0, 10), errors="coerce").dt.date

                df_diaria["dedup_key"] = (
                    df_diaria.get("centro_custo_final").astype(str)
                    + "|"
                    + df_diaria.get("registro_movimento").astype(str)
                    + "|"
                    + df_diaria.get("registro_compromisso").astype(str)
                    + "|"
                    + df_diaria.get("numero_docto").astype(str)
                    + "|"
                    + df_diaria.get("valor_rateado").astype(str)
                    + "|"
                    + df_diaria.get("periodo_inicio").astype(str)
                    + "|"
                    + df_diaria.get("periodo_fim").astype(str)
                )
                df_diaria = df_diaria.drop_duplicates(subset=["dedup_key"])
                keys = list(zip(df_diaria["pi"].tolist(), df_diaria["pf"].tolist()))
                ks = pd.Series(keys)

                if wanted:
                    mask_pair = ks.isin(wanted).values
                    df_pair = df_diaria[mask_pair]
                    df_no_pair = df_diaria[~mask_pair]
                    # Fallback por data_mov somente quando não existir período apurado.
                    df_no_pair = df_no_pair[df_no_pair["pi"].isna() | df_no_pair["pf"].isna()]
                    df_no_pair = df_no_pair[df_no_pair["dm"].apply(date_in_any_range)]
                    df_diaria = pd.concat([df_pair, df_no_pair], ignore_index=True)
                else:
                    df_diaria = df_diaria.iloc[0:0].copy()

        # Diarias (ciclo futuro - apenas auditoria)
        if min_pi_future and max_pf_future and wanted_future:
            diaria_future_rows = _fetch_all_filtered(
                db,
                "coop_conciliacao_entregas",
                COOP_CONCILIACAO_AUDIT_SELECT,
                lambda q: apply_farm_filter(q)
                .eq("entrada_saida", "SAÃDA")
                .eq("plano_contas", "DIARIA DE MOTOBOY")
                .eq("linha_tipo", "RATEIO")
                .not_.is_("periodo_inicio", "null")
                .not_.is_("periodo_fim", "null")
                .gte("periodo_fim", str(min_pi_future))
                .lte("periodo_fim", str(max_pf_future)),
                chunk=1000,
                max_rows=200000,
            )
            df_diaria_future = pd.DataFrame(diaria_future_rows)
            if not df_diaria_future.empty:
                df_diaria_future["centro"] = df_diaria_future["centro_custo_final"].fillna("Nao Especificado")
                df_diaria_future = df_diaria_future[df_diaria_future["centro"].apply(norm) != "NAO ESPECIFICADO"]
                df_diaria_future["valor_rateado"] = pd.to_numeric(df_diaria_future["valor_rateado"], errors="coerce").fillna(0.0)
                df_diaria_future["pi"] = pd.to_datetime(
                    df_diaria_future["periodo_inicio"].astype(str).str.slice(0, 10), errors="coerce"
                ).dt.date
                df_diaria_future["pf"] = pd.to_datetime(
                    df_diaria_future["periodo_fim"].astype(str).str.slice(0, 10), errors="coerce"
                ).dt.date
                keys_f = list(zip(df_diaria_future["pi"].tolist(), df_diaria_future["pf"].tolist()))
                ks_f = pd.Series(keys_f)
                df_diaria_future = df_diaria_future[ks_f.isin(wanted_future).values].copy()

        # Ajustes (global)
        disc_rows = _fetch_all_filtered(
            db,
            "coop_conciliacao_entregas",
            COOP_CONCILIACAO_AUDIT_SELECT,
            lambda q: q.eq("plano_contas", "ENTRADA DE REEMBOLSO ADIANTAMENTO")
            .eq("linha_tipo", "MOV")
            .eq("entrada_saida", "ENTRADA")
            .eq("data_mov", pd_day),
            chunk=1000,
            max_rows=200000,
        )
        df_disc = pd.DataFrame(disc_rows)
        if not df_disc.empty:
            df_disc["valor_mov"] = pd.to_numeric(df_disc["valor_mov"], errors="coerce").fillna(0.0)

        df_adi_out = pd.DataFrame()
        df_adi_in = pd.DataFrame()
        if min_pi_d and max_pf_d and wanted:
            adi_out_rows = _fetch_all_filtered(
                db,
                "coop_conciliacao_entregas",
                COOP_CONCILIACAO_AUDIT_SELECT,
                lambda q: q.eq("plano_contas", "SOLICITACAO DE ADIANTAMENTO")
                .eq("linha_tipo", "MOV")
                .eq("entrada_saida", "SAÍDA")
                .gte("data_mov", str(min_pi_d))
                .lte("data_mov", str(max_pf_d)),
                chunk=1000,
                max_rows=200000,
            )
            df_adi_out = pd.DataFrame(adi_out_rows)
            if not df_adi_out.empty:
                df_adi_out["dm"] = pd.to_datetime(df_adi_out["data_mov"].astype(str).str.slice(0, 10), errors="coerce").dt.date
                df_adi_out = df_adi_out[df_adi_out["dm"].apply(date_in_any_range)]
                df_adi_out["valor_mov"] = pd.to_numeric(df_adi_out["valor_mov"], errors="coerce").fillna(0.0)

            adi_in_rows = _fetch_all_filtered(
                db,
                "coop_conciliacao_entregas",
                COOP_CONCILIACAO_AUDIT_SELECT,
                lambda q: q.eq("plano_contas", "ENTRADA DE REEMBOLSO ADIANTAMENTO")
                .eq("linha_tipo", "MOV")
                .eq("entrada_saida", "ENTRADA")
                .gte("data_mov", str(min_pi_d))
                .lte("data_mov", str(max_pf_d)),
                chunk=1000,
                max_rows=200000,
            )
            df_adi_in = pd.DataFrame(adi_in_rows)
            if not df_adi_in.empty:
                df_adi_in["dm"] = pd.to_datetime(df_adi_in["data_mov"].astype(str).str.slice(0, 10), errors="coerce").dt.date
                df_adi_in = df_adi_in[df_adi_in["dm"].apply(date_in_any_range)]
                df_adi_in["valor_mov"] = pd.to_numeric(df_adi_in["valor_mov"], errors="coerce").fillna(0.0)

        receita_base = float(df_rec_cycle["valor_rateado"].sum() if mode_norm == "CICLO" and not df_rec_cycle.empty else df_rec_cash["valor_rateado"].sum() if not df_rec_cash.empty else 0.0)
        pago = float(df_pay_valid["valor_rateado"].sum() if not df_pay_valid.empty else 0.0)
        pago_futuro = float(df_pay_future["valor_rateado"].sum() if not df_pay_future.empty else 0.0)
        diaria = float(df_diaria["valor_rateado"].sum() if not df_diaria.empty else 0.0)
        diaria_futura = float(df_diaria_future["valor_rateado"].sum() if not df_diaria_future.empty else 0.0)
        custo_bruto = pago + diaria
        custo_bruto_futuro = pago_futuro + diaria_futura
        desconto = float(df_disc["valor_mov"].sum() if not df_disc.empty else 0.0)
        pagamento_liquido = custo_bruto - desconto
        saldo_bruto = receita_base - custo_bruto
        saldo_liquido = receita_base - pagamento_liquido

        # Export orientado Ã  auditoria (poucas abas, colunas legÃ­veis)
        report_farmacias = ",".join(farm_list) if farm_list else None
        report_mode = "caixa" if mode_norm == "CAIXA" else "ciclo"
        report = coop_conciliacao_report(
            cash_week_start=str(ws),
            pay_day=str(pd_day),
            mode=report_mode,
            farmacias=report_farmacias,
            only_divergent=False,
            db=db,
        )

        def _to_br_date(v) -> str:
            if v is None:
                return ""
            d = pd.to_datetime(str(v)[:10], errors="coerce")
            if pd.isna(d):
                return str(v)
            return d.strftime("%d/%m/%Y")

        def _cycles_to_str(cycles) -> str:
            if not cycles:
                return ""
            parts = []
            for c in cycles:
                if isinstance(c, dict):
                    pi = c.get("periodo_inicio") or c.get("pi")
                    pf = c.get("periodo_fim") or c.get("pf")
                elif isinstance(c, (list, tuple)) and len(c) >= 2:
                    pi, pf = c[0], c[1]
                else:
                    continue
                parts.append(f"{_to_br_date(pi)} a {_to_br_date(pf)}")
            return " ; ".join([p for p in parts if p])

        df_recon = pd.DataFrame(report.get("rows") or [])
        if not df_recon.empty:
            df_recon["ciclos"] = df_recon["ciclos"].apply(_cycles_to_str)
            df_recon["ciclos_futuros"] = df_recon["ciclos_futuros"].apply(_cycles_to_str)
            df_recon = df_recon.rename(
                columns={
                    "farmacia": "Farmacia",
                    "receita_ciclos_apurados": "Receita (ciclos pagos)",
                    "receita_semana_caixa": "Receita (caixa semana)",
                    "pagamento_quinta": "Pagamento (rodada)",
                    "diarias_ciclo": "Diarias (ciclo)",
                    "custo_bruto": "Custo bruto (pag + diarias)",
                    "saldo": "Saldo (ciclo)",
                    "saldo_caixa": "Saldo (caixa)",
                    "pct_repasse": "% Repasse (ciclo)",
                    "pct_repasse_caixa": "% Repasse (caixa)",
                    "ciclos": "Ciclos (apurados)",
                    "pagamento_futuro": "Pagamento futuro (excl.)",
                    "diarias_futuro": "Diarias futuro (excl.)",
                    "ciclos_futuros": "Ciclos futuros (excl.)",
                }
            )

        totals = report.get("totals") or {}
        controle = report.get("controle") or {}
        label_map = {
            "receita_semana_caixa": "Receita Entregas (caixa semana)",
            "receita_ciclos_apurados": "Receita Entregas (ciclos pagos)",
            "pagamento_quinta": "Pagamento Entregadores (rodada)",
            "diarias_ciclo": "Diarias (ciclo)",
            "pagamento_futuro": "Pagamento com ciclo futuro (excluido)",
            "diarias_futuro": "Diarias com ciclo futuro (excluidas)",
            "desconto_adiantamento_rodada": "Desconto adiantamento (rodada)",
            "adiantado_ciclo": "Adiantamentos concedidos (ciclo)",
            "reembolsado_ciclo": "Reembolso de adiantamentos (ciclo)",
            "custo_bruto": "Custo bruto (pagamento + diarias)",
            "custo_bruto_futuro": "Custo bruto futuro (excluido)",
            "custo_bruto_total_com_futuro": "Custo bruto total (inclui futuros)",
            "pagamento_liquido": "Pagamento liquido (c/ desconto adiant.)",
            "saldo": "Saldo (ciclo)",
            "saldo_caixa": "Saldo (caixa)",
            "saldo_liquido": "Saldo liquido (ciclo)",
            "saldo_caixa_liquido": "Saldo liquido (caixa)",
            "adiantamento_liquido_ciclo": "Adiantamento liquido no ciclo",
            "pagamento_mov_total": "Controle pagamento (MOV total)",
            "pagamento_rateado_total": "Controle pagamento (RATEIO total)",
            "pagamento_rateado_valid_total": "Controle pagamento (RATEIO valido)",
            "pagamento_rateado_futuro_total": "Controle pagamento (RATEIO futuro)",
            "pagamento_diff": "Controle pagamento (dif. MOV-RATEIO)",
        }
        resumo_rows = [
            {"Campo": "Rodada (pagamento)", "Valor": str(pd_day)},
            {"Campo": "Semana (caixa)", "Valor": f"{_to_br_date(ws)} a {_to_br_date(we)}"},
            {"Campo": "Modo (base de receita)", "Valor": "Caixa" if mode_norm == "CAIXA" else "Ciclo"},
            {"Campo": "Farmacias filtradas", "Valor": report_farmacias or "Todas"},
        ]
        for k, v in totals.items():
            resumo_rows.append({"Campo": label_map.get(k, k), "Valor": v})
        for k, v in controle.items():
            resumo_rows.append({"Campo": label_map.get(k, k), "Valor": v})
        df_resumo = pd.DataFrame(resumo_rows)

        out_cols = [
            "Categoria no relatorio",
            "Incluido no calculo",
            "Motivo exclusao",
            "DATA MOVIMENTO",
            "PAGAMENTO",
            "ENTRADA/SAIDA",
            "PLANO DE CONTAS",
            "PARCEIRO",
            "REFERENTE",
            "NUMERO DOCTO",
            "REGISTRO MOVIMENTO",
            "REGISTRO COMPROMISSO",
            "CENTRO DE CUSTO (FARMACIA PRESTADORA)",
            "CENTRO DE CUSTO (RAW)",
            "VALOR MOV.CONTA",
            "VALOR RATEADO PARA O PRESTADOR",
            "LINHA TIPO",
            "PERIODO INICIO",
            "PERIODO FIM",
            "SEMANA INICIO",
            "SEMANA FIM",
        ]

        def _audit_block(df: pd.DataFrame, categoria: str, incluido: bool, motivo: str = "") -> pd.DataFrame:
            if df is None or df.empty:
                return pd.DataFrame(columns=out_cols)
            x = df.copy()
            x["Categoria no relatorio"] = categoria
            x["Incluido no calculo"] = "Sim" if incluido else "Nao"
            x["Motivo exclusao"] = motivo or ""
            x["DATA MOVIMENTO"] = x.get("data_mov")
            x["PAGAMENTO"] = x.get("data_pag")
            x["ENTRADA/SAIDA"] = x.get("entrada_saida")
            x["PLANO DE CONTAS"] = x.get("plano_contas")
            x["PARCEIRO"] = x.get("parceiro")
            x["REFERENTE"] = x.get("referente")
            x["NUMERO DOCTO"] = x.get("numero_docto")
            x["REGISTRO MOVIMENTO"] = x.get("registro_movimento")
            x["REGISTRO COMPROMISSO"] = x.get("registro_compromisso")
            x["CENTRO DE CUSTO (FARMACIA PRESTADORA)"] = x.get("centro_custo_final")
            x["CENTRO DE CUSTO (RAW)"] = x.get("centro_custo_raw")
            x["VALOR MOV.CONTA"] = x.get("valor_mov")
            x["VALOR RATEADO PARA O PRESTADOR"] = x.get("valor_rateado")
            x["LINHA TIPO"] = x.get("linha_tipo")
            x["PERIODO INICIO"] = x.get("periodo_inicio")
            x["PERIODO FIM"] = x.get("periodo_fim")
            x["SEMANA INICIO"] = x.get("semana_inicio")
            x["SEMANA FIM"] = x.get("semana_fim")
            return x[out_cols]

        def _concat_or_empty(frames: list[pd.DataFrame], columns: list[str]) -> pd.DataFrame:
            ok = [f for f in (frames or []) if f is not None and not f.empty]
            if not ok:
                return pd.DataFrame(columns=columns)
            return pd.concat(ok, ignore_index=True)

        blocks_used = [
            _audit_block(df_rec_cash, "Receita Entregas (caixa semana)", True),
            _audit_block(df_rec_cycle, "Receita Entregas (ciclos pagos)", True),
            _audit_block(df_pay_mov, "Pagamento Entregadores (rodada) - MOV (controle)", True),
            _audit_block(df_pay_valid, "Pagamento Entregadores (rodada) - RATEIO", True),
            _audit_block(df_diaria, "Diarias Motoboy (ciclos pagos) - RATEIO", True),
            _audit_block(df_disc, "Desconto adiantamento (rodada) - MOV", True),
            _audit_block(df_adi_out, "Adiantamento concedido (ciclo) - MOV", True),
            _audit_block(df_adi_in, "Reembolso adiantamento (ciclo) - MOV", True),
        ]
        df_audit = _concat_or_empty(blocks_used, out_cols)

        blocks_excluded = [
            _audit_block(df_pay_future, "Pagamento Entregadores (rodada) - RATEIO", False, "Ciclo futuro (> rodada)"),
            _audit_block(df_rec_cycle_future, "Receita Entregas (ciclos)", False, "Ciclo futuro (> rodada)"),
            _audit_block(df_diaria_future, "Diarias Motoboy (ciclos) - RATEIO", False, "Ciclo futuro (> rodada)"),
        ]
        df_excluded = _concat_or_empty(blocks_excluded, out_cols)

        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            df_resumo.to_excel(writer, index=False, sheet_name="Resumo")
            df_recon.to_excel(writer, index=False, sheet_name="Conciliacao")
            df_audit.to_excel(writer, index=False, sheet_name="Registros_usados")
            df_excluded.to_excel(writer, index=False, sheet_name="Excluidos")

        buf.seek(0)
        fname_base = (farmacia or "todas").replace(" ", "_")
        fname = f"auditoria_conferencia_semanal_{fname_base}_{pd_day}.xlsx"
        headers = {"Content-Disposition": f'attachment; filename="{fname}"'}
        return StreamingResponse(
            buf,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers=headers,
        )
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

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

# Cache em memória (processo) para evitar recalcular alíquota média do Simples (Flux) a cada request.
# Estrutura: {"rate": float|None, "at": datetime|None}
_SIMPLES_RATE_FLUX_CACHE = {"rate": None, "at": None}

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

        # Repasse ao entregador (somente Cooperativa): soma de PAGAMENTO DE ENTREGADORES + DIARIA(S) DE MOTOBOY
        # Base: SAIDA, apenas centros elegiveis (farmacias que faturaram em ENTREGAS no mes).
        repasse_pag_dict = {}
        repasse_diaria_dict = {}
        if context == "coop":
            try:
                df_repasse = df_base_all[
                    (df_base_all["entrada_norm"] == "SAIDA") & (df_base_all["centro_custo"].apply(is_centro_elegivel))
                ].copy()
                df_repasse = df_repasse[
                    df_repasse["plano_norm"].str.contains("PAGAMENTO DE ENTREGADOR|DIARIA DE MOTOBOY", na=False)
                ].copy()
                if not df_repasse.empty:
                    df_repasse["valor_abs"] = df_repasse["valor"].abs()
                    repasse_pag_dict = (
                        df_repasse[df_repasse["plano_norm"].str.contains("PAGAMENTO DE ENTREGADOR", na=False)]
                        .groupby(["centro_custo", "mes_ano"])["valor_abs"]
                        .sum()
                        .to_dict()
                    )
                    repasse_diaria_dict = (
                        df_repasse[df_repasse["plano_norm"].str.contains("DIARIA DE MOTOBOY", na=False)]
                        .groupby(["centro_custo", "mes_ano"])["valor_abs"]
                        .sum()
                        .to_dict()
                    )
            except Exception as e:
                logger.warning(f"[ranking] Falha ao calcular repasse entregador (coop): {e}")

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

            # Repasse ao entregador (coop): pagamento + diarias / receita_coop (entregas) do mes
            repasse_pag = float(repasse_pag_dict.get((farmacia, mes_ano), 0) or 0)
            repasse_diarias = float(repasse_diaria_dict.get((farmacia, mes_ano), 0) or 0)
            repasse_total = repasse_pag + repasse_diarias
            repasse_pct = (repasse_total / receita_bruta * 100) if receita_bruta else 0.0

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
                    "repasse_entregador_pagamento": float(repasse_pag),
                    "repasse_entregador_diarias": float(repasse_diarias),
                    "repasse_entregador_total": float(repasse_total),
                    "repasse_entregador_pct": float(repasse_pct),
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
                    # Coop: repasse entregador (pagamento + diarias), base Receita Coop
                    "repasse_entregador_total": 0.0,
                    "repasse_entregador_pct": 0.0,
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
                merged[k]["repasse_entregador_total"] = to_float(row.get("repasse_entregador_total"))
                merged[k]["repasse_entregador_pct"] = to_float(row.get("repasse_entregador_pct"))

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
            pct_flux_do_total = (to_float(r.get("receita_flux")) / receita_total * 100) if receita_total else 0.0

            rows.append(
                {
                    **r,
                    # Alias: "Faturado p/ Flux" (ENTREGAS) = receita_flux
                    "faturado_flux": float(to_float(r.get("receita_flux"))),
                    "receita_total": float(receita_total),
                    "pct_flux_do_total": float(pct_flux_do_total),
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
        try:
            batch = q.range(start, start + chunk - 1).execute().data or []
        except Exception:
            # Alguns endpoints do PostgREST/Supabase retornam 400 em ranges além do total.
            # Se já buscamos pelo menos 1 página, tratamos como fim da paginação.
            if start == 0:
                raise
            break
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
            "centro_custo_final,centro_custo_raw,valor_rateado,periodo_inicio,periodo_fim,parceiro,referente,numero_docto,registro_movimento,registro_compromisso,data_mov",
            lambda q: apply_farm_filter(q)
            .eq("plano_contas", "PAGAMENTO DE ENTREGADORES")
            .eq("linha_tipo", "RATEIO")
            .eq("data_mov", pd_day),
            chunk=1000,
            max_rows=200000,
        )
        df_pay_all = pd.DataFrame(pay_rate_rows)
        df_pay = pd.DataFrame()
        df_pay_unclassified_valid = pd.DataFrame()
        df_pay_unclassified_future = pd.DataFrame()
        df_pay_all_valid = pd.DataFrame()
        df_pay_all_future = pd.DataFrame()
        if not df_pay_all.empty:
            df_pay_all["centro"] = df_pay_all["centro_custo_final"].fillna("Não Especificado")
            df_pay_all["centro_norm"] = df_pay_all["centro"].apply(norm)
            df_pay_all["valor_rateado"] = pd.to_numeric(df_pay_all["valor_rateado"], errors="coerce").fillna(0.0)
            df_pay_all["pi"] = pd.to_datetime(df_pay_all["periodo_inicio"].astype(str).str.slice(0, 10), errors="coerce").dt.date
            df_pay_all["pf"] = pd.to_datetime(df_pay_all["periodo_fim"].astype(str).str.slice(0, 10), errors="coerce").dt.date

            # Excluir ciclos FUTUROS (periodo_inicio/fim > pay_day) do cálculo principal.
            # Eles representam antecipações/lançamentos fora do ciclo e serão listados separadamente.
            pd_day_d = pd.to_datetime(str(pd_day)[:10], errors="coerce").date()
            mask_future_all = (df_pay_all["pi"] > pd_day_d) | (df_pay_all["pf"] > pd_day_d)
            df_pay_all_future = df_pay_all[mask_future_all].copy()
            df_pay_all_valid = df_pay_all[~mask_future_all].copy()

            # Mantém separado o que não tem farmácia (erro operacional de lançamento)
            df_pay_unclassified_valid = df_pay_all_valid[df_pay_all_valid["centro_norm"] == "NAO ESPECIFICADO"].copy()
            df_pay_unclassified_future = df_pay_all_future[df_pay_all_future["centro_norm"] == "NAO ESPECIFICADO"].copy()

            # Pagamentos classificados por farmácia (lista principal)
            df_pay = df_pay_all[df_pay_all["centro_norm"] != "NAO ESPECIFICADO"].copy()

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

        meta: dict[str, object] = {
            "pay_rateio_rows_total": int(len(df_pay_all)) if df_pay_all is not None else 0,
            "pay_rateio_rows_unclassified": int(len(df_pay_unclassified_valid) + len(df_pay_unclassified_future)) if df_pay_all is not None else 0,
            "pay_rateio_rows_classified": int(len(df_pay)) if df_pay is not None else 0,
            "pay_rateio_rows_valid": int(len(df_pay_valid)) if df_pay_valid is not None else 0,
            "pay_rateio_rows_future": int(len(df_pay_future)) if df_pay_future is not None else 0,
            "pay_farms_total": int(df_pay["centro"].nunique()) if (df_pay is not None and not df_pay.empty and "centro" in df_pay.columns) else 0,
            "pay_farms_valid": int(df_pay_valid["centro"].nunique()) if (df_pay_valid is not None and not df_pay_valid.empty and "centro" in df_pay_valid.columns) else 0,
            "pay_farms_future": int(df_pay_future["centro"].nunique()) if (df_pay_future is not None and not df_pay_future.empty and "centro" in df_pay_future.columns) else 0,
        }

        # Conjunto de ciclos na rodada (inclui lançamentos sem farmácia; usado para auditoria)
        rodada_ranges: set[tuple[date, date]] = set()
        if df_pay_all_valid is not None and not df_pay_all_valid.empty and "pi" in df_pay_all_valid.columns and "pf" in df_pay_all_valid.columns:
            for r in df_pay_all_valid.itertuples():
                if getattr(r, "pi", None) and getattr(r, "pf", None):
                    rodada_ranges.add((r.pi, r.pf))

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
        # Janela de busca mínima considerando todos os ciclos da rodada (mesmo os sem farmácia)
        if rodada_ranges:
            min_pi_d = min(r[0] for r in rodada_ranges)
            max_pf_d = max(r[1] for r in rodada_ranges)
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
        rec_cycle_rows_total = 0
        rec_cycle_rows_considered = 0
        rec_cycle_farms_total = 0
        rec_cycle_farms_considered = 0
        ciclos_receita_por_farm: dict[str, list[dict]] = {}
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
                    df_rec_cycle["key"] = list(zip(df_rec_cycle["pi"].tolist(), df_rec_cycle["pf"].tolist()))

                    rec_cycle_rows_total = int(len(df_rec_cycle))
                    rec_cycle_farms_total = int(df_rec_cycle["centro"].nunique()) if "centro" in df_rec_cycle.columns else 0

                if df_rec_cycle is not None and not df_rec_cycle.empty and wanted_ranges_by_farm:
                    # IMPORTANTE: cada centro deve considerar apenas os ciclos que apareceram no pagamento da prÃ³pria farmÃ¡cia.
                    def _row_used_rec(r):
                        wanted = wanted_ranges_by_farm.get(r.get("centro")) or set()
                        return bool(wanted) and (r.get("key") in wanted)

                    df_cons = df_rec_cycle[df_rec_cycle.apply(_row_used_rec, axis=1)].copy()
                    rec_cycle_rows_considered = int(len(df_cons))
                    rec_cycle_farms_considered = int(df_cons["centro"].nunique()) if "centro" in df_cons.columns else 0
                    if not df_cons.empty:
                        receita_ciclos_por_farm = df_cons.groupby("centro")["valor_rateado"].sum().to_dict()
                        for centro, g in df_cons.groupby("centro"):
                            ciclos_receita_por_farm[centro] = (
                                g[["periodo_inicio", "periodo_fim"]].dropna().drop_duplicates().to_dict(orient="records")
                            )

        meta.update(
            {
                "rec_cycle_rows_total": int(rec_cycle_rows_total),
                "rec_cycle_rows_considered": int(rec_cycle_rows_considered),
                "rec_cycle_farms_total": int(rec_cycle_farms_total),
                "rec_cycle_farms_considered": int(rec_cycle_farms_considered),
            }
        )

        # Receita Flux no mesmo PERIODO APURADO do ciclo (DATA MOVIMENTO no dre_financeiro).
        # Regra: ENTRADA + PLANO DE CONTAS = "RECEITAS COM ENTREGAS".
        receita_flux_ciclos_por_farm: dict[str, float] = {}
        try:
            if wanted_ranges_by_farm and min_pi_d is not None and max_pf_d is not None:
                flux_rows = _fetch_all_filtered(
                    db,
                    "dre_financeiro",
                    "data_mov,centro_custo,valor",
                    lambda q: q.eq("entrada_saida", "ENTRADA")
                    .eq("plano_contas", "RECEITAS COM ENTREGAS")
                    .not_.is_("data_mov", "null")
                    .gte("data_mov", str(min_pi_d))
                    .lte("data_mov", str(max_pf_d)),
                    chunk=1000,
                    max_rows=400000,
                )
                df_flux = pd.DataFrame(flux_rows)
                if not df_flux.empty:
                    df_flux["dm"] = pd.to_datetime(df_flux["data_mov"].astype(str).str.slice(0, 10), errors="coerce").dt.date
                    df_flux = df_flux.dropna(subset=["dm"])
                    df_flux["valor"] = pd.to_numeric(df_flux.get("valor"), errors="coerce").fillna(0.0)
                    df_flux["centro_norm"] = df_flux.get("centro_custo").apply(norm)

                    targets = set(wanted_ranges_by_farm.keys()) | set(pay_por_farm.keys()) | set(pay_por_farm_future.keys())
                    for f in targets:
                        wanted = wanted_ranges_by_farm.get(f) or set()
                        if not wanted:
                            receita_flux_ciclos_por_farm[f] = 0.0
                            continue
                        f_norm = norm(f)
                        df_f = df_flux[df_flux["centro_norm"] == f_norm]
                        if df_f.empty:
                            receita_flux_ciclos_por_farm[f] = 0.0
                            continue
                        receita_flux_ciclos_por_farm[f] = float(
                            df_f[df_f["dm"].apply(lambda d: date_in_any_range(d, wanted))]["valor"].sum()
                        )
        except Exception as e:
            logger.warning(f"[conciliacao] Falha ao calcular receita Flux por ciclo: {e}")

        # Diárias do ciclo (custo adicional): SAÍDA + DIARIA DE MOTOBOY + RATEIO
        # Preferência: match por (periodo_inicio/fim); fallback: data_mov dentro do range do ciclo.
        diarias_por_farm: dict[str, float] = {}
        diarias_futuras_por_farm: dict[str, float] = {}
        unclassified_diarias_rows: list[dict] = []
        df_diaria = pd.DataFrame()
        if min_pi_d and max_pf_d and (all_ranges or all_ranges_future):
            # Prioriza o período apurado (periodo_inicio/fim) para casar com os ciclos do pagamento.
            diaria_rows_periodo = _fetch_all_filtered(
                db,
                "coop_conciliacao_entregas",
                "centro_custo_final,centro_custo_raw,parceiro,referente,valor_rateado,periodo_inicio,periodo_fim,data_mov,registro_movimento,registro_compromisso,numero_docto",
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
                "centro_custo_final,centro_custo_raw,parceiro,referente,valor_rateado,periodo_inicio,periodo_fim,data_mov,registro_movimento,registro_compromisso,numero_docto",
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
                "centro_custo_final,centro_custo_raw,parceiro,referente,valor_rateado,periodo_inicio,periodo_fim,data_mov,registro_movimento,registro_compromisso,numero_docto",
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
                df_diaria["centro_norm"] = df_diaria["centro"].apply(norm)
                df_diaria["valor_rateado"] = pd.to_numeric(df_diaria["valor_rateado"], errors="coerce").fillna(0.0)
                df_diaria["pi"] = pd.to_datetime(df_diaria["periodo_inicio"].astype(str).str.slice(0, 10), errors="coerce").dt.date
                df_diaria["pf"] = pd.to_datetime(df_diaria["periodo_fim"].astype(str).str.slice(0, 10), errors="coerce").dt.date
                df_diaria["dm"] = pd.to_datetime(df_diaria["data_mov"].astype(str).str.slice(0, 10), errors="coerce").dt.date

                # Separar diárias sem farmácia (auditoria de lançamentos)
                df_un_d = df_diaria[df_diaria["centro_norm"] == "NAO ESPECIFICADO"].copy()
                if not df_un_d.empty:
                    df_un_pair = df_un_d[~(df_un_d["pi"].isna() | df_un_d["pf"].isna())].copy()
                    df_un_missing = df_un_d[df_un_d["pi"].isna() | df_un_d["pf"].isna()].copy()
                    # Preferência: ciclo exato na rodada
                    if rodada_ranges and not df_un_pair.empty:
                        df_un_pair["key"] = list(zip(df_un_pair["pi"].tolist(), df_un_pair["pf"].tolist()))
                        df_un_pair = df_un_pair[df_un_pair["key"].isin(rodada_ranges)].copy()
                    # Fallback: data_mov dentro de algum ciclo da rodada
                    if rodada_ranges and not df_un_missing.empty:
                        df_un_missing = df_un_missing[df_un_missing["dm"].apply(lambda d: date_in_any_range(d, rodada_ranges))].copy()
                    df_un_used = pd.concat([df_un_pair, df_un_missing], ignore_index=True)
                    if not df_un_used.empty:
                        unclassified_diarias_rows = (
                            df_un_used[[
                                "valor_rateado",
                                "periodo_inicio",
                                "periodo_fim",
                                "data_mov",
                                "centro_custo_raw",
                                "parceiro",
                                "referente",
                                "numero_docto",
                                "registro_movimento",
                                "registro_compromisso",
                            ]]
                            .fillna("")
                            .to_dict(orient="records")
                        )

                # A partir daqui, para os cálculos por farmácia, removemos o que não tem centro (sem farmácia)
                df_diaria = df_diaria[df_diaria["centro_norm"] != "NAO ESPECIFICADO"].copy()

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

            if not df_diaria.empty:
                # Preferência: casar por par (pi,pf) pertencente ao ciclo apurado.
                df_pair = df_diaria[~(df_diaria["pi"].isna() | df_diaria["pf"].isna())].copy()
                if not df_pair.empty and wanted_ranges_by_farm:
                    df_pair["key"] = list(zip(df_pair["pi"].tolist(), df_pair["pf"].tolist()))

                    def _row_used_diaria(r):
                        wanted = wanted_ranges_by_farm.get(r.get("centro")) or set()
                        return bool(wanted) and (r.get("key") in wanted)

                    df_pair_cons = df_pair[df_pair.apply(_row_used_diaria, axis=1)]
                    diarias_por_farm = (
                        df_pair_cons.groupby("centro")["valor_rateado"].sum().to_dict() if not df_pair_cons.empty else {}
                    )

                # Fallback: somente quando NÃO houver período (pi/pf ausentes) e data_mov cair dentro do range.
                df_missing = df_diaria[df_diaria["pi"].isna() | df_diaria["pf"].isna()].copy()
                if not df_missing.empty and wanted_ranges_by_farm:
                    def _row_used_diaria_missing(r):
                        wanted = wanted_ranges_by_farm.get(r.get("centro")) or set()
                        return bool(wanted) and date_in_any_range(r.get("dm"), wanted)

                    df_missing = df_missing[df_missing.apply(_row_used_diaria_missing, axis=1)]
                    if not df_missing.empty:
                        for centro, val in df_missing.groupby("centro")["valor_rateado"].sum().to_dict().items():
                            diarias_por_farm[centro] = float(diarias_por_farm.get(centro, 0.0) or 0.0) + float(val or 0.0)

                # FUTURO (auditoria): ciclos com pi/pf após a rodada
                if not df_pair.empty and wanted_ranges_by_farm_future:
                    if "key" not in df_pair.columns:
                        df_pair["key"] = list(zip(df_pair["pi"].tolist(), df_pair["pf"].tolist()))

                    def _row_used_diaria_future(r):
                        wanted = wanted_ranges_by_farm_future.get(r.get("centro")) or set()
                        return bool(wanted) and (r.get("key") in wanted)

                    df_pair_f = df_pair[df_pair.apply(_row_used_diaria_future, axis=1)]
                    diarias_futuras_por_farm = (
                        df_pair_f.groupby("centro")["valor_rateado"].sum().to_dict() if not df_pair_f.empty else {}
                    )
                if not df_missing.empty and wanted_ranges_by_farm_future:
                    df_missing_f = df_diaria[df_diaria["pi"].isna() | df_diaria["pf"].isna()].copy()

                    def _row_used_diaria_missing_future(r):
                        wanted = wanted_ranges_by_farm_future.get(r.get("centro")) or set()
                        return bool(wanted) and date_in_any_range(r.get("dm"), wanted)

                    df_missing_f = df_missing_f[df_missing_f.apply(_row_used_diaria_missing_future, axis=1)]
                    if not df_missing_f.empty:
                        for centro, val in df_missing_f.groupby("centro")["valor_rateado"].sum().to_dict().items():
                            diarias_futuras_por_farm[centro] = float(diarias_futuras_por_farm.get(centro, 0.0) or 0.0) + float(val or 0.0)

        # Despesas do ciclo (por farmácia)
        # - ISS (Cooperativa): 3% sobre a Receita Cooperativa no ciclo
        # - INSS (Cooperativa): 4,5% sobre o repasse ao entregador no ciclo (pagamento + diárias)
        # - Bancárias (Cooperativa): IOF/JUROS/DESP.FINANCEIRA no ciclo (dre_financeiro_coop), alocadas por centro_custo quando existir,
        #   e o restante rateado proporcionalmente à Receita Cooperativa do ciclo.
        # - Simples Nacional (Flux): estimado por alíquota média histórica (mensal) aplicada sobre a Receita Flux no ciclo
        iss_por_farm: dict[str, float] = {
            f: float(receita_ciclos_por_farm.get(f, 0) or 0) * 0.03 for f in receita_ciclos_por_farm.keys()
        }

        banc_coop_por_farm: dict[str, float] = {}
        try:
            if all_ranges and min_pi_d is not None and max_pf_d is not None:
                coop_cost_rows = _fetch_all_filtered(
                    db,
                    "dre_financeiro_coop",
                    "data_mov,centro_custo,valor,plano_contas,entrada_saida",
                    lambda q: q.not_.is_("data_mov", "null")
                    .gte("data_mov", str(min_pi_d))
                    .lte("data_mov", str(max_pf_d)),
                    chunk=1000,
                    max_rows=400000,
                )
                df_cost = pd.DataFrame(coop_cost_rows)
                if df_cost is not None and not df_cost.empty:
                    df_cost["entrada_norm"] = df_cost.get("entrada_saida").apply(norm)
                    df_cost = df_cost[df_cost["entrada_norm"] == "SAIDA"]
                    df_cost["plano_norm"] = df_cost.get("plano_contas").apply(norm)
                    df_cost = df_cost[
                        df_cost["plano_norm"].apply(
                            lambda p: ("IOF" in p) or ("JUROS" in p) or ("DESP.FINANCEIRA" in p) or ("DESPESA FINANCEIRA" in p)
                        )
                    ]
                    if not df_cost.empty:
                        df_cost["dm"] = pd.to_datetime(
                            df_cost["data_mov"].astype(str).str.slice(0, 10), errors="coerce"
                        ).dt.date
                        df_cost = df_cost.dropna(subset=["dm"])
                        df_cost = df_cost[df_cost["dm"].apply(lambda d: date_in_any_range(d, all_ranges))]

                    if df_cost is not None and not df_cost.empty:
                        df_cost["valor_num"] = pd.to_numeric(df_cost.get("valor"), errors="coerce").fillna(0.0).abs()
                        df_cost["centro_norm"] = df_cost.get("centro_custo").apply(norm)

                        targets = set(receita_ciclos_por_farm.keys()) | set(pay_por_farm.keys()) | set(pay_por_farm_future.keys())
                        target_norms = {norm(f) for f in targets}

                        assigned = (
                            df_cost[(df_cost["centro_norm"] != "NAO ESPECIFICADO") & (df_cost["centro_norm"].isin(target_norms))]
                            .groupby("centro_norm")["valor_num"]
                            .sum()
                            .to_dict()
                        )
                        assigned_total = float(sum(assigned.values()) if assigned else 0.0)
                        total_cost = float(df_cost["valor_num"].sum())
                        unassigned_total = max(0.0, total_cost - assigned_total)

                        total_rec = float(sum(float(receita_ciclos_por_farm.get(f, 0) or 0.0) for f in targets))
                        for f in targets:
                            f_norm = norm(f)
                            v_ass = float(assigned.get(f_norm, 0.0) or 0.0)
                            v_un = (
                                (float(receita_ciclos_por_farm.get(f, 0) or 0.0) / total_rec * unassigned_total)
                                if total_rec > 0 and unassigned_total > 0
                                else 0.0
                            )
                            banc_coop_por_farm[f] = v_ass + v_un
        except Exception as e:
            logger.warning(f"[conciliacao] Falha ao calcular bancárias (Coop) no ciclo: {e}")
            banc_coop_por_farm = {}

        # Simples Nacional (Flux): alíquota média histórica (mensal), aplicada sobre a Receita Flux no ciclo.
        # Observação: é uma estimativa (ciclo semanal) e não substitui o cálculo oficial mensal.
        simples_rate_flux = 0.0
        # Se não houve Receita Flux no ciclo, o Simples estimado é 0 e evitamos carregar o histórico completo.
        if receita_flux_ciclos_por_farm and float(sum(receita_flux_ciclos_por_farm.values()) or 0.0) > 0:
            try:
                cache = _SIMPLES_RATE_FLUX_CACHE
                now = datetime.now()
                if cache.get("rate") is not None and cache.get("at") and (now - cache["at"]).total_seconds() < 6 * 3600:
                    simples_rate_flux = float(cache["rate"] or 0.0)
                else:
                    fin_flux = get_dre_financeiro_data(db) or []
                    df_flux_all = pd.DataFrame(fin_flux)
                    if not df_flux_all.empty:
                        df_flux_all["plano_norm"] = df_flux_all.get("plano_contas").apply(norm)
                        df_flux_all["entrada_norm"] = df_flux_all.get("entrada_saida").apply(norm)
                        df_flux_all["mes_ano"] = df_flux_all.get("mes_ano").apply(
                            lambda x: str(x).strip() if x is not None else x
                        )
                        df_flux_all["valor_num"] = pd.to_numeric(df_flux_all.get("valor"), errors="coerce").fillna(0.0)

                        rec_mes = (
                            df_flux_all[
                                (df_flux_all["entrada_norm"] == "ENTRADA")
                                & (df_flux_all["plano_norm"] == "RECEITAS COM ENTREGAS")
                            ]
                            .groupby("mes_ano")["valor_num"]
                            .sum()
                            .to_dict()
                        )
                        impostos_pag_mes = (
                            df_flux_all[
                                (df_flux_all["entrada_norm"] == "SAIDA")
                                & (df_flux_all["plano_norm"].str.contains("SIMPLES NACIONAL", na=False))
                            ]
                            .groupby("mes_ano")["valor_num"]
                            .sum()
                            .abs()
                            .to_dict()
                        )

                        def prev_month_str(mes_ano: str) -> str:
                            try:
                                dt = datetime.strptime(mes_ano + "-01", "%Y-%m-%d")
                                return (dt.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
                            except Exception:
                                return mes_ano

                        impostos_comp = {}
                        for m_pag, v in impostos_pag_mes.items():
                            m_comp = prev_month_str(str(m_pag))
                            impostos_comp[m_comp] = impostos_comp.get(m_comp, 0.0) + float(v or 0.0)

                        total_imp = 0.0
                        total_rec = 0.0
                        for m_comp, imp in impostos_comp.items():
                            rec = float(rec_mes.get(m_comp, 0.0) or 0.0)
                            if rec > 0:
                                total_imp += float(imp or 0.0)
                                total_rec += rec
                        simples_rate_flux = (total_imp / total_rec) if total_rec else 0.0

                    _SIMPLES_RATE_FLUX_CACHE["rate"] = simples_rate_flux
                    _SIMPLES_RATE_FLUX_CACHE["at"] = now
            except Exception as e:
                logger.warning(f"[conciliacao] Falha ao calcular alíquota média do Simples (Flux): {e}")
                simples_rate_flux = 0.0

        # Despesas bancárias da Flux no ciclo (rateio por receita Flux quando não houver centro_custo específico).
        banc_flux_por_farm: dict[str, float] = {}
        try:
            if all_ranges and min_pi_d is not None and max_pf_d is not None:
                flux_cost_rows = _fetch_all_filtered(
                    db,
                    "dre_financeiro",
                    "data_mov,centro_custo,valor,plano_contas,entrada_saida",
                    lambda q: q.not_.is_("data_mov", "null")
                    .gte("data_mov", str(min_pi_d))
                    .lte("data_mov", str(max_pf_d)),
                    chunk=1000,
                    max_rows=400000,
                )
                df_cost = pd.DataFrame(flux_cost_rows)
                if not df_cost.empty:
                    df_cost["entrada_norm"] = df_cost.get("entrada_saida").apply(norm)
                    df_cost = df_cost[df_cost["entrada_norm"] == "SAIDA"]
                    df_cost["plano_norm"] = df_cost.get("plano_contas").apply(norm)
                    df_cost = df_cost[
                        df_cost["plano_norm"].apply(
                            lambda p: ("IOF" in p) or ("JUROS" in p) or ("DESP.FINANCEIRA" in p) or ("DESPESA FINANCEIRA" in p)
                        )
                    ]
                if df_cost is None or df_cost.empty:
                    banc_flux_por_farm = {}
                else:
                    df_cost["dm"] = pd.to_datetime(df_cost["data_mov"].astype(str).str.slice(0, 10), errors="coerce").dt.date
                    df_cost = df_cost.dropna(subset=["dm"])
                    df_cost = df_cost[df_cost["dm"].apply(lambda d: date_in_any_range(d, all_ranges))]
                    df_cost["valor_num"] = pd.to_numeric(df_cost.get("valor"), errors="coerce").fillna(0.0).abs()
                    df_cost["centro_norm"] = df_cost.get("centro_custo").apply(norm)

                    targets = set(receita_ciclos_por_farm.keys()) | set(pay_por_farm.keys()) | set(pay_por_farm_future.keys())
                    target_norms = {norm(f) for f in targets}

                    assigned = (
                        df_cost[(df_cost["centro_norm"] != "NAO ESPECIFICADO") & (df_cost["centro_norm"].isin(target_norms))]
                        .groupby("centro_norm")["valor_num"]
                        .sum()
                        .to_dict()
                    )
                    assigned_total = float(sum(assigned.values()) if assigned else 0.0)
                    total_cost = float(df_cost["valor_num"].sum())
                    unassigned_total = max(0.0, total_cost - assigned_total)

                    total_rec_flux = float(sum(float(receita_flux_ciclos_por_farm.get(f, 0) or 0.0) for f in targets))

                    for f in targets:
                        f_norm = norm(f)
                        v_ass = float(assigned.get(f_norm, 0.0) or 0.0)
                        v_un = (
                            (float(receita_flux_ciclos_por_farm.get(f, 0) or 0.0) / total_rec_flux * unassigned_total)
                            if total_rec_flux > 0 and unassigned_total > 0
                            else 0.0
                        )
                        banc_flux_por_farm[f] = v_ass + v_un
        except Exception as e:
            logger.warning(f"[conciliacao] Falha ao calcular bancárias (Flux) no ciclo: {e}")
            banc_flux_por_farm = {}

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
        rate_total = float(pd.to_numeric(df_pay_all.get("valor_rateado"), errors="coerce").fillna(0).sum() if not df_pay_all.empty else 0.0)
        rate_valid_total = float(
            pd.to_numeric(df_pay_valid.get("valor_rateado"), errors="coerce").fillna(0).sum() if not df_pay_valid.empty else 0.0
        )
        rate_future_total = float(
            pd.to_numeric(df_pay_future.get("valor_rateado"), errors="coerce").fillna(0).sum() if not df_pay_future.empty else 0.0
        )
        diff = mov_total - rate_total

        meta.update(
            {
                "pay_mov_rows_total": int(len(df_mov)) if df_mov is not None else 0,
                "pay_mov_total": float(mov_total),
                "pay_rateio_total": float(rate_total),
                "pay_rateio_valid_total": float(rate_valid_total),
                "pay_rateio_future_total": float(rate_future_total),
            }
        )

        farms = set(rec_por_farm.keys()) | set(pay_por_farm.keys()) | set(pay_por_farm_future.keys()) | set(receita_ciclos_por_farm.keys())
        out_rows = []
        for f in sorted(farms):
            receita_caixa = float(rec_por_farm.get(f, 0) or 0)
            receita_ciclos = float(receita_ciclos_por_farm.get(f, 0) or 0)
            receita_flux_ciclos = float(receita_flux_ciclos_por_farm.get(f, 0) or 0)
            receita_total_ciclos = receita_ciclos + receita_flux_ciclos
            pct_flux_total = (receita_flux_ciclos / receita_total_ciclos * 100) if receita_total_ciclos else None
            pago = float(pay_por_farm.get(f, 0) or 0)
            diaria = float(diarias_por_farm.get(f, 0) or 0)
            pago_futuro = float(pay_por_farm_future.get(f, 0) or 0)
            diaria_futura = float(diarias_futuras_por_farm.get(f, 0) or 0)
            custo_bruto = pago + diaria
            iss = float(iss_por_farm.get(f, 0) or 0)
            # INSS: 4,5% apenas sobre o repasse do entregador (pagamento), não incide sobre diárias.
            inss = float(pago) * 0.045 if pago else 0.0
            banc_coop = float(banc_coop_por_farm.get(f, 0) or 0.0)
            banc_flux = float(banc_flux_por_farm.get(f, 0) or 0)
            simples_flux = float(receita_flux_ciclos * simples_rate_flux) if simples_rate_flux and receita_flux_ciclos else 0.0
            despesas_coop = iss + inss + banc_coop
            despesas_flux = simples_flux + banc_flux
            despesas_ciclo = despesas_coop + despesas_flux
            saldo_coop = receita_ciclos - (custo_bruto + despesas_coop)
            saldo_flux = receita_flux_ciclos - despesas_flux
            saldo_operacional = receita_total_ciclos - (custo_bruto + despesas_ciclo)
            saldo_caixa = receita_caixa - custo_bruto
            saldo_ciclos = receita_ciclos - custo_bruto
            pct_caixa = (custo_bruto / receita_caixa * 100) if receita_caixa else None
            pct_ciclos = (custo_bruto / receita_ciclos * 100) if receita_ciclos else None
            out_rows.append(
                {
                    "farmacia": f,
                    "receita_semana_caixa": receita_caixa,
                    "receita_ciclos_apurados": receita_ciclos,
                    "receita_flux_ciclos_apurados": receita_flux_ciclos,
                    "receita_total_ciclos_apurados": receita_total_ciclos,
                    "pct_flux_total_ciclos": pct_flux_total,
                    "pagamento_quinta": pago,
                    "diarias_ciclo": diaria,
                    "custo_bruto": custo_bruto,
                    "iss_ciclo": iss,
                    "inss_ciclo": inss,
                    "despesas_bancarias_ciclo": banc_coop,  # compat: mantém como (Coop)
                    "despesas_bancarias_flux_ciclo": banc_flux,
                    "simples_flux_estimado_ciclo": simples_flux,
                    "despesas_ciclo_total": despesas_ciclo,
                    "saldo_coop": saldo_coop,
                    "saldo_flux": saldo_flux,
                    "saldo_operacional": saldo_operacional,
                    "saldo": saldo_ciclos,
                    "saldo_caixa": saldo_caixa,
                    "pct_repasse": pct_ciclos,
                    "pct_repasse_caixa": pct_caixa,
                    "ciclos": ciclos_por_farm.get(f) or ciclos_receita_por_farm.get(f, []),
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
            "receita_flux_ciclos_apurados": float(sum(receita_flux_ciclos_por_farm.values()) if receita_flux_ciclos_por_farm else 0.0),
            "pagamento_quinta": float(sum(pay_por_farm.values()) if pay_por_farm else 0.0) + float(pd.to_numeric(df_pay_unclassified_valid.get("valor_rateado"), errors="coerce").fillna(0.0).sum() if not df_pay_unclassified_valid.empty else 0.0),
            "pagamento_quinta_sem_farmacia": float(pd.to_numeric(df_pay_unclassified_valid.get("valor_rateado"), errors="coerce").fillna(0.0).sum() if not df_pay_unclassified_valid.empty else 0.0),
            "diarias_ciclo": float(sum(diarias_por_farm.values()) if diarias_por_farm else 0.0) + float(sum((float(r.get("valor_rateado") or 0.0) for r in (unclassified_diarias_rows or []))) if unclassified_diarias_rows else 0.0),
            "diarias_ciclo_sem_farmacia": float(sum((float(r.get("valor_rateado") or 0.0) for r in (unclassified_diarias_rows or []))) if unclassified_diarias_rows else 0.0),
            "pagamento_futuro": float(sum(pay_por_farm_future.values()) if pay_por_farm_future else 0.0),
            "diarias_futuro": float(sum(diarias_futuras_por_farm.values()) if diarias_futuras_por_farm else 0.0),
            "desconto_adiantamento_rodada": float(desconto_adiantamento_rodada or 0.0),
            "adiantado_ciclo": float(adiantado_ciclo or 0.0),
            "reembolsado_ciclo": float(reembolsado_ciclo or 0.0),
        }
        totals["receita_total_ciclos_apurados"] = totals["receita_ciclos_apurados"] + totals["receita_flux_ciclos_apurados"]
        totals["iss_ciclo"] = float(sum(iss_por_farm.values()) if iss_por_farm else 0.0)
        # INSS incide sobre o valor pago aos entregadores (não incide sobre diárias)
        totals["inss_ciclo"] = float((totals["pagamento_quinta"]) * 0.045)
        totals["despesas_bancarias_ciclo"] = float(sum(banc_coop_por_farm.values()) if banc_coop_por_farm else 0.0)
        totals["despesas_bancarias_flux_ciclo"] = float(sum(banc_flux_por_farm.values()) if banc_flux_por_farm else 0.0)
        totals["simples_flux_estimado_ciclo"] = float(
            sum((float(receita_flux_ciclos_por_farm.get(f, 0) or 0) * simples_rate_flux) for f in receita_flux_ciclos_por_farm.keys())
        ) if simples_rate_flux and receita_flux_ciclos_por_farm else 0.0
        totals["custo_bruto"] = totals["pagamento_quinta"] + totals["diarias_ciclo"]
        totals["custo_bruto_futuro"] = totals["pagamento_futuro"] + totals["diarias_futuro"]
        totals["custo_bruto_total_com_futuro"] = totals["custo_bruto"] + totals["custo_bruto_futuro"]
        totals["pagamento_liquido"] = totals["custo_bruto"] - totals["desconto_adiantamento_rodada"]
        totals["saldo"] = totals["receita_ciclos_apurados"] - totals["custo_bruto"]
        totals["saldo_caixa"] = totals["receita_semana_caixa"] - totals["custo_bruto"]
        totals["despesas_ciclo_total"] = (
            totals["iss_ciclo"]
            + totals["inss_ciclo"]
            + totals["despesas_bancarias_ciclo"]
            + totals["despesas_bancarias_flux_ciclo"]
            + totals["simples_flux_estimado_ciclo"]
        )
        totals["saldo_operacional"] = totals["receita_total_ciclos_apurados"] - (totals["custo_bruto"] + totals["despesas_ciclo_total"])
        totals["saldo_coop"] = totals["receita_ciclos_apurados"] - (
            totals["custo_bruto"] + totals["iss_ciclo"] + totals["inss_ciclo"] + totals["despesas_bancarias_ciclo"]
        )
        totals["saldo_flux"] = totals["receita_flux_ciclos_apurados"] - (
            totals["simples_flux_estimado_ciclo"] + totals["despesas_bancarias_flux_ciclo"]
        )
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

        # Linhas sem farmácia (auditoria): pagamentos e diárias que não conseguiram ser associados a uma unidade.
        unclassified_payments_rows = []
        if df_pay_unclassified_valid is not None and not df_pay_unclassified_valid.empty:
            unclassified_payments_rows = (
                df_pay_unclassified_valid[[
                    "valor_rateado",
                    "periodo_inicio",
                    "periodo_fim",
                    "centro_custo_raw",
                    "parceiro",
                    "referente",
                    "numero_docto",
                    "registro_movimento",
                    "registro_compromisso",
                    "data_mov",
                ]]
                .fillna("")
                .to_dict(orient="records")
            )

        return {
            "cash_week_start": ws,
            "cash_week_end": we,
            "pay_day": pd_day,
            "mode": "caixa" if mode_norm == "CAIXA" else "ciclo",
            "meta": meta,
            "totals": totals,
            "unclassified": {
                "pagamentos_sem_farmacia_total": float(totals.get("pagamento_quinta_sem_farmacia") or 0.0),
                "diarias_sem_farmacia_total": float(totals.get("diarias_ciclo_sem_farmacia") or 0.0),
                "pagamentos_rows": unclassified_payments_rows,
                "diarias_rows": unclassified_diarias_rows,
            },
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


@app.get("/api/coop/conciliacao_entregas/farm_cycle_history")
def coop_conciliacao_farm_cycle_history(pay_day: str, farmacia: str, db: Client = Depends(get_db)):
    """
    HistÃ³rico de comparaÃ§Ã£o: Ãºltimos 5 ciclos (atual + 4 anteriores) para a farmÃ¡cia,
    usando (periodo_inicio, periodo_fim) como chave do ciclo.

    ObservaÃ§Ã£o: Receita Flux segue a mesma regra do report (DATA_MOV no dre_financeiro dentro do intervalo apurado).
    """
    try:
        pd_day_iso = _iso_date(pay_day)
        farmacia = (farmacia or "").strip()
        if not farmacia:
            raise HTTPException(status_code=400, detail="ParÃ¢metro 'farmacia' Ã© obrigatÃ³rio.")

        # Ciclos presentes na rodada (pagamento) para determinar o "ciclo atual"
        base_rows = _fetch_all_filtered(
            db,
            "coop_conciliacao_entregas",
            "periodo_inicio,periodo_fim",
            lambda q: q.eq("centro_custo_final", farmacia)
            .eq("plano_contas", "PAGAMENTO DE ENTREGADORES")
            .eq("linha_tipo", "RATEIO")
            .eq("data_mov", pd_day_iso)
            .not_.is_("periodo_inicio", "null")
            .not_.is_("periodo_fim", "null"),
            chunk=1000,
            max_rows=50000,
        )
        df_base = pd.DataFrame(base_rows)
        if df_base.empty:
            raise HTTPException(status_code=404, detail="Nenhum ciclo encontrado para esta farmÃ¡cia na rodada selecionada.")

        df_base["pi"] = pd.to_datetime(df_base["periodo_inicio"].astype(str).str.slice(0, 10), errors="coerce").dt.date
        df_base["pf"] = pd.to_datetime(df_base["periodo_fim"].astype(str).str.slice(0, 10), errors="coerce").dt.date
        df_base = df_base.dropna(subset=["pi", "pf"])
        if df_base.empty:
            raise HTTPException(status_code=404, detail="Ciclos invÃ¡lidos (sem datas) para esta farmÃ¡cia na rodada.")

        current_pf = max(df_base["pf"].tolist())
        pf_start = current_pf - timedelta(days=180)

        # Busca linhas RATEIO relevantes na janela (evita round-trips por ciclo)
        rows = _fetch_all_filtered(
            db,
            "coop_conciliacao_entregas",
            "plano_contas,entrada_saida,valor_rateado,periodo_inicio,periodo_fim,data_mov",
            lambda q: q.eq("centro_custo_final", farmacia)
            .eq("linha_tipo", "RATEIO")
            .not_.is_("periodo_inicio", "null")
            .not_.is_("periodo_fim", "null")
            .gte("periodo_fim", str(pf_start))
            .lte("periodo_fim", str(current_pf))
            .in_("plano_contas", ["RECEITAS COM ENTREGAS", "PAGAMENTO DE ENTREGADORES", "DIARIA DE MOTOBOY"]),
            chunk=1000,
            max_rows=200000,
        )
        df = pd.DataFrame(rows)
        if df.empty:
            raise HTTPException(status_code=404, detail="Sem dados para montar histÃ³rico de ciclos.")

        df["pi"] = pd.to_datetime(df["periodo_inicio"].astype(str).str.slice(0, 10), errors="coerce").dt.date
        df["pf"] = pd.to_datetime(df["periodo_fim"].astype(str).str.slice(0, 10), errors="coerce").dt.date
        df["dm"] = pd.to_datetime(df["data_mov"].astype(str).str.slice(0, 10), errors="coerce").dt.date
        df["valor_rateado"] = pd.to_numeric(df.get("valor_rateado"), errors="coerce").fillna(0.0)
        df["plano_norm"] = df.get("plano_contas").apply(norm)
        df["entrada_norm"] = df.get("entrada_saida").apply(norm)
        df = df.dropna(subset=["pi", "pf"])

        pay_cycles = sorted(
            {(r.pi, r.pf) for r in df[df["plano_norm"] == "PAGAMENTO DE ENTREGADORES"].itertuples(index=False)},
            key=lambda x: x[1],
        )
        pay_cycles = [c for c in pay_cycles if c[1] <= current_pf]
        if not pay_cycles:
            raise HTTPException(status_code=404, detail="Sem ciclos de pagamento suficientes para histÃ³rico.")
        selected_cycles = pay_cycles[-5:]
        selected_set = set(selected_cycles)

        df["cycle_key"] = list(zip(df["pi"].tolist(), df["pf"].tolist()))
        df_sel = df[df["cycle_key"].isin(selected_set)].copy()

        receita_flux_por_cycle: dict[tuple[date, date], float] = {k: 0.0 for k in selected_cycles}
        try:
            min_pi = min(c[0] for c in selected_cycles)
            max_pf = max(c[1] for c in selected_cycles)
            flux_rows = _fetch_all_filtered(
                db,
                "dre_financeiro",
                "data_mov,centro_custo,valor",
                lambda q: q.eq("entrada_saida", "ENTRADA")
                .eq("plano_contas", "RECEITAS COM ENTREGAS")
                .eq("centro_custo", farmacia)
                .not_.is_("data_mov", "null")
                .gte("data_mov", str(min_pi))
                .lte("data_mov", str(max_pf)),
                chunk=1000,
                max_rows=200000,
            )
            df_flux = pd.DataFrame(flux_rows)
            if not df_flux.empty:
                df_flux["dm"] = pd.to_datetime(df_flux["data_mov"].astype(str).str.slice(0, 10), errors="coerce").dt.date
                df_flux["valor"] = pd.to_numeric(df_flux.get("valor"), errors="coerce").fillna(0.0)
                df_flux = df_flux.dropna(subset=["dm"])
                for dm, val in zip(df_flux["dm"].tolist(), df_flux["valor"].tolist()):
                    for pi, pf in selected_cycles:
                        if pi <= dm <= pf:
                            receita_flux_por_cycle[(pi, pf)] += float(val or 0.0)
                            break
        except Exception as e:
            logger.warning(f"[conciliacao] Falha ao calcular receita Flux no histÃ³rico: {e}")

        out = []
        for pi, pf in selected_cycles:
            sub = df_sel[df_sel["cycle_key"] == (pi, pf)]
            rec = float(
                sub[(sub["plano_norm"] == "RECEITAS COM ENTREGAS") & (sub["entrada_norm"] == "ENTRADA")]["valor_rateado"].sum()
            )
            pag = float(sub[sub["plano_norm"] == "PAGAMENTO DE ENTREGADORES"]["valor_rateado"].sum())
            dia = float(sub[sub["plano_norm"] == "DIARIA DE MOTOBOY"]["valor_rateado"].sum())
            custo_bruto = pag + dia
            iss = rec * 0.03 if rec else 0.0
            inss = pag * 0.045 if pag else 0.0  # INSS nÃ£o incide sobre diÃ¡rias
            saldo_coop = rec - (custo_bruto + iss + inss)
            pct_repasse = (custo_bruto / rec * 100) if rec else None

            out.append(
                {
                    "periodo_inicio": pi.isoformat(),
                    "periodo_fim": pf.isoformat(),
                    "receita_coop": rec,
                    "receita_flux": float(receita_flux_por_cycle.get((pi, pf), 0.0) or 0.0),
                    "pagamento": pag,
                    "diarias": dia,
                    "custo_bruto": custo_bruto,
                    "iss": iss,
                    "inss": inss,
                    "saldo_coop": saldo_coop,
                    "pct_repasse": pct_repasse,
                    "is_current": bool(pf == current_pf),
                }
            )

        return {"farmacia": farmacia, "pay_day": pd_day_iso, "cycles": out}
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

        # Pagamento (rodada) - base para ciclos incluídos.
        # Importante: os ciclos (periodo_inicio/fim) devem ser extraídos do conjunto completo da rodada,
        # mesmo quando o export é filtrado por uma farmácia específica (senão algumas farmácias com receita
        # no ciclo, mas sem pagamento na rodada, ficariam sem "wanted cycles").
        pay_rate_rows_all = _fetch_all_filtered(
            db,
            "coop_conciliacao_entregas",
            COOP_CONCILIACAO_AUDIT_SELECT,
            lambda q: q.eq("plano_contas", "PAGAMENTO DE ENTREGADORES")
            .eq("linha_tipo", "RATEIO")
            .eq("data_mov", pd_day),
            chunk=1000,
            max_rows=200000,
        )
        df_pay_all = pd.DataFrame(pay_rate_rows_all)
        if not df_pay_all.empty:
            df_pay_all["centro"] = df_pay_all["centro_custo_final"].fillna("Não Especificado")
            df_pay_all = df_pay_all[df_pay_all["centro"].apply(norm) != "NAO ESPECIFICADO"]
            df_pay_all["valor_rateado"] = pd.to_numeric(df_pay_all["valor_rateado"], errors="coerce").fillna(0.0)
            df_pay_all["pi"] = pd.to_datetime(df_pay_all["periodo_inicio"].astype(str).str.slice(0, 10), errors="coerce").dt.date
            df_pay_all["pf"] = pd.to_datetime(df_pay_all["periodo_fim"].astype(str).str.slice(0, 10), errors="coerce").dt.date

        # Separar ciclos futuros (auditoria) para manter consistência com o relatório principal.
        df_pay_all_valid = df_pay_all
        df_pay_all_future = pd.DataFrame()
        if not df_pay_all.empty and "pi" in df_pay_all.columns and "pf" in df_pay_all.columns:
            pd_day_d = pd.to_datetime(str(pd_day)[:10], errors="coerce").date()
            mask_future = (df_pay_all["pi"] > pd_day_d) | (df_pay_all["pf"] > pd_day_d)
            df_pay_all_future = df_pay_all[mask_future].copy()
            df_pay_all_valid = df_pay_all[~mask_future].copy()

        # Aplicar filtro de farmácia somente na exibição/export (não na seleção de ciclos).
        df_pay_valid = df_pay_all_valid
        df_pay_future = df_pay_all_future
        if farm_list and not df_pay_valid.empty:
            farm_norms = {norm(f) for f in farm_list}
            df_pay_valid = df_pay_valid[df_pay_valid["centro"].apply(norm).isin(farm_norms)].copy()
        if farm_list and not df_pay_future.empty:
            farm_norms = {norm(f) for f in farm_list}
            df_pay_future = df_pay_future[df_pay_future["centro"].apply(norm).isin(farm_norms)].copy()

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
            df_pay_all_valid[["periodo_inicio", "periodo_fim"]].dropna().drop_duplicates().to_dict(orient="records")
            if not df_pay_all_valid.empty
            else []
        )
        wanted = set()
        ciclos_futuros = (
            df_pay_all_future[["periodo_inicio", "periodo_fim"]].dropna().drop_duplicates().to_dict(orient="records")
            if not df_pay_all_future.empty
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
                    "receita_flux_ciclos_apurados": "Faturado Flux (ciclos)",
                    "receita_total_ciclos_apurados": "Receita Total (Flux+Coop, ciclos)",
                    "pct_flux_total_ciclos": "% Flux/Total (ciclos)",
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
                    "iss_ciclo": "ISS (3%)",
                    "inss_ciclo": "INSS (ciclo)",
                    "despesas_bancarias_ciclo": "Bancárias (Coop, ciclo)",
                    "despesas_bancarias_flux_ciclo": "Bancárias (Flux, ciclo)",
                    "simples_flux_estimado_ciclo": "Simples (Flux, estimado)",
                    "despesas_ciclo_total": "Despesas do ciclo (ISS+INSS+BancCoop+BancFlux+Simples)",
                    "saldo_coop": "Saldo Cooperativa (c/ despesas)",
                    "saldo_flux": "Saldo Flux (c/ despesas)",
                    "saldo_operacional": "Saldo operacional (c/ despesas)",
                }
            )

        totals = report.get("totals") or {}
        controle = report.get("controle") or {}
        label_map = {
            "receita_semana_caixa": "Receita Entregas (caixa semana)",
            "receita_ciclos_apurados": "Receita Entregas (ciclos pagos)",
            "receita_flux_ciclos_apurados": "Faturado Flux (ciclos)",
            "receita_total_ciclos_apurados": "Receita Total (Flux+Coop, ciclos)",
            "iss_ciclo": "ISS (3%)",
            "inss_ciclo": "INSS (ciclo)",
            "despesas_bancarias_ciclo": "Bancárias (Coop, ciclo)",
            "despesas_bancarias_flux_ciclo": "Bancárias (Flux, ciclo)",
            "simples_flux_estimado_ciclo": "Simples (Flux, estimado)",
            "despesas_ciclo_total": "Despesas do ciclo (ISS+INSS+BancCoop+BancFlux+Simples)",
            "saldo_coop": "Saldo Cooperativa (c/ despesas)",
            "saldo_flux": "Saldo Flux (c/ despesas)",
            "saldo_operacional": "Saldo operacional (c/ despesas)",
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
elif has_assets(frontend_dist):
    static_dir = frontend_dist
elif has_assets(local_dist):
    static_dir = local_dist
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
    
    if static_dir and os.path.exists(static_dir):
        file_path = os.path.join(static_dir, full_path)
        if os.path.exists(file_path) and os.path.isfile(file_path):
            # Evita cache agressivo do HTML (para não "grudar" em bundles antigos no refresh).
            if file_path.lower().endswith(".html"):
                return FileResponse(file_path, headers={"Cache-Control": "no-store"})
            return FileResponse(file_path)
        return FileResponse(os.path.join(static_dir, "index.html"), headers={"Cache-Control": "no-store"})
    return {"message": "Frontend not built or dist directory missing"}

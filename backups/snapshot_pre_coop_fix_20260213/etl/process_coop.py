"""
ETL para DRE da Cooperativa, seguindo a estrutura do Flux.
- Lê Relatorio_Financeiro_Cooperativa.xlsx (Planilha1)
- Consolida pares de movimento/rateio, priorizando valor rateado quando existir
- Exclui transferências e operações não operacionais
- Adiantamentos geram um saldo separado para auditoria
- Usa whitelist de centros de custo do Flux (receitas com entregas) para manter apenas farmácias
- Envia dados para tabelas dedicadas no Supabase:
  dre_financeiro_coop, dre_gerencial_coop, predictive_data_coop, coop_adiantamentos_saldo
"""

from __future__ import annotations

import os
import unicodedata
import pandas as pd
from supabase import Client

import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.database import supabase  # noqa: E402

COL_DATA = "DATA MOVIMENTO"
COL_ENTRADA = "ENTRADA/SAÍDA"
COL_PLANO = "PLANO DE CONTAS"
COL_CENTRO = "CENTRO DE CUSTO (FARMÁCIA PRESTADORA)"
COL_PARCEIRO = "PARCEIROV (FARMÁCIA PAGADOR OU ENTREGADOR)"
COL_VALOR_MOV = "VALOR MOV.CONTA"
COL_VALOR_RATEADO = "VALOR RATEADO PARA O PRESTADOR"
COL_VALOR_ORIG = "VALOR ORIGINAL"
COL_NUM_DOC = "NÚMERO DOCTO"
COL_PARC = "PARC"

MESES_PT = {
    "01": "Janeiro",
    "02": "Fevereiro",
    "03": "Marco",
    "04": "Abril",
    "05": "Maio",
    "06": "Junho",
    "07": "Julho",
    "08": "Agosto",
    "09": "Setembro",
    "10": "Outubro",
    "11": "Novembro",
    "12": "Dezembro",
}


def normalize_text(text: str) -> str:
    text = "" if text is None else str(text)
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).upper().strip()

def extract_store_code(text: str) -> str | None:
    """
    Extrai o código numérico da loja do formato comum: '... (10006)'.
    Retorna None se não encontrar.
    """
    if text is None:
        return None
    s = str(text)
    import re

    m = re.search(r"\((\d{4,8})\)\s*$", s)
    if m:
        return m.group(1)
    m = re.search(r"\((\d{4,8})\)", s)
    if m:
        return m.group(1)
    return None


def load_center_whitelist(db: Client) -> tuple[set[str], dict[str, str]]:
    """Centros que geram receita no Flux (receitas com entregas)."""
    try:
        rows = (
            db.table("dre_financeiro")
            .select("centro_custo, plano_contas, entrada_saida")
            .eq("entrada_saida", "ENTRADA")
            .ilike("plano_contas", "%RECEITAS COM ENTREGAS%")
            .execute()
            .data
        )
        name_set = {normalize_text(r.get("centro_custo")) for r in rows if r.get("centro_custo")}
        code_map: dict[str, str] = {}
        for r in rows:
            c = r.get("centro_custo")
            if not c:
                continue
            code = extract_store_code(c)
            if code and code not in code_map:
                code_map[code] = c
        return name_set, code_map
    except Exception:
        return set(), {}


def classify_plano(plano_norm: str) -> tuple[str, str]:
    """Retorna tipo_custo, categoria."""
    transfer_keywords = [
        "BANCO CORA",
        "BANCO C6",
        "CAIXA - FLUX",
        "APLICACAO BANCO A",
        "BANCO SICREDI",
        "CARTAO CREDITO COOPMOB",
    ]
    if any(k in plano_norm for k in transfer_keywords):
        return "TRANSFERENCIA", "Transferencia"
    if "ADIANTAMENTO" in plano_norm:
        return "ADIANTAMENTO", "Adiantamento"
    if "DEVOLUCAO COTA" in plano_norm or "COTA COOPERATIVA" in plano_norm:
        return "NAO OPERACIONAL", "Cota Cooperativa"
    if "IOF/JUROS/DESP.FINANCEIRA" in plano_norm:
        return "VARIAVEL", "Despesas Bancarias"
    if "RECEITAS COM ENTREGAS" in plano_norm:
        return "VARIAVEL", "Receita Entregas"
    if "RECEITAS COM REEMBOLSOS" in plano_norm or "ENTRADA DE REEMBOLSO" in plano_norm:
        return "VARIAVEL", "Receita Reembolsos"
    if "ENTRADA DE CAPITAL" in plano_norm:
        return "NAO OPERACIONAL", "Entrada Capital"
    fix_keywords = ["PAGAMENTO DE FUNCION", "SOFTWARE", "PLANOS E SEGUROS", "ALUGUEL", "HONORARIOS"]
    if any(k in plano_norm for k in fix_keywords):
        return "FIXO", "Despesa Fixa"
    var_keywords = [
        "PAGAMENTO DE ENTREGADORES",
        "DIARIA DE MOTOBOY",
        "PAGAMENTO COMISSAO",
        "COMISSAO DE REPRESENTANTES",
        "DESPESAS COM REEMBOLSOS",
        "DESPESAS COM VIAGENS",
        "PUBLICIDADE",
    ]
    if any(k in plano_norm for k in var_keywords):
        return "VARIAVEL", "Despesa Variavel"
    if "ISS" in plano_norm or plano_norm == "INSS":
        return "VARIAVEL", "Impostos"
    return "VARIAVEL", "Outros"


def consolidate_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Consolida pares de rateio/movimento."""
    df[COL_DATA] = pd.to_datetime(df[COL_DATA], dayfirst=True, errors="coerce")
    df["valor_mov"] = pd.to_numeric(df[COL_VALOR_MOV], errors="coerce")
    df["valor_rateado"] = pd.to_numeric(df[COL_VALOR_RATEADO], errors="coerce")
    df["valor_original"] = pd.to_numeric(df[COL_VALOR_ORIG], errors="coerce")

    def pick_valor(row) -> float:
        v = row.get("valor_rateado")
        if pd.notna(v) and v != 0:
            return float(v)
        return 0.0

    def key_fn(row):
        return (
            row.get(COL_DATA),
            row.get(COL_NUM_DOC),
            row.get(COL_PARC),
            row.get(COL_PLANO),
            row.get(COL_PARCEIRO),
            row.get(COL_ENTRADA),
        )

    records: dict[tuple, dict] = {}
    for _, row in df.iterrows():
        k = key_fn(row)
        rec = records.get(k)
        valor = pick_valor(row)
        if rec:
            if abs(valor) > abs(rec.get("valor") or 0):
                rec["valor"] = valor if pd.notna(valor) else 0.0
            # preserva centro se algum dos pares tiver centro preenchido
            if (pd.isna(rec.get(COL_CENTRO)) or rec.get(COL_CENTRO) in ("", None)) and row.get(COL_CENTRO):
                rec[COL_CENTRO] = row.get(COL_CENTRO)
        else:
            new_rec = row.to_dict()
            new_rec["valor"] = valor if pd.notna(valor) else 0.0
            records[k] = new_rec

    out = pd.DataFrame(records.values())
    out = out[(out["valor"].notna()) & (out["valor"] != 0)]
    return out


def process_coop(file_path: str):
    print("Lendo arquivo coop:", file_path)
    df = pd.read_excel(file_path, sheet_name="Planilha1", engine="openpyxl")
    df[COL_DATA] = pd.to_datetime(df[COL_DATA], dayfirst=True, errors="coerce")
    df["valor_rateado"] = pd.to_numeric(df[COL_VALOR_RATEADO], errors="coerce")
    df["valor"] = df["valor_rateado"]
    df = df[(df["valor"].notna()) & (df["valor"] != 0)]

    df["MES_ANO"] = df[COL_DATA].dt.strftime("%Y-%m")
    df["MES_NOME"] = df["MES_ANO"].str[5:7].map(MESES_PT)
    df["entrada_saida_norm"] = df[COL_ENTRADA].apply(normalize_text)
    df["plano_norm"] = df[COL_PLANO].apply(normalize_text)
    df["centro_norm"] = df[COL_CENTRO].apply(normalize_text)

    tipo_custo = []
    categoria = []
    for plano in df["plano_norm"]:
        tc, cat = classify_plano(plano)
        tipo_custo.append(tc)
        categoria.append(cat)
    df["tipo_custo"] = tipo_custo
    df["categoria"] = categoria

    whitelist, whitelist_codes = load_center_whitelist(supabase)

    def centro_valid(row):
        raw = row.get(COL_CENTRO)
        c_norm = row.get("centro_norm")
        if raw:
            code = extract_store_code(raw)
            if code and code in whitelist_codes:
                return whitelist_codes[code]
        if c_norm and c_norm in whitelist:
            return raw
        # Para a Cooperativa, centro de custo deve ser estritamente uma farmácia.
        # Entregadores/prestadores/bancos não devem virar centro de custo.
        return "Não Especificado"

    df["centro_custo_final"] = df.apply(centro_valid, axis=1)

    is_transfer = df["tipo_custo"] == "TRANSFERENCIA"
    # Mantemos NAO OPERACIONAL (cotas) para indicadores; só removemos transferências e adiantamentos
    df_dre = df[~is_transfer & (df["tipo_custo"] != "ADIANTAMENTO")].copy()
    df_dre["MES_NOME"] = df_dre["MES_NOME"].fillna("Indefinido")
    df_dre["MES_ANO"] = df_dre["MES_ANO"].fillna("0000-00")
    df_dre[COL_ENTRADA] = df_dre[COL_ENTRADA].fillna("DESCONHECIDO")
    df_dre[COL_PLANO] = df_dre[COL_PLANO].fillna("DESCONHECIDO")
    df_dre["centro_custo_final"] = df_dre["centro_custo_final"].fillna("NÃO ESPECIFICADO")

    def safe_num(v):
        if pd.isna(v):
            return 0.0
        try:
            f = float(v)
            if f != f:  # NaN
                return 0.0
            return f
        except Exception:
            return 0.0

    fin_records = []
    for _, r in df_dre.iterrows():
        val = safe_num(r.get("valor"))
        # saídas negativas para evitar dobrar receita/despesa
        if r.get(COL_ENTRADA) == "SAÍDA":
            val = -abs(val)
        parceiro_raw = r.get(COL_PARCEIRO)
        parceiro = None if pd.isna(parceiro_raw) else parceiro_raw
        data_mov = r[COL_DATA]
        fin_records.append(
            {
                "mes_ano": r["MES_ANO"],
                "mes_nome": r["MES_NOME"],
                "data_mov": data_mov.date().isoformat() if pd.notna(data_mov) else None,
                "centro_custo": r["centro_custo_final"],
                "parceiro": parceiro,
                "plano_contas": r[COL_PLANO],
                "entrada_saida": r[COL_ENTRADA],
                "valor": val,
                "tipo_custo": r["tipo_custo"],
            }
        )

    print("Limpando tabelas coop no Supabase...")
    supabase.table("dre_financeiro_coop").delete().neq("id", 0).execute()
    supabase.table("dre_gerencial_coop").delete().neq("id", 0).execute()
    supabase.table("predictive_data_coop").delete().neq("id", 0).execute()
    try:
        supabase.table("coop_adiantamentos_saldo").delete().neq("id", 0).execute()
    except Exception:
        pass

    chunk = 1000
    for i in range(0, len(fin_records), chunk):
        supabase.table("dre_financeiro_coop").insert(fin_records[i : i + chunk]).execute()
    print(f"Inseridos {len(fin_records)} registros em dre_financeiro_coop")

    df_fin = pd.DataFrame(fin_records).fillna(
        {
            "mes_nome": "Indefinido",
            "mes_ano": "0000-00",
            "centro_custo": "NÃO ESPECIFICADO",
            "entrada_saida": "DESCONHECIDO",
            "plano_contas": "DESCONHECIDO",
            "tipo_custo": "DESCONHECIDO",
        }
    )
    grouped = df_fin.groupby(["mes_ano", "mes_nome", "centro_custo", "entrada_saida"])["valor"].sum().reset_index()
    pivot = (
        grouped.pivot_table(
            index=["mes_ano", "mes_nome", "centro_custo"],
            columns="entrada_saida",
            values="valor",
            fill_value=0,
        )
        .reset_index()
        .rename_axis(None, axis=1)
    )
    pivot["receita_total"] = pivot.get("ENTRADA", 0).fillna(0)
    pivot["despesa_total"] = pivot.get("SAÍDA", 0).fillna(0).abs() if isinstance(pivot.get("SAÍDA"), pd.Series) else 0
    pivot["lucro"] = pivot["receita_total"] - pivot["despesa_total"]
    pivot["margem_liquida"] = (pivot["lucro"] / pivot["receita_total"] * 100).fillna(0)
    pivot = pivot.replace([float("inf"), float("-inf")], 0).fillna(0)

    ger_regs = []
    for _, r in pivot.iterrows():
        ger_regs.append(
            {
                "mes_ano": r["mes_ano"],
                "mes_nome": r["mes_nome"],
                "centro_custo": r["centro_custo"],
                "receita_total": safe_num(r.get("receita_total")),
                "despesa_total": safe_num(r.get("despesa_total")),
                "lucro": safe_num(r.get("lucro")),
                "margem_liquida": safe_num(r.get("margem_liquida")),
            }
        )
    for i in range(0, len(ger_regs), chunk):
        supabase.table("dre_gerencial_coop").insert(ger_regs[i : i + chunk]).execute()
    print(f"Inseridos {len(ger_regs)} registros em dre_gerencial_coop")

    df_ad = df[df["tipo_custo"] == "ADIANTAMENTO"].copy()
    if not df_ad.empty:
        df_ad["valor_num"] = pd.to_numeric(df_ad["valor"], errors="coerce").fillna(0)
        # saída = adiantado (negativo para facilitar saldo), entrada = reembolso
        df_ad.loc[df_ad[COL_ENTRADA].str.upper() == "SAÍDA", "valor_num"] *= -1
        df_ad["mes_ano"] = df_ad[COL_DATA].dt.strftime("%Y-%m")
        df_ad["mes_nome"] = df_ad["mes_ano"].str[5:7].map(MESES_PT)
        df_ad["centro_final"] = df_ad["centro_custo_final"]
        df_ad["parceiro_norm"] = df_ad[COL_PARCEIRO]
        ag = (
            df_ad.groupby(["mes_ano", "mes_nome", "centro_final", "parceiro_norm"])
            .agg(
                adiantado=("valor_num", lambda x: x[x < 0].sum() * -1),
                reembolsado=("valor_num", lambda x: x[x > 0].sum()),
            )
            .reset_index()
        )
        ag["saldo"] = ag["reembolsado"] - ag["adiantado"]
        adi_regs = []
        for _, r in ag.iterrows():
            adi_regs.append(
                {
                    "mes_ano": r["mes_ano"],
                    "mes_nome": r["mes_nome"],
                    "centro_custo": r["centro_final"],
                    "parceiro": r["parceiro_norm"],
                    "adiantado": safe_num(r.get("adiantado")),
                    "reembolsado": safe_num(r.get("reembolsado")),
                    "saldo": safe_num(r.get("saldo")),
                }
            )
        for i in range(0, len(adi_regs), chunk):
            supabase.table("coop_adiantamentos_saldo").insert(adi_regs[i : i + chunk]).execute()
        print(f"Inseridos {len(adi_regs)} registros em coop_adiantamentos_saldo")

    supabase.table("predictive_data_coop").insert({"data_type": "main", "data_json": {}}).execute()
    print("ETL cooperativa concluído.")


if __name__ == "__main__":
    base = os.path.dirname(os.path.abspath(__file__))
    if len(os.sys.argv) > 1:
        file_path = os.sys.argv[1]
    else:
        # Padrões do projeto (prioriza a pasta data)
        data_path = os.path.join(base, "..", "data", "Relatório de Movimentos do Financeiro CoopMob 2025.xlsx")
        legacy_path = os.path.join(base, "Relatorio_Financeiro_Cooperativa.xlsx")
        root_legacy = os.path.join(base, "..", "Relatorio_Financeiro_Cooperativa.xlsx")
        if os.path.exists(data_path):
            file_path = data_path
        elif os.path.exists(legacy_path):
            file_path = legacy_path
        else:
            file_path = root_legacy

    process_coop(file_path)

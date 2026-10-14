"""
ETL para carregar dados no Supabase (HTTP API).
- Lê o Excel de entrada
- Normaliza/limpa dados
- Classifica custos (fixo/variavel/nao operacional)
- Gera agregados gerenciais
- Salva dados brutos, agregados e preditivos no Supabase
"""

import os
import sys
import sys
import unicodedata
import pandas as pd
from supabase import Client

# Permite imports relativos ao backend
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.database import supabase  # noqa: E402
from etl.predictive import process_predictive_data  # noqa: E402


# Mapeamento de classificacao de custos (case/acento-insensivel)
CUSTO_FIXO_VARIAVEL = {
    "COMISSOES DE REPRESENTANTES": "Variavel",
    "SIMPLES NACIONAL (DAS/DASN)": "Variavel",
    "SIMPLES NACIONAL": "Variavel",
    "PUBLICIDADE E PROPAGANDAS": "Variavel",
    "IOF/JUROS/DESP.FINANCEIRA": "Variavel",
    "RECEITAS COM ENTREGAS": "Variavel",
    "RECEITAS COM REEMBOLSOS": "Variavel",
    "RECEITAS COM SETUP": "Variavel",
    "OUTRAS RECEITAS": "Variavel",
    "PRO LABORE - LILIANE": "Fixo",
    "PRO LABORE - MARCELO": "Fixo",
    "PAGAMENTO DE FUNCIONARIOS": "Fixo",
    "SOFTWARE / LICENCAS (SAAS)": "Fixo",
    "SOFTWARE / LICENCAS": "Fixo",
    "EMPRESTIMOS": "Fixo",
    "DISTRIBUICAO DE LUCRO": "Nao Operacional",
}

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
    return "".join(c for c in nfkd if not unicodedata.combining(c)).upper()


def classificar_custo(plano_contas):
    if pd.isna(plano_contas):
        return "Variavel"
    plano_norm = normalize_text(plano_contas)
    if "DISTRIBUICAO DE LUCRO" in plano_norm:
        return "DISTRIBUICAO"
    if "TRANSFERENCIA" in plano_norm:
        return "TRANSFERENCIA"
    if "APLICACAO" in plano_norm:
        return "TRANSFERENCIA"
    for key, value in CUSTO_FIXO_VARIAVEL.items():
        if key in plano_norm:
            return value
    return "Variavel"


def process_excel_data(file_path):
    """Le o Excel e devolve dataframe limpo."""
    print(f"Lendo arquivo: {file_path}")
    # Alguns relatórios vêm com nomes de aba diferentes ("Planilha1", "Sheet1", etc.).
    # Tentamos as opções comuns e, por fim, caímos na primeira aba.
    def detect_header_row(preview: pd.DataFrame):
        if preview is None or preview.empty:
            return None

        def row_score(row_vals) -> int:
            cells = [normalize_text(c) for c in row_vals if pd.notna(c)]
            score = 0
            if any(("DATA" in c and "MOVIMENTO" in c) for c in cells):
                score += 1
            if any("ENTRADA/SAIDA" in c for c in cells):
                score += 1
            if any("PLANO DE CONTAS" in c for c in cells):
                score += 1
            if any(("VALOR" in c and "MOV" in c) for c in cells):
                score += 1
            return score

        best = None
        best_score = 0
        for i in range(min(len(preview), 60)):
            s = row_score(preview.iloc[i].tolist())
            if s > best_score:
                best_score = s
                best = i
        return best if best_score >= 3 else None

    def read_excel_smart(path: str) -> pd.DataFrame:
        last_err = None
        for sheet in ("Planilha1", "Sheet1", 0):
            try:
                preview = pd.read_excel(path, sheet_name=sheet, header=None, nrows=40)
                header_row = detect_header_row(preview)
                df0 = pd.read_excel(path, sheet_name=sheet, header=header_row if header_row is not None else 0)
                if df0 is not None and not df0.empty:
                    return df0
            except Exception as e:
                last_err = e
                continue
        if last_err:
            raise last_err
        raise RuntimeError("Falha ao ler o Excel (sem erro capturado).")

    df = read_excel_smart(file_path)
    print(f"Colunas encontradas: {df.columns.tolist()}")

    def find_col(partial):
        partial_norm = normalize_text(partial)
        for col in df.columns:
            if partial_norm in normalize_text(col):
                return col
        return partial

    valor_original_col = find_col("VALOR ORIGINAL")
    valor_mov_col = find_col("VALOR MOV.CONTA")
    valor_rateado_col = find_col("VALOR RATEADO")
    tipo_titulo_col = find_col("TIPO TÍTULO")
    banco_col = find_col("BANCO / CAIXA")
    registro_mov_col = find_col("REGISTRO MOVIMENTO")
    referente_col = find_col("REFERENTE")

    col_map = {
        find_col("DATA MOVIMENTO"): "DATA MOV.CONTA",
        valor_original_col: "VALOR_ORIGINAL_RAW",
        valor_mov_col: "VALOR_MOV_RAW",
        valor_rateado_col: "VALOR_RATEADO_RAW",
        find_col("CENTRO DE CUSTO"): "CENTRO DE CUSTO",
        find_col("PARCEIROV"): "PARCEIRO",
        find_col("PLANO DE CONTAS"): "PLANO DE CONTAS",
        find_col("ENTRADA/SAÍDA"): "ENTRADA_SAIDA",
        tipo_titulo_col: "TIPO_TITULO_RAW",
        banco_col: "BANCO / CAIXA",
        registro_mov_col: "REGISTRO_MOVIMENTO",
        referente_col: "REFERENTE",
    }
    print(f"Mapeamento de colunas: {col_map}")

    df = df.rename(columns=col_map)

    if len(df.columns) != len(set(df.columns)):
        print("Aviso: colunas duplicadas encontradas. Removendo duplicatas...")
        df = df.loc[:, ~df.columns.duplicated()]

    df["DATA MOV.CONTA"] = pd.to_datetime(df["DATA MOV.CONTA"], format="%d/%m/%Y", errors="coerce")
    df["VALOR_MOV_RAW"] = pd.to_numeric(df.get("VALOR_MOV_RAW"), errors="coerce")
    df["VALOR_RATEADO_RAW"] = pd.to_numeric(df.get("VALOR_RATEADO_RAW"), errors="coerce")
    df["VALOR_ORIGINAL_RAW"] = pd.to_numeric(df.get("VALOR_ORIGINAL_RAW"), errors="coerce")

    def compute_valor(row):
        tipo = normalize_text(row.get("ENTRADA_SAIDA", ""))
        mov = row.get("VALOR_MOV_RAW")
        rateado = row.get("VALOR_RATEADO_RAW")
        original = row.get("VALOR_ORIGINAL_RAW")
        plano_norm = normalize_text(row.get("PLANO DE CONTAS", ""))
        banco_norm = normalize_text(row.get("BANCO / CAIXA", ""))
        titulo_norm = normalize_text(row.get("TIPO_TITULO_RAW", ""))

        # Transferencias internas (apenas se titulo indicar transferencia)
        if "TRANSFERENCIA" in titulo_norm:
            return 0
        if "APLICACAO" in plano_norm:
            return 0
        if "DISTRIBUICAO DE LUCRO" in plano_norm or "DISTRIBUICAO DE LUCROS" in plano_norm:
            return mov if pd.notna(mov) and mov != 0 else 0

        if "ENTRADA" in tipo:
            if "ENTREGAS" in plano_norm:
                return rateado if pd.notna(rateado) and rateado != 0 else 0
            if "SETUP" in plano_norm:
                return mov if pd.notna(mov) and mov != 0 else 0
            # Reembolsos/demais receitas
            if pd.notna(mov) and mov != 0:
                return mov
            if pd.notna(rateado) and rateado != 0:
                return rateado
            if pd.notna(original) and original != 0:
                return original
            return 0

        # Despesas
        if pd.notna(mov) and mov != 0:
            return mov
        if pd.notna(rateado) and rateado != 0:
            return rateado
        if pd.notna(original) and original != 0:
            return original
        return 0

    df["VALOR MOV.CONTA"] = df.apply(compute_valor, axis=1)

    df["MES_ANO"] = df["DATA MOV.CONTA"].dt.strftime("%Y-%m")
    df["MES_NOME"] = df["MES_ANO"].str[5:7].map(MESES_PT)
    
    # Normalizar colunas de texto para evitar problemas de encoding
    text_cols = ["CENTRO DE CUSTO", "PARCEIRO", "PLANO DE CONTAS", "ENTRADA_SAIDA"]
    for col in text_cols:
        if col in df.columns:
            df[col] = df[col].apply(lambda x: normalize_text(x) if pd.notna(x) else x)
            
    df["CENTRO DE CUSTO"] = df["CENTRO DE CUSTO"].replace("", "NAO ESPECIFICADO")
    df["PARCEIRO"] = df["PARCEIRO"].replace("", "NAO ESPECIFICADO")

    def classify_row(row):
        plano = row.get("PLANO DE CONTAS")
        titulo = row.get("TIPO_TITULO_RAW")
        referente = row.get("REFERENTE")
        if pd.notna(titulo) and "TRANSFERENCIA" in normalize_text(str(titulo)):
            return "TRANSFERENCIA"
        if pd.notna(referente):
            referente_norm = normalize_text(str(referente))
            plano_norm = normalize_text(plano)
            if "DISTRIBUICAO DE LUCRO" in plano_norm or "DISTRIBUICAO DE LUCROS" in plano_norm:
                if "PARTICIPACAO NOS LUCROS FLUX FARMA" in referente_norm:
                    return "DISTRIBUICAO_PLR"
                if "DISTRIBUICAO DE LUCROS ANTECIPADA" in referente_norm:
                    return "DISTRIBUICAO_PLR"
        return classificar_custo(plano)

    df["TIPO_CUSTO"] = df.apply(classify_row, axis=1)

    df_filtered = df[df["TIPO_CUSTO"] != "Nao Operacional"].copy()
    df_filtered = df_filtered[df_filtered["VALOR MOV.CONTA"].notna() & (df_filtered["VALOR MOV.CONTA"] != 0)]

    entrada_df = df_filtered[df_filtered["ENTRADA_SAIDA"] == "ENTRADA"]
    saida_df = df_filtered[df_filtered["ENTRADA_SAIDA"] == "SAIDA"]

    # Deduplicacao especial apenas para distribuicao de lucros (pares mov/rateado).
    dist_mask = saida_df["PLANO DE CONTAS"].apply(
        lambda x: "DISTRIBUICAO DE LUCRO" in normalize_text(str(x))
        or "DISTRIBUICAO DE LUCROS" in normalize_text(str(x))
    )
    saida_dist = saida_df[dist_mask].copy()
    saida_outros = saida_df[~dist_mask].copy()

    # Saidas normais: manter regra anterior por data/plano/valor
    saida_outros = saida_outros.drop_duplicates(
        subset=["DATA MOV.CONTA", "PLANO DE CONTAS", "VALOR MOV.CONTA"],
        keep="first",
    )

    # Distribuicao: usar registro quando existir, senao fallback por data/plano/valor
    if not saida_dist.empty and "REGISTRO_MOVIMENTO" in saida_dist.columns:
        saida_dist_com_reg = saida_dist[saida_dist["REGISTRO_MOVIMENTO"].notna()].copy()
        saida_dist_sem_reg = saida_dist[saida_dist["REGISTRO_MOVIMENTO"].isna()].copy()

        if not saida_dist_com_reg.empty:
            saida_dist_com_reg["HAS_VALOR_MOV"] = (
                saida_dist_com_reg["VALOR_MOV_RAW"].notna() & (saida_dist_com_reg["VALOR_MOV_RAW"] != 0)
            )
            saida_dist_com_reg = saida_dist_com_reg.sort_values(
                ["REGISTRO_MOVIMENTO", "HAS_VALOR_MOV"], ascending=[True, False]
            )
            saida_dist_com_reg = saida_dist_com_reg.drop_duplicates(
                subset=["REGISTRO_MOVIMENTO"], keep="first"
            ).drop(columns=["HAS_VALOR_MOV"])

        if not saida_dist_sem_reg.empty:
            saida_dist_sem_reg = saida_dist_sem_reg.drop_duplicates(
                subset=["DATA MOV.CONTA", "PLANO DE CONTAS", "VALOR MOV.CONTA"],
                keep="first",
            )

        saida_dist_dedup = pd.concat([saida_dist_com_reg, saida_dist_sem_reg], ignore_index=True)
    else:
        saida_dist_dedup = saida_dist.drop_duplicates(
            subset=["DATA MOV.CONTA", "PLANO DE CONTAS", "VALOR MOV.CONTA"],
            keep="first",
        )

    saida_dedup = pd.concat([saida_outros, saida_dist_dedup], ignore_index=True)
    df_filtered = pd.concat([entrada_df, saida_dedup], ignore_index=True)

    df_filtered["ENTRADA/SAIDA"] = df_filtered["ENTRADA_SAIDA"]

    print(f"Total registros finais para carga: {len(df_filtered)}")
    return df_filtered


def generate_gerencial_data(df, db: Client):
    """Gera dados agregados gerenciais e salva."""
    print("Gerando dados gerenciais...")
    grouped = (
        df.groupby(["MES_ANO", "MES_NOME", "CENTRO DE CUSTO", "ENTRADA_SAIDA"])["VALOR MOV.CONTA"]
        .sum()
        .reset_index()
    )

    pivot = (
        grouped.pivot_table(
            index=["MES_ANO", "MES_NOME", "CENTRO DE CUSTO"],
            columns="ENTRADA_SAIDA",
            values="VALOR MOV.CONTA",
            fill_value=0,
        )
        .reset_index()
        .rename_axis(None, axis=1)
    )

    pivot["receita_total"] = pivot.get("ENTRADA", 0) if isinstance(pivot.get("ENTRADA"), pd.Series) else 0
    pivot["despesa_total"] = pivot.get("SAIDA", 0).abs() if isinstance(pivot.get("SAIDA"), pd.Series) else 0
    pivot["lucro"] = pivot["receita_total"] - pivot["despesa_total"]
    pivot["margem_liquida"] = (pivot["lucro"] / pivot["receita_total"] * 100).fillna(0)
    pivot = pivot.replace([float("inf"), float("-inf")], 0)
    pivot["margem_liquida"] = pivot["margem_liquida"].clip(lower=-999.99, upper=999.99)

    records = []
    for _, row in pivot.iterrows():
        records.append(
            {
                "mes_ano": row["MES_ANO"],
                "mes_nome": row["MES_NOME"],
                "centro_custo": row["CENTRO DE CUSTO"],
                "receita_total": float(row["receita_total"]),
                "despesa_total": float(row["despesa_total"]),
                "lucro": float(row["lucro"]),
                "margem_liquida": float(row["margem_liquida"]),
            }
        )

    print(f"Inserindo {len(records)} registros em dre_gerencial...")
    db.table("dre_gerencial").insert(records).execute()


def fetch_all_data(db: Client):
    """Busca todos os dados da tabela dre_financeiro para análise histórica."""
    print("Buscando histórico completo para análise preditiva...")
    all_records = []
    offset = 0
    limit = 1000
    
    while True:
        try:
            response = db.table("dre_financeiro").select("*").range(offset, offset + limit - 1).execute()
            records = response.data
            if not records:
                break
            all_records.extend(records)
            offset += limit
            if len(records) < limit:
                break
        except Exception as e:
            print(f"Erro ao buscar histórico (offset {offset}): {e}")
            break
            
    print(f"Histórico carregado: {len(all_records)} registros.")
    return pd.DataFrame(all_records)

def save_predictive_data(df_ignored, db: Client):
    """Processa e salva dados preditivos usando TODO o histórico do banco."""
    print("Processando dados preditivos (com histórico completo)...")
    
    try:
        # 1. Buscar histórico completo do banco
        df_full = fetch_all_data(db)
        
        if df_full.empty:
            print("Aviso: Nenhum dado histórico encontrado. Pulando predição.")
            return

        # 2. Renomear colunas para o formato esperado por process_predictive_data (formato Excel)
        # Banco (snake_case) -> Esperado (Upper/Excel like)
        col_map = {
            "plano_contas": "PLANO DE CONTAS",
            "entrada_saida": "ENTRADA/SAIDA",
            "valor": "VALOR MOV.CONTA",
            "mes_ano": "MES_ANO"
        }
        df_financeiro = df_full.rename(columns=col_map)
        
        # Garantir tipos numéricos
        df_financeiro["VALOR MOV.CONTA"] = pd.to_numeric(df_financeiro["VALOR MOV.CONTA"], errors="coerce").fillna(0)

        # 3. Processar
        # O segundo argumento (df_gerencial) não é usado na função atual, passamos None
        predictive_result = process_predictive_data(df_financeiro, None)
        
        data = {"data_type": "main", "data_json": predictive_result}
        
        # Test JSON serialization before sending to DB
        import json
        json_str = json.dumps(predictive_result)
        print(f"Predictive data JSON size: {len(json_str)} bytes")
        
        db.table("predictive_data").upsert(data, on_conflict="data_type").execute()
        print("Dados preditivos salvos com sucesso.")
        
    except Exception as e:
        print(f"ERRO ao salvar dados preditivos: {e}")
        import traceback
        traceback.print_exc()
        print("Continuando sem dados preditivos...")


def save_to_supabase(df, db: Client):
    """Salva dados brutos e agregados no Supabase (Incremental por mês)."""
    
    # Identificar meses presentes no arquivo
    meses_no_arquivo = df["MES_ANO"].unique().tolist()
    meses_no_arquivo = [m for m in meses_no_arquivo if pd.notna(m) and m != ""]
    
    print(f"Meses encontrados no arquivo: {meses_no_arquivo}")
    
    if not meses_no_arquivo:
        print("Aviso: Nenhum mês encontrado no arquivo. Nada será salvo.")
        return

    print("Removendo dados existentes APENAS para os meses do arquivo...")
    try:
        # Remover registros dos meses afetados para evitar duplicação
        # Supabase não suporta "IN" com lista muito grande facilmente na lib python as vezes, 
        # mas para poucos meses (1-12) é tranquilo fazer um loop ou usar filtro 'in'.
        # Vamos usar loop para garantir segurança e simplicidade.
        for mes in meses_no_arquivo:
            print(f"  Removendo mês {mes}...")
            db.table("dre_financeiro").delete().eq("mes_ano", mes).execute()
            db.table("dre_gerencial").delete().eq("mes_ano", mes).execute()
            
    except Exception as e:
        print(f"Erro ao limpar meses específicos: {e}")
        # Se falhar a limpeza parcial, melhor parar do que duplicar ou apagar tudo errado
        raise

    records = []
    for idx, row in df.iterrows():
        entrada_saida_val = row["ENTRADA_SAIDA"]
        if idx < 5:
            print(f"DEBUG row {idx}: ENTRADA/SAIDA = '{entrada_saida_val}' (type: {type(entrada_saida_val)})")

        records.append(
            {
                "mes_ano": row["MES_ANO"] if pd.notna(row["MES_ANO"]) else "",
                "mes_nome": row["MES_NOME"] if pd.notna(row["MES_NOME"]) else "",
                "data_mov": row["DATA MOV.CONTA"].isoformat() if pd.notna(row["DATA MOV.CONTA"]) else None,
                "centro_custo": row["CENTRO DE CUSTO"] if pd.notna(row["CENTRO DE CUSTO"]) else "",
                "parceiro": row["PARCEIRO"] if pd.notna(row["PARCEIRO"]) else "",
                "plano_contas": row["PLANO DE CONTAS"] if pd.notna(row["PLANO DE CONTAS"]) else "",
                "entrada_saida": entrada_saida_val if pd.notna(entrada_saida_val) else "",
                "valor": float(row["VALOR MOV.CONTA"]) if pd.notna(row["VALOR MOV.CONTA"]) else 0.0,
                "tipo_custo": row["TIPO_CUSTO"] if pd.notna(row["TIPO_CUSTO"]) else "",
            }
        )

    chunk_size = 1000
    print(f"Inserindo {len(records)} registros em dre_financeiro...")
    for i in range(0, len(records), chunk_size):
        chunk = records[i : i + chunk_size]
        db.table("dre_financeiro").insert(chunk).execute()
        print(f"  Chunk {i} a {i + len(chunk)} inserido")

    generate_gerencial_data(df, db)


def main():
    print("=== Iniciando processamento ETL (Supabase) ===")
    
    # Verificar se caminho do arquivo foi passado como argumento
    if len(sys.argv) > 1:
        file_path = sys.argv[1]
    else:
        file_path = os.path.join(os.path.dirname(__file__), "..", "data", "relatorio_flux.xlsx")
    
    print(f"Lendo arquivo: {file_path}")
    
    try:
        df = process_excel_data(file_path)
        save_to_supabase(df, supabase)
        save_predictive_data(df, supabase)
        print("=== Processamento concluído ===")
    except Exception as e:
        print(f"Erro durante processamento: {e}")
        raise


if __name__ == "__main__":
    main()

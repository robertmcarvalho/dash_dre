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
from typing import Optional

import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.database import supabase  # noqa: E402

COL_DATA = "DATA MOVIMENTO"
COL_ENTRADA = "ENTRADA/SAÍDA"
COL_PLANO = "PLANO DE CONTAS"
COL_CENTRO = "CENTRO DE CUSTO (FARMÁCIA PRESTADORA)"
COL_PARCEIRO = "PARCEIROV (FARMÁCIA PAGADOR OU ENTREGADOR)"
COL_REFERENTE = "REFERENTE"
COL_PAGAMENTO = "PAGAMENTO"
COL_VALOR_MOV = "VALOR MOV.CONTA"
COL_VALOR_RATEADO = "VALOR RATEADO PARA O PRESTADOR"
COL_VALOR_ORIG = "VALOR ORIGINAL"
COL_NUM_DOC = "NÚMERO DOCTO"
COL_PARC = "PARC"
COL_REG_MOV = "REGISTRO MOVIMENTO"
COL_REG_COMP = "REGISTRO COMPROMISSO"

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


def normalize_center_key(text: str) -> str:
    """
    Normalização "solta" para centros de custo:
    - remove acentos
    - upper
    - remove espaços/pontuação para igualar variações tipo 'LJ06' vs 'LJ 06'
    """
    s = normalize_text(text)
    import re

    return re.sub(r"[^A-Z0-9]", "", s)

def extract_store_code(text: str) -> Optional[str]:
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
    m = re.search(r"\b(\d{4,8})\b", s)
    if m:
        return m.group(1)
    return None


def read_coop_excel(file_path: str) -> pd.DataFrame:
    """
    Lê o Excel da cooperativa aceitando variações de nome de aba.
    - legado: "Planilha1"
    - alguns relatórios exportados: "Sheet1"
    """
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

    last_err = None
    for sheet in ("Planilha1", "Sheet1", 0):
        try:
            preview = pd.read_excel(file_path, sheet_name=sheet, header=None, nrows=40, engine="openpyxl")
            header_row = detect_header_row(preview)
            df = pd.read_excel(
                file_path,
                sheet_name=sheet,
                header=header_row if header_row is not None else 0,
                engine="openpyxl",
            )
            if df is not None and not df.empty:
                return df
        except Exception as e:
            last_err = e
            continue
    if last_err:
        raise last_err
    raise RuntimeError("Falha ao ler o Excel (sem erro capturado).")


def load_center_whitelist(db: Client) -> tuple[set[str], dict[str, str], dict[str, str]]:
    """Centros que geram receita no Flux (receitas com entregas)."""
    try:
        all_rows = []
        start = 0
        chunk = 1000
        while True:
            resp = (
                db.table("dre_financeiro")
                .select("centro_custo, plano_contas, entrada_saida")
                .eq("entrada_saida", "ENTRADA")
                .ilike("plano_contas", "%RECEITAS COM ENTREGAS%")
                .range(start, start + chunk - 1)
                .execute()
            )
            batch = resp.data or []
            all_rows.extend(batch)
            if len(batch) < chunk:
                break
            start += chunk
        rows = all_rows
        name_set = {normalize_text(r.get("centro_custo")) for r in rows if r.get("centro_custo")}
        key_map: dict[str, str] = {}
        code_map: dict[str, str] = {}
        for r in rows:
            c = r.get("centro_custo")
            if not c:
                continue
            key = normalize_center_key(c)
            if key and key not in key_map:
                key_map[key] = c
            code = extract_store_code(c)
            if code and code not in code_map:
                code_map[code] = c
        return name_set, code_map, key_map
    except Exception:
        return set(), {}, {}


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


def parse_periodo_referente(text: str):
    """
    Extrai (inicio, fim) do padrão 'dd/mm/yyyy a dd/mm/yyyy' dentro de REFERENTE.
    Retorna (None, None) se não encontrar.
    """
    if text is None:
        return None, None
    s = str(text)
    import re
    from datetime import datetime

    m = re.search(r"(\d{2}/\d{2}/\d{4})\s*(?:a|à)\s*(\d{2}/\d{2}/\d{4})", s, flags=re.IGNORECASE)
    if not m:
        return None, None
    try:
        d1 = datetime.strptime(m.group(1), "%d/%m/%Y").date()
        d2 = datetime.strptime(m.group(2), "%d/%m/%Y").date()
        return d1, d2
    except Exception:
        return None, None


def week_bounds_from_end_date(end_date):
    """
    Recebe uma data e retorna (segunda, domingo) da semana.
    Regra aprovada: usar a data final do REFERENTE como referência.
    """
    if end_date is None:
        return None, None
    from datetime import timedelta

    start = end_date - timedelta(days=end_date.weekday())
    end = start + timedelta(days=6)
    return start, end


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
    df = read_coop_excel(file_path)
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

    whitelist, whitelist_codes, whitelist_key_map = load_center_whitelist(supabase)

    def map_to_pharmacy(candidate):
        if candidate is None:
            return None
        s = str(candidate).strip()
        if not s or s.lower() == "nan":
            return None
        code = extract_store_code(s)
        if code and code in whitelist_codes:
            return whitelist_codes[code]
        k = normalize_center_key(s)
        if k and k in whitelist_key_map:
            return whitelist_key_map[k]
        if normalize_text(s) in whitelist:
            return s
        return None

    def centro_valid(row):
        # Prioridade: centro prestadora, se for farmácia válida
        centro = map_to_pharmacy(row.get(COL_CENTRO))
        if centro:
            return centro

        # Fallback específico para receitas: se for ENTREGAS e não houver centro válido,
        # usar a farmácia no campo PARCEIROV (muitas vezes o centro vem vazio/errado no Excel).
        entrada = normalize_text(row.get(COL_ENTRADA))
        plano = normalize_text(row.get(COL_PLANO))
        if entrada == "ENTRADA" and "RECEITAS COM ENTREGAS" in plano:
            parceiro = map_to_pharmacy(row.get(COL_PARCEIRO))
            if parceiro:
                return parceiro

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

    meses_no_arquivo = sorted({m for m in df_dre["MES_ANO"].dropna().astype(str).tolist() if m and m != "0000-00"})
    print(f"Meses encontrados no arquivo coop: {meses_no_arquivo}")
    if not meses_no_arquivo:
        print("Aviso: Nenhum mês encontrado no arquivo. Nada será salvo.")
        return

    print("Limpando APENAS os meses do arquivo (coop) no Supabase...")
    for mes in meses_no_arquivo:
        print(f"  Removendo mês {mes}...")
        supabase.table("dre_financeiro_coop").delete().eq("mes_ano", mes).execute()
        supabase.table("dre_gerencial_coop").delete().eq("mes_ano", mes).execute()
        try:
            supabase.table("coop_adiantamentos_saldo").delete().eq("mes_ano", mes).execute()
        except Exception:
            pass

    # predictive_data_coop é derivado e pode ser regravado a cada rodada
    try:
        supabase.table("predictive_data_coop").delete().eq("data_type", "main").execute()
    except Exception:
        pass

    chunk = 1000
    for i in range(0, len(fin_records), chunk):
        supabase.table("dre_financeiro_coop").insert(fin_records[i : i + chunk]).execute()
    print(f"Inseridos {len(fin_records)} registros em dre_financeiro_coop")

    # ----------------------------
    # Dataset de conciliação (Cooperativa): somente Entregas e Pagamento de Entregadores
    # Semana definida pelo fim do intervalo em REFERENTE (Seg-Dom).
    # ----------------------------
    df_raw = read_coop_excel(file_path)
    df_raw[COL_DATA] = pd.to_datetime(df_raw.get(COL_DATA), dayfirst=True, errors="coerce")
    df_raw[COL_PAGAMENTO] = pd.to_datetime(df_raw.get(COL_PAGAMENTO), dayfirst=True, errors="coerce")

    df_raw["plano_norm"] = df_raw[COL_PLANO].apply(normalize_text)
    df_raw["entrada_norm"] = df_raw[COL_ENTRADA].apply(normalize_text)

    mask_plano = df_raw["plano_norm"].isin(
        [
            "RECEITAS COM ENTREGAS",
            "PAGAMENTO DE ENTREGADORES",
            "DIARIA DE MOTOBOY",
            "SOLICITACAO DE ADIANTAMENTO",
            "ENTRADA DE REEMBOLSO ADIANTAMENTO",
        ]
    )
    df_conc = df_raw[mask_plano].copy()
    if not df_conc.empty:
        df_conc["valor_mov"] = pd.to_numeric(df_conc.get(COL_VALOR_MOV), errors="coerce")
        df_conc["valor_rateado"] = pd.to_numeric(df_conc.get(COL_VALOR_RATEADO), errors="coerce")

        # Centro final (farmácia) com as mesmas regras do DRE (inclui fallback parceiro para ENTREGAS)
        df_conc["centro_norm"] = df_conc.get(COL_CENTRO).apply(normalize_text)
        df_conc["centro_custo_final"] = df_conc.apply(centro_valid, axis=1)

        periodo_ini = []
        periodo_fim = []
        semana_ini = []
        semana_fim = []
        for ref, data_mov in zip(df_conc.get(COL_REFERENTE), df_conc.get(COL_DATA)):
            di, dfim = parse_periodo_referente(ref)
            if dfim is None and pd.notna(data_mov):
                dfim = data_mov.date()
            if di is None and pd.notna(data_mov):
                di = data_mov.date()
            wi, wf = week_bounds_from_end_date(dfim)
            periodo_ini.append(di)
            periodo_fim.append(dfim)
            semana_ini.append(wi)
            semana_fim.append(wf)
        df_conc["periodo_inicio"] = periodo_ini
        df_conc["periodo_fim"] = periodo_fim
        df_conc["semana_inicio"] = semana_ini
        df_conc["semana_fim"] = semana_fim

        df_conc["linha_tipo"] = df_conc["valor_rateado"].apply(
            lambda v: "RATEIO" if pd.notna(v) and float(v) != 0 else "MOV"
        )

        def to_iso(d):
            return d.isoformat() if d else None

        conc_records = []
        for _, r in df_conc.iterrows():
            data_mov = r.get(COL_DATA)
            data_pag = r.get(COL_PAGAMENTO)
            conc_records.append(
                {
                    "semana_inicio": to_iso(r.get("semana_inicio")),
                    "semana_fim": to_iso(r.get("semana_fim")),
                    "periodo_inicio": to_iso(r.get("periodo_inicio")),
                    "periodo_fim": to_iso(r.get("periodo_fim")),
                    "data_mov": data_mov.date().isoformat() if pd.notna(data_mov) else None,
                    "data_pag": data_pag.date().isoformat() if pd.notna(data_pag) else None,
                    "entrada_saida": r.get(COL_ENTRADA),
                    "plano_contas": r.get(COL_PLANO),
                    "parceiro": None if pd.isna(r.get(COL_PARCEIRO)) else r.get(COL_PARCEIRO),
                    "referente": None if pd.isna(r.get(COL_REFERENTE)) else r.get(COL_REFERENTE),
                    "numero_docto": None if pd.isna(r.get(COL_NUM_DOC)) else str(r.get(COL_NUM_DOC)),
                    "registro_movimento": None if pd.isna(r.get(COL_REG_MOV)) else str(r.get(COL_REG_MOV)),
                    "registro_compromisso": None if pd.isna(r.get(COL_REG_COMP)) else str(r.get(COL_REG_COMP)),
                    "centro_custo_raw": None if pd.isna(r.get(COL_CENTRO)) else r.get(COL_CENTRO),
                    "centro_custo_final": r.get("centro_custo_final"),
                    "valor_mov": safe_num(r.get("valor_mov")),
                    "valor_rateado": safe_num(r.get("valor_rateado")),
                    "linha_tipo": r.get("linha_tipo"),
                }
            )

        if conc_records:
            # Limpa apenas as semanas presentes no arquivo para evitar duplicação
            try:
                week_pairs = sorted(
                    {
                        (r.get("semana_inicio"), r.get("semana_fim"))
                        for r in conc_records
                        if r.get("semana_inicio") and r.get("semana_fim")
                    }
                )
                if week_pairs:
                    print(f"Limpando semanas específicas em coop_conciliacao_entregas: {len(week_pairs)}")
                    for si, sf in week_pairs:
                        supabase.table("coop_conciliacao_entregas").delete().eq("semana_inicio", si).eq(
                            "semana_fim", sf
                        ).execute()
            except Exception:
                # tabela pode não existir ainda (migração pendente)
                pass

            ok = True
            try:
                for i in range(0, len(conc_records), chunk):
                    supabase.table("coop_conciliacao_entregas").insert(conc_records[i : i + chunk]).execute()
            except Exception as e:
                ok = False
                print("AVISO: falha ao inserir coop_conciliacao_entregas (migração pendente?):", e)
            if ok:
                print(f"Inseridos {len(conc_records)} registros em coop_conciliacao_entregas")

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

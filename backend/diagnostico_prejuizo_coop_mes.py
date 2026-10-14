import argparse
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime

import pandas as pd

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from backend.database import supabase  # noqa: E402
from backend.main import _fetch_all_filtered, norm  # noqa: E402
from etl.process_coop import read_coop_excel, parse_periodo_referente, COL_DATA, COL_PLANO, COL_ENTRADA, COL_VALOR_RATEADO, COL_CENTRO, COL_PARCEIRO  # noqa: E402


def _to_date(value) -> date | None:
    if value is None:
        return None
    d = pd.to_datetime(str(value)[:10], errors="coerce")
    if pd.isna(d):
        return None
    return d.date()


def _date_in_any_range(d: date | None, ranges: list[tuple[date, date]]) -> bool:
    if not d or not ranges:
        return False
    for a, b in ranges:
        if a <= d <= b:
            return True
    return False


@dataclass(frozen=True)
class MonthWindow:
    month: str
    start: date
    end: date


def _month_window(month: str) -> MonthWindow:
    try:
        y, m = month.split("-")
        y = int(y)
        m = int(m)
        if m < 1 or m > 12:
            raise ValueError
    except Exception:
        raise SystemExit("Use --month YYYY-MM (ex.: 2026-02).")

    from calendar import monthrange

    last_day = monthrange(y, m)[1]
    start = date(y, m, 1)
    end = date(y, m, last_day)
    return MonthWindow(month=month, start=start, end=end)


def _dedup_diaria(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    for c in ["registro_movimento", "registro_compromisso", "numero_docto", "periodo_inicio", "periodo_fim"]:
        if c not in df.columns:
            df[c] = None
    df["dedup_key"] = (
        df.get("centro_custo_final").astype(str)
        + "|"
        + df.get("registro_movimento").astype(str)
        + "|"
        + df.get("registro_compromisso").astype(str)
        + "|"
        + df.get("numero_docto").astype(str)
        + "|"
        + df.get("valor_rateado").astype(str)
        + "|"
        + df.get("periodo_inicio").astype(str)
        + "|"
        + df.get("periodo_fim").astype(str)
    )
    return df.drop_duplicates(subset=["dedup_key"])


def _fetch_rateio_coop(month: MonthWindow) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    start = month.start.isoformat()
    end = month.end.isoformat()

    # Receitas (competência): usa periodo_fim
    rec_rows = _fetch_all_filtered(
        supabase,
        "coop_conciliacao_entregas",
        "centro_custo_final,valor_rateado,periodo_inicio,periodo_fim",
        lambda q: q.eq("plano_contas", "RECEITAS COM ENTREGAS")
        .eq("linha_tipo", "RATEIO")
        .not_.is_("periodo_fim", "null")
        .gte("periodo_fim", start)
        .lte("periodo_fim", end),
        chunk=1000,
        max_rows=800000,
    )
    df_rec = pd.DataFrame(rec_rows)

    pay_rows = _fetch_all_filtered(
        supabase,
        "coop_conciliacao_entregas",
        "centro_custo_final,valor_rateado,periodo_inicio,periodo_fim",
        lambda q: q.eq("plano_contas", "PAGAMENTO DE ENTREGADORES")
        .eq("linha_tipo", "RATEIO")
        .not_.is_("periodo_fim", "null")
        .gte("periodo_fim", start)
        .lte("periodo_fim", end),
        chunk=1000,
        max_rows=800000,
    )
    df_pay = pd.DataFrame(pay_rows)

    dia_rows = _fetch_all_filtered(
        supabase,
        "coop_conciliacao_entregas",
        "centro_custo_final,valor_rateado,periodo_inicio,periodo_fim,data_mov,entrada_saida,registro_movimento,registro_compromisso,numero_docto",
        lambda q: q.eq("plano_contas", "DIARIA DE MOTOBOY").eq("linha_tipo", "RATEIO"),
        chunk=1000,
        max_rows=800000,
    )
    df_dia = pd.DataFrame(dia_rows)

    def clean_centros(df: pd.DataFrame) -> pd.DataFrame:
        if df is None or df.empty:
            return df
        df = df.copy()
        df["centro"] = df["centro_custo_final"].fillna("Não Especificado")
        df = df[df["centro"].apply(norm) != "NAO ESPECIFICADO"]
        df["valor_rateado"] = pd.to_numeric(df.get("valor_rateado"), errors="coerce").fillna(0.0)
        df["pi"] = df.get("periodo_inicio").apply(_to_date)
        df["pf"] = df.get("periodo_fim").apply(_to_date)
        df["dm"] = df.get("data_mov").apply(_to_date) if "data_mov" in df.columns else None
        return df

    df_rec = clean_centros(df_rec)
    df_pay = clean_centros(df_pay)
    df_dia = clean_centros(df_dia)

    # Filtra diárias: somente SAÍDA e competência por periodo_fim (ou fallback por data_mov quando não houver pf)
    if df_dia is not None and not df_dia.empty:
        df_dia = df_dia[df_dia.get("entrada_saida").apply(norm) == "SAIDA"].copy()
        df_dia = _dedup_diaria(df_dia)
        in_pf = df_dia["pf"].notna() & (df_dia["pf"] >= month.start) & (df_dia["pf"] <= month.end)
        in_dm = df_dia["pf"].isna() & df_dia["dm"].notna() & (df_dia["dm"] >= month.start) & (df_dia["dm"] <= month.end)
        df_dia = df_dia[in_pf | in_dm].copy()

    return df_rec, df_pay, df_dia


def _fetch_flux_deliveries(min_d: date, max_d: date) -> pd.DataFrame:
    rows = _fetch_all_filtered(
        supabase,
        "dre_financeiro",
        "data_mov,centro_custo,valor",
        lambda q: q.eq("entrada_saida", "ENTRADA")
        .eq("plano_contas", "RECEITAS COM ENTREGAS")
        .not_.is_("data_mov", "null")
        .gte("data_mov", min_d.isoformat())
        .lte("data_mov", max_d.isoformat()),
        chunk=1000,
        max_rows=800000,
    )
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["dm"] = df["data_mov"].apply(_to_date)
    df["centro_norm"] = df.get("centro_custo").fillna("Não Especificado").apply(norm)
    df = df[df["centro_norm"] != "NAO ESPECIFICADO"].copy()
    df["valor"] = pd.to_numeric(df.get("valor"), errors="coerce").fillna(0.0)
    return df


def diagnostico(month_str: str) -> tuple[pd.DataFrame, dict]:
    month = _month_window(month_str)
    df_rec, df_pay, df_dia = _fetch_rateio_coop(month)

    rec = (df_rec.groupby("centro")["valor_rateado"].sum().to_dict() if df_rec is not None and not df_rec.empty else {})
    pay = (df_pay.groupby("centro")["valor_rateado"].sum().to_dict() if df_pay is not None and not df_pay.empty else {})
    dia = (df_dia.groupby("centro")["valor_rateado"].sum().to_dict() if df_dia is not None and not df_dia.empty else {})

    # ciclos (competência) do mês: por pi/pf de pagamentos
    cycles_by_farm: dict[str, set[tuple[date, date]]] = {}
    if df_pay is not None and not df_pay.empty:
        dfp = df_pay.dropna(subset=["pi", "pf"]).copy()
        if not dfp.empty:
            cycles_by_farm = {c: set(zip(g["pi"], g["pf"])) for c, g in dfp.groupby("centro")}

    # janela global para buscar flux (reduz custo)
    all_cycles = sorted({cy for s in cycles_by_farm.values() for cy in s})
    if all_cycles:
        min_pi = min(a for a, _ in all_cycles)
        max_pf = max(b for _, b in all_cycles)
    else:
        min_pi = month.start
        max_pf = month.end

    df_flux = _fetch_flux_deliveries(min_pi, max_pf)

    rows = []
    farms = sorted(set(rec.keys()) | set(pay.keys()) | set(dia.keys()) | set(cycles_by_farm.keys()))
    for f in farms:
        receita = float(rec.get(f, 0.0) or 0.0)
        pagamento = float(pay.get(f, 0.0) or 0.0)
        diarias = float(dia.get(f, 0.0) or 0.0)
        custo_bruto = pagamento + diarias
        iss = receita * 0.03
        inss = pagamento * 0.045  # regra vigente no backend
        saldo = receita - (custo_bruto + iss + inss)
        pct_repasse = (custo_bruto / receita * 100) if receita else None

        # Flux: soma das entregas dentro dos ciclos de competência da farmácia (DATA_MOV no intervalo)
        receita_flux = 0.0
        cycles = sorted(list(cycles_by_farm.get(f, set())))
        if df_flux is not None and not df_flux.empty and cycles:
            f_norm = norm(f)
            df_f = df_flux[df_flux["centro_norm"] == f_norm]
            if not df_f.empty:
                receita_flux = float(df_f[df_f["dm"].apply(lambda d: _date_in_any_range(d, cycles))]["valor"].sum())

        pct_flux_total = (
            (receita_flux / (receita_flux + receita) * 100) if (receita_flux + receita) > 0 else None
        )

        rows.append(
            {
                "farmacia": f,
                "competencia": month.month,
                "receita_coop": receita,
                "receita_flux": receita_flux,
                "pct_flux_total": pct_flux_total,
                "pagamento_entregadores": pagamento,
                "diarias": diarias,
                "custo_bruto": custo_bruto,
                "iss_3pct": iss,
                "inss_4_5pct_sobre_pagamento": inss,
                "saldo_coop_estimado": saldo,
                "pct_repasse_custo_bruto": pct_repasse,
                "ciclos_qtd": len(cycles),
            }
        )

    df = pd.DataFrame(rows)
    if df.empty:
        return df, {"month": month.month, "start": month.start.isoformat(), "end": month.end.isoformat()}

    df = df.sort_values(["saldo_coop_estimado", "receita_coop"], ascending=[True, False])

    summary = {
        "month": month.month,
        "start": month.start.isoformat(),
        "end": month.end.isoformat(),
        "farms_total": int(df["farmacia"].nunique()),
        "farms_prejuizo": int((df["saldo_coop_estimado"] < 0).sum()),
        "receita_coop_total": float(df["receita_coop"].sum()),
        "pagamento_total": float(df["pagamento_entregadores"].sum()),
        "diarias_total": float(df["diarias"].sum()),
        "iss_total": float(df["iss_3pct"].sum()),
        "inss_total": float(df["inss_4_5pct_sobre_pagamento"].sum()),
        "saldo_total": float(df["saldo_coop_estimado"].sum()),
    }
    return df, summary


def reconcile_with_excel(month_str: str) -> pd.DataFrame:
    """
    Reconciliação simples (sanidade): soma total do Excel base (coop) para entregas/pagamento/diárias
    por competência (periodo_fim), usando VALOR RATEADO.
    """
    month = _month_window(month_str)
    # localizar arquivo base (coop) no repo
    coop_path = None
    for p in os.listdir(os.path.join(REPO_ROOT, "data")):
        if "Cooperativa" in p and "2026" in p and p.lower().endswith(".xlsx"):
            coop_path = os.path.join(REPO_ROOT, "data", p)
            break
    if not coop_path or not os.path.exists(coop_path):
        return pd.DataFrame()

    df = read_coop_excel(coop_path)
    df[COL_DATA] = pd.to_datetime(df.get(COL_DATA), dayfirst=True, errors="coerce")
    df[COL_VALOR_RATEADO] = pd.to_numeric(df.get(COL_VALOR_RATEADO), errors="coerce")
    df = df[(df[COL_VALOR_RATEADO].notna()) & (df[COL_VALOR_RATEADO] != 0)].copy()

    # competência via REFERENTE
    pis = []
    pfs = []
    for ref, dm in zip(df.get("REFERENTE"), df.get(COL_DATA)):
        pi, pf = parse_periodo_referente(ref)
        if pf is None and pd.notna(dm):
            pf = dm.date()
        if pi is None and pd.notna(dm):
            pi = dm.date()
        if pi and pf and pi > pf:
            pi, pf = pf, pi
        pis.append(pi)
        pfs.append(pf)
    df["pf"] = pfs

    df = df[df["pf"].notna()].copy()
    df = df[(df["pf"] >= month.start) & (df["pf"] <= month.end)].copy()
    df["plano_norm"] = df.get(COL_PLANO).fillna("").apply(norm)
    df["entrada_norm"] = df.get(COL_ENTRADA).fillna("").apply(norm)

    def sum_plan(plano: str, entrada: str | None = None) -> float:
        m = df["plano_norm"] == norm(plano)
        if entrada is not None:
            m = m & (df["entrada_norm"] == norm(entrada))
        return float(pd.to_numeric(df.loc[m, COL_VALOR_RATEADO], errors="coerce").fillna(0.0).sum())

    return pd.DataFrame(
        [
            {"fonte": "excel_base", "month": month.month, "metric": "receita_entregas", "total": sum_plan("RECEITAS COM ENTREGAS", "ENTRADA")},
            {"fonte": "excel_base", "month": month.month, "metric": "pagamento_entregadores", "total": sum_plan("PAGAMENTO DE ENTREGADORES", "SAÍDA")},
            {"fonte": "excel_base", "month": month.month, "metric": "diarias", "total": sum_plan("DIARIA DE MOTOBOY", "SAÍDA")},
        ]
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", required=True, help="YYYY-MM (ex.: 2026-02)")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    df, summary = diagnostico(args.month)
    df_rec = reconcile_with_excel(args.month)

    os.makedirs(os.path.join(REPO_ROOT, "logs"), exist_ok=True)
    out = args.out or os.path.join(REPO_ROOT, "logs", f"diagnostico_prejuizo_coop_{args.month}.xlsx")

    with pd.ExcelWriter(out, engine="openpyxl") as xw:
        pd.DataFrame([summary]).to_excel(xw, index=False, sheet_name="Resumo")
        if df is not None and not df.empty:
            df.to_excel(xw, index=False, sheet_name="Por_Farmacia")
            df[df["saldo_coop_estimado"] < 0].to_excel(xw, index=False, sheet_name="Prejuizo")
            df.sort_values("saldo_coop_estimado", ascending=True).head(50).to_excel(xw, index=False, sheet_name="Top_Piores")
            df.sort_values("saldo_coop_estimado", ascending=False).head(50).to_excel(xw, index=False, sheet_name="Top_Melhores")
        if df_rec is not None and not df_rec.empty:
            df_rec.to_excel(xw, index=False, sheet_name="Recon_Excel_Base")

    print(f"OK: diagnóstico salvo em {out}")
    print(summary)


if __name__ == "__main__":
    main()


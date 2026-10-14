import argparse
import os
import sys
from collections import Counter, defaultdict
from datetime import date

import pandas as pd

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from backend.database import supabase  # noqa: E402
from backend.main import _fetch_all_filtered, norm  # noqa: E402


def _to_date(value) -> date | None:
    if value is None:
        return None
    d = pd.to_datetime(str(value)[:10], errors="coerce")
    if pd.isna(d):
        return None
    return d.date()


def _date_in_any_range(d: date | None, ranges: set[tuple[date, date]]) -> bool:
    if not d or not ranges:
        return False
    for a, b in ranges:
        if a <= d <= b:
            return True
    return False


def _dedup_key_df(df: pd.DataFrame) -> pd.Series:
    cols = [
        "centro_custo_final",
        "registro_movimento",
        "registro_compromisso",
        "numero_docto",
        "valor_rateado",
        "periodo_inicio",
        "periodo_fim",
        "data_mov",
    ]
    for c in cols:
        if c not in df.columns:
            df[c] = None
    tmp = df[cols].astype(str)
    if tmp.empty:
        return pd.Series([], index=tmp.index, dtype=object)
    return tmp.apply(lambda r: "|".join(r.values.tolist()), axis=1)


def _count_dups(df: pd.DataFrame) -> tuple[int, int]:
    if df is None or df.empty:
        return 0, 0
    keys = _dedup_key_df(df)
    counts = Counter(keys.tolist())
    dup_keys = sum(1 for v in counts.values() if v > 1)
    dup_rows = sum(v - 1 for v in counts.values() if v > 1)
    return dup_keys, dup_rows


def _sum_rate(df: pd.DataFrame) -> float:
    if df is None or df.empty:
        return 0.0
    s = pd.to_numeric(df.get("valor_rateado"), errors="coerce").fillna(0.0)
    return float(s.sum())


def audit_range(date_from: str, date_to: str, top: int = 30) -> tuple[pd.DataFrame, pd.DataFrame]:
    d0 = _to_date(date_from)
    d1 = _to_date(date_to)
    if not d0 or not d1:
        raise SystemExit("Intervalo inválido, use YYYY-MM-DD.")

    # Pay days (rodadas) existentes no range
    pay_day_rows = _fetch_all_filtered(
        supabase,
        "coop_conciliacao_entregas",
        "data_mov",
        lambda q: q.eq("plano_contas", "PAGAMENTO DE ENTREGADORES")
        .eq("linha_tipo", "RATEIO")
        .gte("data_mov", str(d0))
        .lte("data_mov", str(d1))
        .not_.is_("data_mov", "null")
        .order("data_mov", desc=False),
        chunk=1000,
        max_rows=200000,
    )
    df_days = pd.DataFrame(pay_day_rows)
    if df_days.empty:
        raise SystemExit("Não encontrei rodadas (pay_day) no intervalo informado.")

    pay_days = sorted(set(df_days["data_mov"].astype(str).str.slice(0, 10).tolist()))

    all_rows = []
    day_summary = []

    for pay_day in pay_days:
        pd_day = _to_date(pay_day)

        pay_rows = _fetch_all_filtered(
            supabase,
            "coop_conciliacao_entregas",
            "centro_custo_final,valor_rateado,periodo_inicio,periodo_fim,data_mov,referente,numero_docto,registro_movimento,registro_compromisso",
            lambda q: q.eq("plano_contas", "PAGAMENTO DE ENTREGADORES")
            .eq("linha_tipo", "RATEIO")
            .eq("data_mov", pd_day),
            chunk=1000,
            max_rows=400000,
        )
        df_pay = pd.DataFrame(pay_rows)
        if df_pay.empty:
            continue

        df_pay["centro"] = df_pay["centro_custo_final"].fillna("Não Especificado")
        df_pay["centro_norm"] = df_pay["centro"].apply(norm)
        df_pay["valor_rateado"] = pd.to_numeric(df_pay.get("valor_rateado"), errors="coerce").fillna(0.0)

        pay_dup_keys, pay_dup_rows = _count_dups(df_pay)

        # ciclos por farmácia (exclui NAO ESPECIFICADO)
        ciclos_por_farm: dict[str, set[tuple[date, date]]] = defaultdict(set)
        missing_cycle_rows = 0
        for _, r in df_pay.iterrows():
            if r.get("centro_norm") == "NAO ESPECIFICADO":
                continue
            pi = _to_date(r.get("periodo_inicio"))
            pf = _to_date(r.get("periodo_fim"))
            if not pi or not pf:
                missing_cycle_rows += 1
                continue
            if pi > pf:
                pi, pf = pf, pi
            ciclos_por_farm[str(r.get("centro"))].add((pi, pf))

        if not ciclos_por_farm:
            day_summary.append(
                {
                    "pay_day": pay_day,
                    "farms": int((df_pay["centro_norm"] != "NAO ESPECIFICADO").sum()),
                    "cycles_window_start": None,
                    "cycles_window_end": None,
                    "missing_cycle_rows": missing_cycle_rows,
                    "pay_nao_especificado_rows": int((df_pay["centro_norm"] == "NAO ESPECIFICADO").sum()),
                    "pay_dup_keys": pay_dup_keys,
                    "pay_dup_extra_rows": pay_dup_rows,
                    "diaria_leak_total": 0.0,
                    "diaria_leak_farms": 0,
                }
            )
            continue

        all_wanted = set()
        min_pi = None
        max_pf = None
        for wanted in ciclos_por_farm.values():
            all_wanted |= wanted
            lo = min(a for a, _ in wanted)
            hi = max(b for _, b in wanted)
            min_pi = lo if min_pi is None else min(min_pi, lo)
            max_pf = hi if max_pf is None else max(max_pf, hi)

        # Diárias na janela dos ciclos (período apurado + fallback sem período)
        diaria_rows_periodo = _fetch_all_filtered(
            supabase,
            "coop_conciliacao_entregas",
            "centro_custo_final,valor_rateado,periodo_inicio,periodo_fim,data_mov,referente,numero_docto,registro_movimento,registro_compromisso",
            lambda q: q.eq("plano_contas", "DIARIA DE MOTOBOY")
            .eq("linha_tipo", "RATEIO")
            .not_.is_("periodo_inicio", "null")
            .not_.is_("periodo_fim", "null")
            .gte("periodo_fim", str(min_pi))
            .lte("periodo_fim", str(max_pf)),
            chunk=1000,
            max_rows=400000,
        )
        diaria_rows_missing_pi = _fetch_all_filtered(
            supabase,
            "coop_conciliacao_entregas",
            "centro_custo_final,valor_rateado,periodo_inicio,periodo_fim,data_mov,referente,numero_docto,registro_movimento,registro_compromisso",
            lambda q: q.eq("plano_contas", "DIARIA DE MOTOBOY")
            .eq("linha_tipo", "RATEIO")
            .is_("periodo_inicio", "null")
            .gte("data_mov", str(min_pi))
            .lte("data_mov", str(max_pf)),
            chunk=1000,
            max_rows=400000,
        )
        diaria_rows_missing_pf = _fetch_all_filtered(
            supabase,
            "coop_conciliacao_entregas",
            "centro_custo_final,valor_rateado,periodo_inicio,periodo_fim,data_mov,referente,numero_docto,registro_movimento,registro_compromisso",
            lambda q: q.eq("plano_contas", "DIARIA DE MOTOBOY")
            .eq("linha_tipo", "RATEIO")
            .is_("periodo_fim", "null")
            .gte("data_mov", str(min_pi))
            .lte("data_mov", str(max_pf)),
            chunk=1000,
            max_rows=400000,
        )

        df_diaria = pd.DataFrame((diaria_rows_periodo or []) + (diaria_rows_missing_pi or []) + (diaria_rows_missing_pf or []))
        if df_diaria.empty:
            day_summary.append(
                {
                    "pay_day": pay_day,
                    "farms": len(ciclos_por_farm),
                    "cycles_window_start": str(min_pi),
                    "cycles_window_end": str(max_pf),
                    "missing_cycle_rows": missing_cycle_rows,
                    "pay_nao_especificado_rows": int((df_pay["centro_norm"] == "NAO ESPECIFICADO").sum()),
                    "pay_dup_keys": pay_dup_keys,
                    "pay_dup_extra_rows": pay_dup_rows,
                    "diaria_leak_total": 0.0,
                    "diaria_leak_farms": 0,
                }
            )
            continue

        df_diaria["centro"] = df_diaria["centro_custo_final"].fillna("Não Especificado")
        df_diaria["centro_norm"] = df_diaria["centro"].apply(norm)
        df_diaria = df_diaria[df_diaria["centro_norm"] != "NAO ESPECIFICADO"]
        df_diaria["valor_rateado"] = pd.to_numeric(df_diaria.get("valor_rateado"), errors="coerce").fillna(0.0)
        df_diaria["pi"] = df_diaria["periodo_inicio"].apply(_to_date)
        df_diaria["pf"] = df_diaria["periodo_fim"].apply(_to_date)
        df_diaria["dm"] = df_diaria["data_mov"].apply(_to_date)
        df_diaria["dedup_key"] = _dedup_key_df(df_diaria)
        df_diaria = df_diaria.drop_duplicates(subset=["dedup_key"])

        diaria_dup_keys, diaria_dup_rows = _count_dups(df_diaria)

        leak_total = 0.0
        leak_farms = 0

        for farm, wanted in ciclos_por_farm.items():
            df_f = df_diaria[df_diaria["centro"] == farm]
            if df_f.empty:
                continue
            pair = list(zip(df_f["pi"].tolist(), df_f["pf"].tolist()))
            in_pair = pd.Series(pair).isin(wanted).values

            strict = _sum_rate(df_f.loc[in_pair])

            df_wrong = df_f.loc[~in_pair].copy()
            df_wrong = df_wrong[~(df_wrong["pi"].isna() | df_wrong["pf"].isna())]
            leakage = _sum_rate(df_wrong[df_wrong["dm"].apply(lambda d: _date_in_any_range(d, wanted))])

            df_missing_period = df_f.loc[~in_pair].copy()
            df_missing_period = df_missing_period[df_missing_period["pi"].isna() | df_missing_period["pf"].isna()]
            fallback = _sum_rate(df_missing_period[df_missing_period["dm"].apply(lambda d: _date_in_any_range(d, wanted))])

            if leakage > 0.0001:
                leak_total += leakage
                leak_farms += 1

            all_rows.append(
                {
                    "pay_day": pay_day,
                    "farmacia": farm,
                    "cycles_window_start": str(min_pi),
                    "cycles_window_end": str(max_pf),
                    "ciclos_qtd": len(wanted),
                    "diarias_strict": strict,
                    "diarias_fallback_sem_periodo": fallback,
                    "diarias_leakage_potencial": leakage,
                    "diarias_total_janela": _sum_rate(df_f),
                }
            )

        day_summary.append(
            {
                "pay_day": pay_day,
                "farms": len(ciclos_por_farm),
                "cycles_window_start": str(min_pi),
                "cycles_window_end": str(max_pf),
                "missing_cycle_rows": missing_cycle_rows,
                "pay_nao_especificado_rows": int((df_pay["centro_norm"] == "NAO ESPECIFICADO").sum()),
                "pay_dup_keys": pay_dup_keys,
                "pay_dup_extra_rows": pay_dup_rows,
                "diaria_dup_keys": diaria_dup_keys,
                "diaria_dup_extra_rows": diaria_dup_rows,
                "diaria_leak_total": float(leak_total),
                "diaria_leak_farms": int(leak_farms),
            }
        )

    df_all = pd.DataFrame(all_rows)
    df_sum = pd.DataFrame(day_summary).sort_values("pay_day")

    if not df_all.empty:
        df_top = df_all.sort_values(["diarias_leakage_potencial", "diarias_total_janela"], ascending=False).head(top)
    else:
        df_top = pd.DataFrame()

    return df_sum, df_top, df_all


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="date_from", required=True, help="YYYY-MM-DD")
    ap.add_argument("--to", dest="date_to", required=True, help="YYYY-MM-DD")
    ap.add_argument("--top", type=int, default=30, help="Top suspeitas no consolidado")
    ap.add_argument("--out", default=None, help="Caminho do .xlsx de saída (default em logs/)")
    args = ap.parse_args()

    df_sum, df_top, df_all = audit_range(args.date_from, args.date_to, top=args.top)

    out = args.out
    if not out:
        os.makedirs("logs", exist_ok=True)
        out = os.path.join("logs", f"audit_coop_conciliacao_{args.date_from}_to_{args.date_to}.xlsx")

    with pd.ExcelWriter(out, engine="openpyxl") as xw:
        df_sum.to_excel(xw, index=False, sheet_name="Resumo_Por_Rodada")
        df_top.to_excel(xw, index=False, sheet_name="Top_Suspeitas")
        df_all.to_excel(xw, index=False, sheet_name="Todos")

    print(f"OK: auditoria salva em {out}")
    if not df_top.empty:
        print("\nTOP SUSPEITAS (leakage potencial):")
        print(df_top.head(20).to_string(index=False))


if __name__ == "__main__":
    main()

import argparse
import os
import sys
from datetime import date

import pandas as pd

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from backend.audit_coop_conciliacao_batch import audit_range  # noqa: E402


def month_bounds(year: int, month: int) -> tuple[date, date]:
    start = date(year, month, 1)
    if month == 12:
        end = date(year, 12, 31)
    else:
        end = date(year, month + 1, 1) - pd.Timedelta(days=1)
        end = end.date() if hasattr(end, "date") else end
    return start, end


def run_year(year: int, top: int = 200) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    all_sum = []
    all_top_candidates = []
    all_rows = []

    for m in range(1, 13):
        start, end = month_bounds(year, m)
        print(f"[{year}-{m:02d}] auditando {start}..{end} ...")
        try:
            df_sum, _df_top, df_all = audit_range(start.isoformat(), end.isoformat(), top=top)
        except SystemExit as e:
            # Mês sem rodadas / sem dados
            print(f"  skip: {e}")
            continue

        if not df_sum.empty:
            df_sum = df_sum.copy()
            df_sum["mes"] = f"{year}-{m:02d}"
            all_sum.append(df_sum)

        if df_all is not None and not df_all.empty:
            df_all = df_all.copy()
            df_all["mes"] = f"{year}-{m:02d}"
            all_rows.append(df_all)
            all_top_candidates.append(df_all)

    df_sum_y = pd.concat(all_sum, ignore_index=True) if all_sum else pd.DataFrame()
    df_all_y = pd.concat(all_rows, ignore_index=True) if all_rows else pd.DataFrame()

    if all_top_candidates:
        df_top_base = pd.concat(all_top_candidates, ignore_index=True)
        df_top_y = df_top_base.sort_values(
            ["diarias_leakage_potencial", "diarias_total_janela"],
            ascending=False,
        ).head(top)
    else:
        df_top_y = pd.DataFrame()

    return df_sum_y, df_top_y, df_all_y


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=2025)
    ap.add_argument("--top", type=int, default=200)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    df_sum, df_top, df_all = run_year(args.year, top=args.top)

    os.makedirs("logs", exist_ok=True)
    out = args.out or os.path.join("logs", f"audit_coop_conciliacao_{args.year}.xlsx")

    with pd.ExcelWriter(out, engine="openpyxl") as xw:
        df_sum.to_excel(xw, index=False, sheet_name="Resumo_Por_Rodada")
        df_top.to_excel(xw, index=False, sheet_name="Top_Suspeitas")
        df_all.to_excel(xw, index=False, sheet_name="Todos")

    print(f"OK: auditoria anual salva em {out}")


if __name__ == "__main__":
    main()


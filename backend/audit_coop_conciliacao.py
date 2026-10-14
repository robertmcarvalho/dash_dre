import argparse
import os
import sys
from collections import defaultdict
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


def audit_pay_day(pay_day: str, farmacia: str | None = None, top: int = 20):
    pd_day = _to_date(pay_day)
    if not pd_day:
        raise SystemExit(f"pay_day inválido: {pay_day!r}")

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
        print(f"Nenhum pagamento RATEIO encontrado para {pay_day}.")
        return

    df_pay["centro"] = df_pay["centro_custo_final"].fillna("Não Especificado")
    df_pay = df_pay[df_pay["centro"].apply(norm) != "NAO ESPECIFICADO"]
    if farmacia:
        df_pay = df_pay[df_pay["centro"] == farmacia]

    # ciclos por farmácia
    ciclos_por_farm: dict[str, set[tuple[date, date]]] = defaultdict(set)
    for _, r in df_pay.iterrows():
        pi = _to_date(r.get("periodo_inicio"))
        pf = _to_date(r.get("periodo_fim"))
        if not pi or not pf:
            continue
        if pi > pf:
            pi, pf = pf, pi
        ciclos_por_farm[str(r.get("centro"))].add((pi, pf))

    if not ciclos_por_farm:
        print(f"Nenhum ciclo (pi/pf) encontrado para {pay_day} (provavelmente período ausente no Excel).")
        return

    all_wanted = set()
    min_pi = None
    max_pf = None
    for wanted in ciclos_por_farm.values():
        all_wanted |= wanted
        lo = min(a for a, _ in wanted)
        hi = max(b for _, b in wanted)
        min_pi = lo if min_pi is None else min(min_pi, lo)
        max_pf = hi if max_pf is None else max(max_pf, hi)

    # Diárias: buscamos por período apurado (periodo_fim) e também as sem período (fallback por data_mov)
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
    diaria_rows_sem_periodo = _fetch_all_filtered(
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
    diaria_rows_sem_periodo2 = _fetch_all_filtered(
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

    df_diaria = pd.DataFrame((diaria_rows_periodo or []) + (diaria_rows_sem_periodo or []) + (diaria_rows_sem_periodo2 or []))
    if df_diaria.empty:
        print("Nenhuma diária encontrada na janela dos ciclos.")
        return

    df_diaria["centro"] = df_diaria["centro_custo_final"].fillna("Não Especificado")
    df_diaria = df_diaria[df_diaria["centro"].apply(norm) != "NAO ESPECIFICADO"]
    if farmacia:
        df_diaria = df_diaria[df_diaria["centro"] == farmacia]
    df_diaria["valor_rateado"] = pd.to_numeric(df_diaria["valor_rateado"], errors="coerce").fillna(0.0)
    df_diaria["pi"] = df_diaria["periodo_inicio"].apply(_to_date)
    df_diaria["pf"] = df_diaria["periodo_fim"].apply(_to_date)
    df_diaria["dm"] = df_diaria["data_mov"].apply(_to_date)

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

    def sum_rate(df: pd.DataFrame) -> float:
        if df is None or df.empty:
            return 0.0
        s = pd.to_numeric(df.get("valor_rateado"), errors="coerce").fillna(0.0)
        return float(s.sum())

    rows_out = []
    for farm, wanted in ciclos_por_farm.items():
        df_f = df_diaria[df_diaria["centro"] == farm]
        if df_f.empty:
            continue
        pair = list(zip(df_f["pi"].tolist(), df_f["pf"].tolist()))
        in_pair = pd.Series(pair).isin(wanted).values

        strict = sum_rate(df_f.loc[in_pair])

        # fallback: só quando não há pi/pf
        df_missing = df_f.loc[~in_pair].copy()
        df_missing = df_missing[df_missing["pi"].isna() | df_missing["pf"].isna()]
        fallback = sum_rate(df_missing[df_missing["dm"].apply(lambda d: _date_in_any_range(d, wanted))])

        # “leakage”: linhas que NÃO pertencem ao ciclo (pi/pf diferente), mas data_mov cairia dentro do ciclo
        df_wrong = df_f.loc[~in_pair].copy()
        df_wrong = df_wrong[~(df_wrong["pi"].isna() | df_wrong["pf"].isna())]
        leakage = sum_rate(df_wrong[df_wrong["dm"].apply(lambda d: _date_in_any_range(d, wanted))])

        rows_out.append(
            {
                "farmacia": farm,
                "ciclos": len(wanted),
                "diarias_strict": strict,
                "diarias_fallback_sem_periodo": fallback,
                "diarias_leakage_potencial": leakage,
                "diarias_total_janela": sum_rate(df_f),
            }
        )

    out = pd.DataFrame(rows_out)
    if out.empty:
        print("Nenhuma farmácia com diárias na janela.")
        return

    out = out.sort_values(["diarias_leakage_potencial", "diarias_total_janela"], ascending=False)

    print(f"\nAUDITORIA DIÁRIAS — pay_day={pay_day}  (ciclos: {min_pi}..{max_pf})")
    print(f"Farmácias auditadas: {out['farmacia'].nunique()}")
    print("\nTop possíveis vazamentos (diárias de outro ciclo que cairiam no ciclo por data_mov):")
    print(out.head(top).to_string(index=False))

    if farmacia:
        print("\nDetalhe das diárias (janela) para a farmácia filtrada:")
        df_det = df_diaria[df_diaria["centro"] == farmacia].copy()
        df_det = df_det.sort_values(["pi", "pf", "dm", "valor_rateado"], ascending=True)
        cols = [
            "centro",
            "valor_rateado",
            "pi",
            "pf",
            "dm",
            "numero_docto",
            "registro_movimento",
            "registro_compromisso",
            "referente",
        ]
        print(df_det[cols].head(200).to_string(index=False))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pay-day", required=True, help="Data da rodada de pagamento (YYYY-MM-DD)")
    ap.add_argument("--farmacia", default=None, help="Filtrar por centro_custo_final (exato)")
    ap.add_argument("--top", type=int, default=20, help="Quantidade de linhas no ranking do vazamento potencial")
    args = ap.parse_args()

    audit_pay_day(args.pay_day, farmacia=args.farmacia, top=args.top)


if __name__ == "__main__":
    main()

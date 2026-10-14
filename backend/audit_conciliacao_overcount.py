import argparse
import os
import sys
from dataclasses import dataclass
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


@dataclass(frozen=True)
class PayDayWorkset:
    pay_day: date
    cycles_by_farm: dict[str, set[tuple[date, date]]]
    all_ranges: set[tuple[date, date]]
    min_pi: date
    max_pf: date


def _build_workset(df_pay_rateio: pd.DataFrame, pay_day: date) -> PayDayWorkset | None:
    if df_pay_rateio is None or df_pay_rateio.empty:
        return None

    df = df_pay_rateio.copy()
    df["centro"] = df["centro_custo_final"].fillna("Não Especificado")
    df = df[df["centro"].apply(norm) != "NAO ESPECIFICADO"]
    if df.empty:
        return None

    df["pi"] = df["periodo_inicio"].apply(_to_date)
    df["pf"] = df["periodo_fim"].apply(_to_date)
    df = df.dropna(subset=["pi", "pf"])
    if df.empty:
        return None

    # excluir ciclos futuros (mesma regra do backend report)
    mask_future = (df["pi"] > pay_day) | (df["pf"] > pay_day)
    df = df[~mask_future].copy()
    if df.empty:
        return None

    cycles_by_farm = {c: set(zip(g["pi"], g["pf"])) for c, g in df.groupby("centro")}
    all_ranges: set[tuple[date, date]] = set()
    for s in cycles_by_farm.values():
        all_ranges |= s
    if not all_ranges:
        return None

    min_pi = min(a for a, _ in all_ranges)
    max_pf = max(b for _, b in all_ranges)

    return PayDayWorkset(
        pay_day=pay_day,
        cycles_by_farm=cycles_by_farm,
        all_ranges=all_ranges,
        min_pi=min_pi,
        max_pf=max_pf,
    )


def audit_year(year: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    date_from = date(year, 1, 1).isoformat()
    date_to = date(year, 12, 31).isoformat()

    # Rodadas (pay_day) pela mesma regra do app: MOV de pagamento de entregadores
    mov_rows = _fetch_all_filtered(
        supabase,
        "coop_conciliacao_entregas",
        "data_mov",
        lambda q: q.eq("plano_contas", "PAGAMENTO DE ENTREGADORES")
        .eq("linha_tipo", "MOV")
        .not_.is_("data_mov", "null")
        .gte("data_mov", date_from)
        .lte("data_mov", date_to)
        .order("data_mov", desc=False),
        chunk=1000,
        max_rows=400000,
    )
    df_mov = pd.DataFrame(mov_rows)
    if df_mov.empty:
        return pd.DataFrame(), pd.DataFrame()
    df_mov["pay_day"] = pd.to_datetime(df_mov["data_mov"], errors="coerce").dt.date
    df_mov = df_mov.dropna(subset=["pay_day"])
    pay_days = sorted(set(df_mov["pay_day"].tolist()))

    # Prefetch: pagamentos rateio (todos), receitas coop rateio (todos), diárias rateio (todos) e flux entregas (todos)
    pay_rateio_rows = _fetch_all_filtered(
        supabase,
        "coop_conciliacao_entregas",
        "data_mov,centro_custo_final,periodo_inicio,periodo_fim,valor_rateado",
        lambda q: q.eq("plano_contas", "PAGAMENTO DE ENTREGADORES")
        .eq("linha_tipo", "RATEIO")
        .not_.is_("data_mov", "null")
        .gte("data_mov", date_from)
        .lte("data_mov", date_to),
        chunk=1000,
        max_rows=800000,
    )
    df_pay_rateio = pd.DataFrame(pay_rateio_rows)
    if not df_pay_rateio.empty:
        df_pay_rateio["pay_day"] = pd.to_datetime(df_pay_rateio["data_mov"], errors="coerce").dt.date
        df_pay_rateio = df_pay_rateio.dropna(subset=["pay_day"])

    rec_rows = _fetch_all_filtered(
        supabase,
        "coop_conciliacao_entregas",
        "centro_custo_final,periodo_inicio,periodo_fim,valor_rateado",
        lambda q: q.eq("plano_contas", "RECEITAS COM ENTREGAS")
        .eq("linha_tipo", "RATEIO")
        .not_.is_("periodo_inicio", "null")
        .not_.is_("periodo_fim", "null")
        .gte("periodo_fim", date_from)
        .lte("periodo_fim", date_to),
        chunk=1000,
        max_rows=800000,
    )
    df_rec = pd.DataFrame(rec_rows)
    if not df_rec.empty:
        df_rec["centro"] = df_rec["centro_custo_final"].fillna("Não Especificado")
        df_rec = df_rec[df_rec["centro"].apply(norm) != "NAO ESPECIFICADO"]
        df_rec["pi"] = df_rec["periodo_inicio"].apply(_to_date)
        df_rec["pf"] = df_rec["periodo_fim"].apply(_to_date)
        df_rec = df_rec.dropna(subset=["pi", "pf"])
        df_rec["key"] = list(zip(df_rec["pi"], df_rec["pf"]))
        df_rec["valor_rateado"] = pd.to_numeric(df_rec["valor_rateado"], errors="coerce").fillna(0.0)

    diaria_rows = _fetch_all_filtered(
        supabase,
        "coop_conciliacao_entregas",
        "centro_custo_final,periodo_inicio,periodo_fim,data_mov,valor_rateado,registro_movimento,registro_compromisso,numero_docto,entrada_saida",
        lambda q: q.eq("plano_contas", "DIARIA DE MOTOBOY").eq("linha_tipo", "RATEIO"),
        chunk=1000,
        max_rows=800000,
    )
    df_diaria = pd.DataFrame(diaria_rows)
    if not df_diaria.empty:
        # filtra SAIDA (sem depender do encoding no servidor)
        df_diaria = df_diaria[df_diaria.get("entrada_saida").apply(norm) == "SAIDA"]
        df_diaria["centro"] = df_diaria["centro_custo_final"].fillna("Não Especificado")
        df_diaria = df_diaria[df_diaria["centro"].apply(norm) != "NAO ESPECIFICADO"]
        df_diaria["valor_rateado"] = pd.to_numeric(df_diaria["valor_rateado"], errors="coerce").fillna(0.0)
        df_diaria["pi"] = df_diaria["periodo_inicio"].apply(_to_date)
        df_diaria["pf"] = df_diaria["periodo_fim"].apply(_to_date)
        df_diaria["dm"] = df_diaria["data_mov"].apply(_to_date)
        df_diaria["key"] = list(zip(df_diaria["pi"], df_diaria["pf"]))
        # dedup defensivo (mesma chave do backend)
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

    flux_rows = _fetch_all_filtered(
        supabase,
        "dre_financeiro",
        "data_mov,centro_custo,valor",
        lambda q: q.eq("entrada_saida", "ENTRADA")
        .eq("plano_contas", "RECEITAS COM ENTREGAS")
        .not_.is_("data_mov", "null")
        .gte("data_mov", date_from)
        .lte("data_mov", date_to),
        chunk=1000,
        max_rows=800000,
    )
    df_flux = pd.DataFrame(flux_rows)
    if not df_flux.empty:
        df_flux["dm"] = df_flux["data_mov"].apply(_to_date)
        df_flux["centro_norm"] = df_flux.get("centro_custo").fillna("Não Especificado").apply(norm)
        df_flux = df_flux[df_flux["centro_norm"] != "NAO ESPECIFICADO"]
        df_flux["valor"] = pd.to_numeric(df_flux["valor"], errors="coerce").fillna(0.0)

    divergencias = []
    resumo = []

    for pd_day in pay_days:
        df_pay_d = df_pay_rateio[df_pay_rateio["pay_day"] == pd_day] if not df_pay_rateio.empty else pd.DataFrame()
        ws = _build_workset(df_pay_d, pd_day)
        if not ws:
            continue

        # dataset janela de ciclos (somente ranges válidos)
        rec_win = df_rec[(df_rec["pf"] >= ws.min_pi) & (df_rec["pf"] <= ws.max_pf)] if not df_rec.empty else pd.DataFrame()
        if not rec_win.empty:
            rec_win = rec_win[rec_win["key"].isin(ws.all_ranges)]
        rec_current = (rec_win.groupby("centro")["valor_rateado"].sum().to_dict() if not rec_win.empty else {})

        rec_correct = {}
        for farm, cy in ws.cycles_by_farm.items():
            if rec_win.empty:
                rec_correct[farm] = 0.0
            else:
                rec_correct[farm] = float(rec_win[(rec_win["centro"] == farm) & (rec_win["key"].isin(cy))]["valor_rateado"].sum())

        dia_win = df_diaria
        if not df_diaria.empty:
            # janela ampla pelo periodo_fim quando existir; mantém dm para fallback
            dia_win = df_diaria[
                ((df_diaria["pf"].isna()) | ((df_diaria["pf"] >= ws.min_pi) & (df_diaria["pf"] <= ws.max_pf)))
            ].copy()

        # diárias atuais (bug): global keys + fallback dm em global
        dia_current = {}
        if dia_win is not None and not dia_win.empty:
            dia_pair = dia_win[~(dia_win["pi"].isna() | dia_win["pf"].isna())].copy()
            if not dia_pair.empty:
                dia_pair = dia_pair[dia_pair["key"].isin(ws.all_ranges)]
                dia_current = dia_pair.groupby("centro")["valor_rateado"].sum().to_dict()
            dia_missing = dia_win[dia_win["pi"].isna() | dia_win["pf"].isna()].copy()
            if not dia_missing.empty and ws.all_ranges:
                dia_missing = dia_missing[dia_missing["dm"].apply(lambda d: _date_in_any_range(d, ws.all_ranges))]
                if not dia_missing.empty:
                    for centro, val in dia_missing.groupby("centro")["valor_rateado"].sum().to_dict().items():
                        dia_current[centro] = float(dia_current.get(centro, 0.0) or 0.0) + float(val or 0.0)

        # diárias corretas: por farm (keys do farm + fallback dm no farm)
        dia_correct = {}
        if dia_win is not None and not dia_win.empty:
            dia_pair_all = dia_win[~(dia_win["pi"].isna() | dia_win["pf"].isna())].copy()
            dia_missing_all = dia_win[dia_win["pi"].isna() | dia_win["pf"].isna()].copy()
            for farm, cy in ws.cycles_by_farm.items():
                v = 0.0
                if not dia_pair_all.empty:
                    v += float(dia_pair_all[(dia_pair_all["centro"] == farm) & (dia_pair_all["key"].isin(cy))]["valor_rateado"].sum())
                if not dia_missing_all.empty:
                    v += float(
                        dia_missing_all[
                            (dia_missing_all["centro"] == farm)
                            & (dia_missing_all["dm"].apply(lambda d: _date_in_any_range(d, cy)))
                        ]["valor_rateado"].sum()
                    )
                dia_correct[farm] = v
        else:
            for farm in ws.cycles_by_farm.keys():
                dia_correct[farm] = 0.0

        flux_win = df_flux[(df_flux["dm"] >= ws.min_pi) & (df_flux["dm"] <= ws.max_pf)] if not df_flux.empty else pd.DataFrame()
        flux_current = {}
        if flux_win is not None and not flux_win.empty:
            flux_win = flux_win[flux_win["dm"].apply(lambda d: _date_in_any_range(d, ws.all_ranges))]
            flux_current = flux_win.groupby("centro_norm")["valor"].sum().to_dict()

        flux_correct = {}
        if flux_win is not None and not flux_win.empty:
            for farm, cy in ws.cycles_by_farm.items():
                farm_norm = norm(farm)
                flux_correct[farm] = float(
                    flux_win[(flux_win["centro_norm"] == farm_norm) & (flux_win["dm"].apply(lambda d: _date_in_any_range(d, cy)))]["valor"].sum()
                )
        else:
            for farm in ws.cycles_by_farm.keys():
                flux_correct[farm] = 0.0

        # divergências por farm
        farms = sorted(set(ws.cycles_by_farm.keys()) | set(rec_current.keys()) | set(dia_current.keys()))
        d_rec_total = 0.0
        d_dia_total = 0.0
        d_flux_total = 0.0
        for farm in farms:
            rec_c = float(rec_current.get(farm, 0.0) or 0.0)
            rec_ok = float(rec_correct.get(farm, 0.0) or 0.0)
            d_rec = rec_c - rec_ok

            dia_c = float(dia_current.get(farm, 0.0) or 0.0)
            dia_ok = float(dia_correct.get(farm, 0.0) or 0.0)
            d_dia = dia_c - dia_ok

            flux_c = float(flux_current.get(norm(farm), 0.0) or 0.0)
            flux_ok = float(flux_correct.get(farm, 0.0) or 0.0)
            d_flux = flux_c - flux_ok

            if abs(d_rec) < 0.005 and abs(d_dia) < 0.005 and abs(d_flux) < 0.005:
                continue

            divergencias.append(
                {
                    "pay_day": pd_day.isoformat(),
                    "farmacia": farm,
                    "ciclos_validos": len(ws.cycles_by_farm.get(farm, set())),
                    "receita_coop_atual": rec_c,
                    "receita_coop_correta": rec_ok,
                    "diff_receita_coop": d_rec,
                    "diarias_atual": dia_c,
                    "diarias_corretas": dia_ok,
                    "diff_diarias": d_dia,
                    "receita_flux_atual": flux_c,
                    "receita_flux_correta": flux_ok,
                    "diff_receita_flux": d_flux,
                }
            )

            d_rec_total += d_rec
            d_dia_total += d_dia
            d_flux_total += d_flux

        resumo.append(
            {
                "pay_day": pd_day.isoformat(),
                "farms": len(ws.cycles_by_farm),
                "valid_cycles_distintos": len(ws.all_ranges),
                "window_start": ws.min_pi.isoformat(),
                "window_end": ws.max_pf.isoformat(),
                "total_diff_receita_coop": d_rec_total,
                "total_diff_diarias": d_dia_total,
                "total_diff_receita_flux": d_flux_total,
            }
        )

    df_div = pd.DataFrame(divergencias).sort_values(["pay_day", "diff_receita_coop"], ascending=[True, False]) if divergencias else pd.DataFrame()
    df_sum = pd.DataFrame(resumo).sort_values("pay_day") if resumo else pd.DataFrame()
    return df_sum, df_div


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=2026)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    df_sum, df_div = audit_year(args.year)

    os.makedirs("logs", exist_ok=True)
    out = args.out or os.path.join("logs", f"audit_conciliacao_overcount_{args.year}.xlsx")
    with pd.ExcelWriter(out, engine="openpyxl") as xw:
        df_sum.to_excel(xw, index=False, sheet_name="Resumo_Por_Rodada")
        df_div.to_excel(xw, index=False, sheet_name="Divergencias")

    print(f"OK: auditoria salva em {out}")
    if not df_div.empty:
        print("\nTop divergências (por receita_coop):")
        print(df_div.sort_values('diff_receita_coop', ascending=False).head(25).to_string(index=False))


if __name__ == "__main__":
    main()


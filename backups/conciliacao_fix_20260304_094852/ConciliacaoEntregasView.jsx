import { useEffect, useMemo, useState } from "react";
import apiClient from "./apiClient";
import { formatCurrency } from "./utils/helpers";

function recommendedCashWeekStartForPayDay(payDayIso) {
  if (!payDayIso) return "";
  const d = new Date(`${payDayIso}T00:00:00`);
  if (Number.isNaN(d.getTime())) return "";
  const day = d.getDay(); // 0=Dom ... 6=Sab
  const mondayThisWeek = new Date(d);
  const diffToMonday = (day + 6) % 7;
  mondayThisWeek.setDate(d.getDate() - diffToMonday);
  return mondayThisWeek.toISOString().slice(0, 10);
}

function formatWeekLabel(weekStart, weekEnd) {
  if (!weekStart || !weekEnd) return "—";
  const [ys, ms, ds] = weekStart.split("-");
  const [ye, me, de] = weekEnd.split("-");
  return `${ds}/${ms}/${ys} a ${de}/${me}/${ye}`;
}

function formatDateLabel(isoDate) {
  if (!isoDate) return "—";
  const [y, m, d] = isoDate.split("-");
  return `${d}/${m}/${y}`;
}

function formatMonthLabel(ym) {
  if (!ym) return "—";
  const d = new Date(`${ym}-01T00:00:00`);
  if (Number.isNaN(d.getTime())) return ym;
  const label = new Intl.DateTimeFormat("pt-BR", { month: "long", year: "numeric" }).format(d);
  return label.charAt(0).toUpperCase() + label.slice(1);
}

export default function ConciliacaoEntregasView() {
  const [payMonths, setPayMonths] = useState([]);
  const [payMonth, setPayMonth] = useState("");
  const [payDays, setPayDays] = useState([]);
  const [payDay, setPayDay] = useState("");
  const [mode, setMode] = useState("ciclo"); // "ciclo" | "caixa"
  const [onlyDivergent, setOnlyDivergent] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [showNoCyclePayments, setShowNoCyclePayments] = useState(false);
  const [data, setData] = useState(null);

  const payDayOptions = useMemo(() => {
    const unique = new Map();
    for (const d of payDays || []) {
      if (!d?.pay_day) continue;
      unique.set(d.pay_day, d);
    }
    return Array.from(unique.values()).sort((a, b) =>
      (a.pay_day || "").localeCompare(b.pay_day || "")
    );
  }, [payDays]);

  const payMonthOptions = useMemo(() => {
    const unique = new Map();
    for (const m of payMonths || []) {
      if (!m?.month) continue;
      unique.set(m.month, m);
    }
    return Array.from(unique.values()).sort((a, b) => (a.month || "").localeCompare(b.month || ""));
  }, [payMonths]);

  useEffect(() => {
    let mounted = true;
    (async () => {
      setError("");
      try {
        const pm = await apiClient.get("/api/coop/conciliacao_entregas/pay_months");
        if (!mounted) return;
        const pms = pm?.data || [];
        setPayMonths(pms);

        const lastPm = pms
          ?.slice()
          ?.sort((a, b) => (a.month || "").localeCompare(b.month || ""))
          .at(-1);
        if (lastPm?.month) setPayMonth(lastPm.month);
      } catch (e) {
        if (!mounted) return;
        setError(e?.response?.data?.detail || e?.message || "Erro ao carregar rodadas.");
      }
    })();
    return () => {
      mounted = false;
    };
  }, []);

  useEffect(() => {
    if (!payMonth) return;
    let mounted = true;
    (async () => {
      setLoading(true);
      setError("");
      try {
        const pd = await apiClient.get("/api/coop/conciliacao_entregas/pay_days", {
          params: { month: payMonth },
        });
        if (!mounted) return;
        const pds = pd?.data || [];
        setPayDays(pds);

        const lastPd = pds
          ?.slice()
          ?.sort((a, b) => (a.pay_day || "").localeCompare(b.pay_day || ""))
          .at(-1);
        setPayDay(lastPd?.pay_day || "");
      } catch (e) {
        if (!mounted) return;
        setError(e?.response?.data?.detail || e?.message || "Erro ao carregar rodadas do mês.");
        setPayDays([]);
        setPayDay("");
      } finally {
        if (mounted) setLoading(false);
      }
    })();
    return () => {
      mounted = false;
    };
  }, [payMonth]);

  useEffect(() => {
    if (!payDay) return;
    let mounted = true;
    (async () => {
      setLoading(true);
      setError("");
      try {
        const cashWeekStart = recommendedCashWeekStartForPayDay(payDay);
        const resp = await apiClient.get("/api/coop/conciliacao_entregas/report", {
          params: {
            cash_week_start: cashWeekStart,
            pay_day: payDay,
            mode,
            only_divergent: onlyDivergent,
          },
        });
        if (!mounted) return;
        setData(resp.data);
      } catch (e) {
        if (!mounted) return;
        setError(e?.response?.data?.detail || e?.message || "Erro ao buscar conciliação.");
        setData(null);
      } finally {
        if (mounted) setLoading(false);
      }
    })();
    return () => {
      mounted = false;
    };
  }, [payDay, mode, onlyDivergent]);

  const computedCashWeekLabel = useMemo(() => {
    const ws = recommendedCashWeekStartForPayDay(payDay);
    if (!ws) return "—";
    const start = new Date(`${ws}T00:00:00`);
    if (Number.isNaN(start.getTime())) return "—";
    const end = new Date(start);
    end.setDate(start.getDate() + 6);
    const weekEnd = end.toISOString().slice(0, 10);
    return formatWeekLabel(ws, weekEnd);
  }, [payDay]);

  const totals = useMemo(() => {
    const receitaCiclos = data?.totals?.receita_ciclos_apurados || 0;
    const receitaCaixa = data?.totals?.receita_semana_caixa || 0;
    const pago = data?.totals?.pagamento_quinta || 0;
    const diarias = data?.totals?.diarias_ciclo || 0;
    const custoBruto = data?.totals?.custo_bruto ?? (pago + diarias);
    const descontoAdiant = data?.totals?.desconto_adiantamento_rodada || 0;
    const pagamentoLiquido = data?.totals?.pagamento_liquido ?? (custoBruto - descontoAdiant);

    const saldoCiclo = data?.totals?.saldo ?? (receitaCiclos - custoBruto);
    const saldoCaixa = data?.totals?.saldo_caixa ?? (receitaCaixa - custoBruto);
    const saldoCicloLiq = data?.totals?.saldo_liquido ?? (receitaCiclos - pagamentoLiquido);
    const saldoCaixaLiq = data?.totals?.saldo_caixa_liquido ?? (receitaCaixa - pagamentoLiquido);

    const adiantado = data?.totals?.adiantado_ciclo || 0;
    const reembolsado = data?.totals?.reembolsado_ciclo || 0;
    const adiantLiq = data?.totals?.adiantamento_liquido_ciclo ?? (adiantado - reembolsado);

    const receitaBase = mode === "caixa" ? receitaCaixa : receitaCiclos;
    const saldoBaseBruto = mode === "caixa" ? saldoCaixa : saldoCiclo;
    const saldoBaseLiquido = mode === "caixa" ? saldoCaixaLiq : saldoCicloLiq;

    return {
      receitaCiclos,
      receitaCaixa,
      receitaBase,
      pago,
      diarias,
      custoBruto,
      descontoAdiant,
      pagamentoLiquido,
      saldoCiclo,
      saldoCaixa,
      saldoCicloLiq,
      saldoCaixaLiq,
      saldoBaseBruto,
      saldoBaseLiquido,
      adiantado,
      reembolsado,
      adiantLiq,
    };
  }, [data, mode]);

  const repasseBadgeClass = (pct) => {
    if (pct == null || !Number.isFinite(pct)) return "bg-gray-100 text-gray-600";
    const value = pct;
    if (value <= 70) return "bg-green-100 text-green-700";
    if (value <= 80) return "bg-yellow-100 text-yellow-800";
    return "bg-red-100 text-red-700";
  };

  const formatCyclesCompact = (cycles) => {
    const ranges = Array.from(
      new Set(
        (cycles || [])
          .map((c) => {
            const pi = (c?.periodo_inicio || "").slice(0, 10);
            const pf = (c?.periodo_fim || "").slice(0, 10);
            if (!pi || !pf) return null;
            return `${formatDateLabel(pi)} a ${formatDateLabel(pf)}`;
          })
          .filter(Boolean)
      )
    );
    if (!ranges.length) return { text: "—", title: "" };
    const shown = ranges.slice(0, 2).join(" • ");
    const extra = ranges.length > 2 ? ` +${ranges.length - 2}` : "";
    return { text: `${shown}${extra}`, title: ranges.join(" • ") };
  };

  const rows = useMemo(() => data?.rows || [], [data]);
  const receitaBaseForRow = (r) =>
    mode === "caixa" ? (r?.receita_semana_caixa || 0) : (r?.receita_ciclos_apurados || 0);
  const pctBaseForRow = (r) => (mode === "caixa" ? r?.pct_repasse_caixa : r?.pct_repasse);
  const saldoBaseForRow = (r) => (mode === "caixa" ? (r?.saldo_caixa || 0) : (r?.saldo || 0));
  const noCyclePaymentRows = useMemo(
    () =>
      rows.filter((r) => (r?.custo_bruto || 0) > 0 && receitaBaseForRow(r) <= 0),
    [rows, mode]
  );
  const mainRows = useMemo(
    () =>
      rows.filter((r) => !((r?.custo_bruto || 0) > 0 && receitaBaseForRow(r) <= 0)),
    [rows, mode]
  );
  const mainRowsFiltered = useMemo(
    () => (mainRows || []).filter((r) => (r?.custo_bruto || 0) > 0),
    [mainRows]
  );
  const noCyclePaymentTotal = useMemo(
    () => noCyclePaymentRows.reduce((acc, r) => acc + (Number(r?.custo_bruto) || 0), 0),
    [noCyclePaymentRows]
  );

  const handleExportXlsx = async (farmacia) => {
    try {
      setLoading(true);
      setError("");
      const cashWeekStart = recommendedCashWeekStartForPayDay(payDay);
      const resp = await apiClient.get("/api/coop/conciliacao_entregas/export_xlsx", {
        params: {
          cash_week_start: cashWeekStart,
          pay_day: payDay,
          mode,
          farmacia,
        },
        responseType: "blob",
      });
      const blob = new Blob([resp.data], {
        type:
          resp?.headers?.["content-type"] ||
          "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      });
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      const safeName = String(farmacia || "todas")
        .replaceAll(" ", "_")
        .replaceAll("/", "_")
        .replaceAll("\\\\", "_");
      a.href = url;
      a.download = `auditoria_${safeName}_${payDay}.xlsx`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
    } catch (e) {
      setError(e?.response?.data?.detail || e?.message || "Erro ao exportar Excel.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="p-6">
      <div className="flex flex-col gap-4">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-semibold">Conferência semanal de receita e pagamento de entregadores</h1>
          </div>
        </div>

        <div className="bg-white rounded-xl p-4 border border-gray-100 flex flex-wrap items-center gap-4">
          <div className="flex items-center gap-2">
            <span className="text-sm text-gray-600">Mês:</span>
            <select
              className="border rounded-lg px-3 py-2 text-sm"
              value={payMonth}
              onChange={(e) => setPayMonth(e.target.value)}
            >
              {payMonthOptions.map((m) => (
                <option key={m.month} value={m.month}>
                  {formatMonthLabel(m.month)}
                </option>
              ))}
            </select>
          </div>

          <div className="flex items-center gap-2">
            <span className="text-sm text-gray-600">Rodada (pagamento):</span>
            <select className="border rounded-lg px-3 py-2 text-sm" value={payDay} onChange={(e) => setPayDay(e.target.value)}>
              {payDayOptions.map((d) => (
                <option key={d.pay_day} value={d.pay_day}>
                  {formatDateLabel(d.pay_day)}
                </option>
              ))}
            </select>
          </div>

          <div className="flex items-center gap-2">
            <span className="text-sm text-gray-600">Modo:</span>
            <select className="border rounded-lg px-3 py-2 text-sm" value={mode} onChange={(e) => setMode(e.target.value)}>
              <option value="ciclo">Ciclo (apuração)</option>
              <option value="caixa">Caixa (semana)</option>
            </select>
          </div>

          <label className="flex items-center gap-2 text-sm text-gray-700">
            <input
              type="checkbox"
              checked={onlyDivergent}
              onChange={(e) => setOnlyDivergent(e.target.checked)}
            />
            Mostrar apenas divergências
          </label>

          <div className="ml-auto text-sm text-gray-500">
            Semana (Seg–Dom): {computedCashWeekLabel}
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="bg-blue-50 border border-blue-100 rounded-xl p-4 text-sm text-blue-900">
            <div className="font-semibold mb-1">Como ler a Receita</div>
            <div>
              <span className="font-semibold">Modo Ciclo</span>: Receita = soma das entradas de entregas (rateio) dentro dos períodos apurados (extraídos do <span className="font-mono">REFERENTE</span>) que aparecem na rodada.
              <div className="mt-1">
                <span className="font-semibold">Modo Caixa</span>: Receita = soma das entradas de entregas (rateio) na semana Seg–Dom calculada a partir da rodada.
              </div>
            </div>
          </div>
          <div className="bg-amber-50 border border-amber-100 rounded-xl p-4 text-sm text-amber-900">
            <div className="font-semibold mb-1">Como ler o Pagamento</div>
            <div>
              Pagamento (rodada) = rateio do que será liberado no dia selecionado. Diárias entram como custo adicional do ciclo.
              Descontos de adiantamento aparecem como ajuste (para explicar o líquido), mas o prejuízo usa o custo bruto.
            </div>
          </div>
        </div>

        {error ? (
          <div className="bg-red-50 border border-red-200 text-red-700 rounded-xl p-4 text-sm">{error}</div>
        ) : null}

        <div className="grid grid-cols-1 md:grid-cols-6 gap-4">
          <div className="bg-white rounded-xl p-4 border border-gray-100">
            <div className="text-xs text-gray-500">Receita (base - {mode === "caixa" ? "Caixa" : "Ciclo"})</div>
            <div className="text-xl font-semibold">{formatCurrency(totals.receitaBase)}</div>
            <div className="text-xs text-gray-500 mt-1">
              Ciclo: {formatCurrency(totals.receitaCiclos)} • Caixa: {formatCurrency(totals.receitaCaixa)}
            </div>
          </div>
          <div className="bg-white rounded-xl p-4 border border-gray-100">
            <div className="text-xs text-gray-500">Custo bruto (rodada + diárias)</div>
            <div className="text-xl font-semibold">{formatCurrency(totals.custoBruto)}</div>
            <div className="text-xs text-gray-500 mt-1">
              Pagamento: {formatCurrency(totals.pago)} • Diárias: {formatCurrency(totals.diarias)}
            </div>
          </div>
          <div className="bg-white rounded-xl p-4 border border-gray-100">
            <div className="text-xs text-gray-500">Desconto adiantamento (rodada)</div>
            <div className="text-xl font-semibold">{formatCurrency(totals.descontoAdiant)}</div>
            <div className="text-xs text-gray-500 mt-1">Pagamento líquido (bruto - desconto): {formatCurrency(totals.pagamentoLiquido)}</div>
          </div>
          <div className="bg-white rounded-xl p-4 border border-gray-100">
            <div className="text-xs text-gray-500">Saldo (base - bruto)</div>
            <div className="text-xl font-semibold">{formatCurrency(totals.saldoBaseBruto)}</div>
            <div className="text-xs text-gray-500 mt-1">Saldo (base - líquido): {formatCurrency(totals.saldoBaseLiquido)}</div>
          </div>
          <div className="bg-white rounded-xl p-4 border border-gray-100">
            <div className="text-xs text-gray-500">Adiantamentos (ciclo filtrado)</div>
            <div className="text-sm text-gray-700 mt-1">
              Adiantado: <span className="font-semibold">{formatCurrency(totals.adiantado)}</span>
            </div>
            <div className="text-sm text-gray-700">
              Reembolsado: <span className="font-semibold">{formatCurrency(totals.reembolsado)}</span>
            </div>
            <div className="text-sm text-gray-700">
              Líquido: <span className="font-semibold">{formatCurrency(totals.adiantLiq)}</span>
            </div>
          </div>
          <div className="bg-white rounded-xl p-4 border border-gray-100">
            <div className="text-xs text-gray-500">Controle rateio (pagamento)</div>
            <div className="text-sm text-gray-700">
              Mov: <span className="font-semibold">{formatCurrency(data?.controle?.pagamento_mov_total || 0)}</span>
            </div>
            <div className="text-sm text-gray-700">
              Rateado: <span className="font-semibold">{formatCurrency(data?.controle?.pagamento_rateado_total || 0)}</span>
            </div>
            <div className="text-sm text-gray-700">
              Dif.: <span className="font-semibold">{formatCurrency(data?.controle?.pagamento_diff || 0)}</span>
            </div>
          </div>
        </div>

        {noCyclePaymentRows.length ? (
          <div className="bg-white rounded-xl border border-gray-100 overflow-hidden">
            <div className="p-4 border-b border-gray-100 flex flex-wrap items-center justify-between gap-3">
              <div>
                <div className="font-semibold">Pagamentos sem receita do ciclo</div>
                <div className="text-xs text-gray-500">
                  {noCyclePaymentRows.length} farmácia(s) • Total custo bruto: <span className="font-semibold">{formatCurrency(noCyclePaymentTotal)}</span>
                </div>
              </div>
              <button
                type="button"
                className="px-3 py-2 text-sm rounded-lg border border-gray-200 hover:bg-gray-50"
                onClick={() => setShowNoCyclePayments((v) => !v)}
              >
                {showNoCyclePayments ? "Ocultar" : "Mostrar"}
              </button>
            </div>

            {showNoCyclePayments ? (
              <div className="overflow-auto">
                <table className="min-w-full text-sm">
                  <thead className="bg-gray-50 text-gray-700">
                    <tr>
                      <th className="text-left px-4 py-3">Farmácia</th>
                      <th className="text-right px-4 py-3">Custo (bruto)</th>
                      <th className="text-left px-4 py-3">Ciclos</th>
                      <th className="text-left px-4 py-3">Obs.</th>
                      <th className="text-right px-4 py-3">Exportar</th>
                    </tr>
                  </thead>
                  <tbody>
                    {noCyclePaymentRows
                      .slice()
                      .sort((a, b) => (b?.custo_bruto || 0) - (a?.custo_bruto || 0))
                      .map((r) => {
                        const cycles = formatCyclesCompact(r?.ciclos || []);
                        return (
                          <tr key={`no-cycle-${r.farmacia}`} className="border-t border-gray-100">
                            <td className="px-4 py-3 font-medium">{r.farmacia}</td>
                            <td className="px-4 py-3 text-right">{formatCurrency(r.custo_bruto || 0)}</td>
                            <td className="px-4 py-3 text-left">
                              <span className="text-xs text-gray-600" title={cycles.title || ""}>
                                {cycles.text}
                              </span>
                            </td>
                            <td className="px-4 py-3 text-left text-xs text-gray-600">
                              Pagamento encontrado, mas não localizamos receita de entregas dentro dos períodos apurados.
                            </td>
                            <td className="px-4 py-3 text-right">
                              <button
                                type="button"
                                className="px-3 py-1 text-xs rounded-lg border border-gray-200 hover:bg-gray-50"
                                onClick={() => handleExportXlsx(r.farmacia)}
                                disabled={loading}
                              >
                                Excel
                              </button>
                            </td>
                          </tr>
                        );
                      })}
                  </tbody>
                </table>
              </div>
            ) : null}
          </div>
        ) : null}

        <div className="bg-white rounded-xl border border-gray-100 overflow-hidden">
          <div className="p-4 border-b border-gray-100 flex items-center justify-between">
            <div className="font-semibold">Por farmácia</div>
            <div className="text-sm text-gray-500">{loading ? "Carregando..." : `${mainRowsFiltered.length} linhas`}</div>
          </div>

          <div className="overflow-auto">
            <table className="min-w-full text-sm">
              <thead className="bg-gray-50 text-gray-700">
                <tr>
                  <th className="text-left px-4 py-3">Farmácia</th>
                  <th className="text-right px-4 py-3">Receita (base)</th>
                  <th className="text-right px-4 py-3">Pagamento (rodada)</th>
                  <th className="text-right px-4 py-3">Diárias</th>
                  <th className="text-right px-4 py-3">Saldo</th>
                  <th className="text-right px-4 py-3">% Repasse</th>
                  <th className="text-left px-4 py-3">Ciclos</th>
                  <th className="text-right px-4 py-3">Exportar</th>
                </tr>
              </thead>
              <tbody>
                {mainRowsFiltered.map((r) => {
                  const pct = pctBaseForRow(r);
                  const cycles = formatCyclesCompact(r.ciclos || []);
                  const receitaBase = receitaBaseForRow(r);
                  const otherReceita = mode === "caixa" ? (r?.receita_ciclos_apurados || 0) : (r?.receita_semana_caixa || 0);
                  const otherLabel = mode === "caixa" ? "Ciclo" : "Caixa";
                  return (
                    <tr key={r.farmacia} className="border-t border-gray-100">
                      <td className="px-4 py-3 font-medium">{r.farmacia}</td>
                      <td className="px-4 py-3 text-right" title={`${otherLabel}: ${formatCurrency(otherReceita)}`}>
                        {formatCurrency(receitaBase || 0)}
                      </td>
                      <td className="px-4 py-3 text-right">{formatCurrency(r.pagamento_quinta || 0)}</td>
                      <td className="px-4 py-3 text-right">{formatCurrency(r.diarias_ciclo || 0)}</td>
                      <td className="px-4 py-3 text-right">{formatCurrency(saldoBaseForRow(r) || 0)}</td>
                      <td className="px-4 py-3 text-right">
                        {pct == null || !Number.isFinite(pct) ? (
                          <span className="inline-flex px-2 py-1 rounded-full text-xs font-semibold bg-gray-100 text-gray-600">—</span>
                        ) : (
                          <span className={`inline-flex px-2 py-1 rounded-full text-xs font-semibold ${repasseBadgeClass(pct)}`}>
                            {pct.toFixed(2)}%
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-left">
                        <span className="text-xs text-gray-600" title={cycles.title || ""}>
                          {cycles.text}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-right">
                        <button
                          type="button"
                          className="px-3 py-1 text-xs rounded-lg border border-gray-200 hover:bg-gray-50"
                          onClick={() => handleExportXlsx(r.farmacia)}
                          disabled={loading}
                        >
                          Excel
                        </button>
                      </td>
                    </tr>
                  );
                })}
                {!loading && mainRowsFiltered.length === 0 ? (
                  <tr>
                    <td className="px-4 py-6 text-gray-500" colSpan={8}>
                      Nenhum dado disponível para os filtros selecionados.
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}

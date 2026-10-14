import { useEffect, useMemo, useState } from "react";
import apiClient from "./apiClient";
import { formatCurrency } from "./utils/helpers";
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

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
  const [sortMode, setSortMode] = useState("repasse_desc"); // repasse_desc | repasse_asc | receita_desc
  const [onlyDivergent, setOnlyDivergent] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [showNoCyclePayments, setShowNoCyclePayments] = useState(false);
  const [showCycleRevenueNoPayment, setShowCycleRevenueNoPayment] = useState(false);
  const [showFuturePayments, setShowFuturePayments] = useState(false);
  const [showUnclassified, setShowUnclassified] = useState(false);
  const [data, setData] = useState(null);

  const [farmModalOpen, setFarmModalOpen] = useState(false);
  const [farmModalFarm, setFarmModalFarm] = useState("");
  const [farmModalLoading, setFarmModalLoading] = useState(false);
  const [farmModalError, setFarmModalError] = useState("");
  const [farmModalData, setFarmModalData] = useState(null);

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

  const closeFarmModal = () => {
    setFarmModalOpen(false);
    setFarmModalFarm("");
    setFarmModalLoading(false);
    setFarmModalError("");
    setFarmModalData(null);
  };

  const openFarmModal = async (farm) => {
    if (!farm) return;
    if (!payDay) return;
    setFarmModalOpen(true);
    setFarmModalFarm(farm);
    setFarmModalLoading(true);
    setFarmModalError("");
    setFarmModalData(null);
    try {
      const resp = await apiClient.get("/api/coop/conciliacao_entregas/farm_cycle_history", {
        params: { pay_day: payDay, farmacia: farm },
      });
      setFarmModalData(resp?.data || null);
    } catch (e) {
      setFarmModalError(e?.response?.data?.detail || e?.message || "Erro ao carregar histórico da farmácia.");
    } finally {
      setFarmModalLoading(false);
    }
  };

  const farmHistory = useMemo(() => {
    const cycles = farmModalData?.cycles || [];
    const short = (iso) => {
      if (!iso) return "—";
      const [y, m, d] = String(iso).slice(0, 10).split("-");
      return `${d}/${m}`;
    };
    return (cycles || []).map((c) => ({
      ...c,
      label: `${short(c.periodo_inicio)}–${short(c.periodo_fim)}`,
      receita_coop: Number(c.receita_coop || 0),
      receita_flux: Number(c.receita_flux || 0),
      custo_bruto: Number(c.custo_bruto || 0),
      pagamento: Number(c.pagamento || 0),
      diarias: Number(c.diarias || 0),
      iss: Number(c.iss || 0),
      inss: Number(c.inss || 0),
      saldo_coop: Number(c.saldo_coop || 0),
      pct_repasse_real: c.pct_repasse == null ? null : Number(c.pct_repasse),
      pct_repasse_plot:
        c.pct_repasse == null || !Number.isFinite(Number(c.pct_repasse))
          ? null
          : Math.min(Number(c.pct_repasse), 120),
      pct_repasse_capped:
        c.pct_repasse == null || !Number.isFinite(Number(c.pct_repasse)) ? false : Number(c.pct_repasse) > 120,
      is_current: Boolean(c.is_current),
    }));
  }, [farmModalData]);

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
    const receitaFluxCiclos = data?.totals?.receita_flux_ciclos_apurados || 0;
    const receitaTotalCiclos = data?.totals?.receita_total_ciclos_apurados ?? (receitaCiclos + receitaFluxCiclos);
    const pago = data?.totals?.pagamento_quinta || 0;
    const pagoSemFarmacia = data?.totals?.pagamento_quinta_sem_farmacia || 0;
    const diarias = data?.totals?.diarias_ciclo || 0;
    const diariasSemFarmacia = data?.totals?.diarias_ciclo_sem_farmacia || 0;
    const custoBruto = data?.totals?.custo_bruto ?? (pago + diarias);
    const descontoAdiant = data?.totals?.desconto_adiantamento_rodada || 0;
    const pagamentoLiquido = data?.totals?.pagamento_liquido ?? (custoBruto - descontoAdiant);
    const iss = data?.totals?.iss_ciclo || 0;
    const inss = data?.totals?.inss_ciclo || 0;
    const bancariasCoop = data?.totals?.despesas_bancarias_ciclo || 0;
    const bancariasFlux = data?.totals?.despesas_bancarias_flux_ciclo || 0;
    const simplesFlux = data?.totals?.simples_flux_estimado_ciclo || 0;
    const despesasCicloTotal =
      data?.totals?.despesas_ciclo_total ?? (iss + inss + bancariasCoop + bancariasFlux + simplesFlux);
    const saldoOperacional =
      data?.totals?.saldo_operacional ?? (receitaTotalCiclos - (custoBruto + despesasCicloTotal));
    const saldoCoop =
      data?.totals?.saldo_coop ?? (receitaCiclos - (custoBruto + iss + inss + bancariasCoop));
    const saldoFlux =
      data?.totals?.saldo_flux ?? (receitaFluxCiclos - (simplesFlux + bancariasFlux));

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
      receitaFluxCiclos,
      receitaTotalCiclos,
      pago,
      pagoSemFarmacia,
      diarias,
      diariasSemFarmacia,
      custoBruto,
      descontoAdiant,
      pagamentoLiquido,
      iss,
      inss,
      bancariasCoop,
      bancariasFlux,
      simplesFlux,
      despesasCicloTotal,
      saldoOperacional,
      saldoCoop,
      saldoFlux,
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

  const meta = useMemo(() => data?.meta || {}, [data]);

  const repasseBadgeClass = (pct) => {
    if (pct == null || !Number.isFinite(pct)) return "bg-gray-100 text-gray-600";
    const value = pct;
    if (value <= 70) return "bg-emerald-100 text-emerald-800 border border-emerald-200";
    if (value <= 80) return "bg-yellow-100 text-yellow-900 border border-yellow-200";
    if (value <= 90) return "bg-amber-200 text-amber-900 border border-amber-300";
    return "bg-red-200 text-red-900 border border-red-400 ring-2 ring-red-300 shadow-sm animate-pulse";
  };

  const fluxShareBadgeClass = (pct) => {
    if (pct == null || !Number.isFinite(pct)) return "bg-gray-100 text-gray-600";
    const value = pct;
    if (value >= 15) return "bg-emerald-100 text-emerald-800 border border-emerald-200";
    if (value >= 10) return "bg-yellow-100 text-yellow-900 border border-yellow-200";
    return "bg-red-100 text-red-800 border border-red-200";
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
  const futureRows = useMemo(() => data?.future_rows || [], [data]);
  const unclassified = useMemo(() => data?.unclassified || null, [data]);
  const unclassifiedPaymentsRows = useMemo(() => unclassified?.pagamentos_rows || [], [unclassified]);
  const unclassifiedDiariasRows = useMemo(() => unclassified?.diarias_rows || [], [unclassified]);
  const unclassifiedPaymentsTotal = useMemo(
    () =>
      Number.isFinite(Number(unclassified?.pagamentos_sem_farmacia_total))
        ? Number(unclassified?.pagamentos_sem_farmacia_total)
        : unclassifiedPaymentsRows.reduce((acc, r) => acc + (Number(r?.valor_rateado) || 0), 0),
    [unclassified, unclassifiedPaymentsRows]
  );
  const unclassifiedDiariasTotal = useMemo(
    () =>
      Number.isFinite(Number(unclassified?.diarias_sem_farmacia_total))
        ? Number(unclassified?.diarias_sem_farmacia_total)
        : unclassifiedDiariasRows.reduce((acc, r) => acc + (Number(r?.valor_rateado) || 0), 0),
    [unclassified, unclassifiedDiariasRows]
  );
  const receitaBaseForRow = (r) =>
    mode === "caixa" ? (r?.receita_semana_caixa || 0) : (r?.receita_ciclos_apurados || 0);
  const pctBaseForRow = (r) => (mode === "caixa" ? r?.pct_repasse_caixa : r?.pct_repasse);
  const saldoBaseForRow = (r) => (mode === "caixa" ? (r?.saldo_caixa || 0) : (r?.saldo || 0));
  const noCyclePaymentRows = useMemo(
    () =>
      rows.filter((r) => (r?.custo_bruto || 0) > 0 && receitaBaseForRow(r) <= 0),
    [rows, mode]
  );
  const cycleRevenueNoPaymentRows = useMemo(
    () => rows.filter((r) => (r?.custo_bruto || 0) <= 0 && receitaBaseForRow(r) > 0),
    [rows, mode]
  );
  const paidRows = useMemo(
    () => rows.filter((r) => (r?.custo_bruto || 0) > 0 && receitaBaseForRow(r) > 0),
    [rows, mode]
  );
  const paidRowsSorted = useMemo(() => {
    const arr = (paidRows || []).slice();
    const safePct = (r) => {
      const v = pctBaseForRow(r);
      return v == null || !Number.isFinite(v) ? null : Number(v);
    };
    if (sortMode === "repasse_asc") {
      arr.sort((a, b) => {
        const pa = safePct(a);
        const pb = safePct(b);
        if (pa == null && pb == null) return 0;
        if (pa == null) return 1;
        if (pb == null) return -1;
        return pa - pb;
      });
      return arr;
    }
    if (sortMode === "repasse_desc") {
      arr.sort((a, b) => {
        const pa = safePct(a);
        const pb = safePct(b);
        if (pa == null && pb == null) return 0;
        if (pa == null) return 1;
        if (pb == null) return -1;
        return pb - pa;
      });
      return arr;
    }
    arr.sort((a, b) => (receitaBaseForRow(b) || 0) - (receitaBaseForRow(a) || 0));
    return arr;
  }, [paidRows, sortMode, mode]);
  const noCyclePaymentTotal = useMemo(
    () => noCyclePaymentRows.reduce((acc, r) => acc + (Number(r?.custo_bruto) || 0), 0),
    [noCyclePaymentRows]
  );
  const cycleRevenueNoPaymentTotal = useMemo(
    () => cycleRevenueNoPaymentRows.reduce((acc, r) => acc + (Number(receitaBaseForRow(r)) || 0), 0),
    [cycleRevenueNoPaymentRows, mode]
  );
  const futurePaymentTotal = useMemo(
    () =>
      (futureRows || []).reduce(
        (acc, r) => acc + (Number(r?.pagamento_futuro) || 0) + (Number(r?.diarias_futuro) || 0),
        0
      ),
    [futureRows]
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

  const formatPeriodoCompact = (pi, pf) => {
    const a = String(pi || "").slice(0, 10);
    const b = String(pf || "").slice(0, 10);
    if (a && b) return `${formatDateLabel(a)} a ${formatDateLabel(b)}`;
    if (a) return `A partir de ${formatDateLabel(a)}`;
    if (b) return `Até ${formatDateLabel(b)}`;
    return "â€”";
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

          <div className="flex items-center gap-2">
            <span className="text-sm text-gray-600">Ordenar:</span>
            <select
              className="border rounded-lg px-3 py-2 text-sm"
              value={sortMode}
              onChange={(e) => setSortMode(e.target.value)}
            >
              <option value="repasse_desc">% Repasse (maior)</option>
              <option value="repasse_asc">% Repasse (menor)</option>
              <option value="receita_desc">Receita (maior)</option>
            </select>
          </div>

          <div className="ml-auto text-right text-sm text-gray-500">
            <div>Semana (Seg–Dom): {computedCashWeekLabel}</div>
            {meta?.pay_rateio_rows_total ? (
              <div className="text-xs text-gray-400">
                Rodada: {meta.pay_rateio_rows_total} rateios ({meta.pay_farms_total || 0} farmácias) • Receita (ciclos)
                considerada: {meta.rec_cycle_rows_considered || 0}/{meta.rec_cycle_rows_total || 0}
              </div>
            ) : null}
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
            <div className="text-xs text-gray-500">Receita Cooperativa (base - {mode === "caixa" ? "Caixa" : "Ciclo"})</div>
            <div className="text-xl font-semibold">{formatCurrency(totals.receitaBase)}</div>
            <div className="text-xs text-gray-500 mt-1">
              Ciclo: {formatCurrency(totals.receitaCiclos)} • Caixa: {formatCurrency(totals.receitaCaixa)} • Flux (ciclos):{" "}
              {formatCurrency(totals.receitaFluxCiclos || 0)}
            </div>
          </div>
          <div className="bg-white rounded-xl p-4 border border-gray-100">
            <div className="text-xs text-gray-500">Custo bruto (rodada + diárias)</div>
            <div className="text-xl font-semibold">{formatCurrency(totals.custoBruto)}</div>
            <div className="text-xs text-gray-500 mt-1">
              Pagamento: {formatCurrency(totals.pago)}{" "}
              {totals.pagoSemFarmacia ? (
                <span className="text-gray-400" title="Pagamentos (rateio) sem centro de custo de farmácia associado.">
                  (sem farmácia: {formatCurrency(totals.pagoSemFarmacia)})
                </span>
              ) : null}
              {" • "}Diárias: {formatCurrency(totals.diarias)}{" "}
              {totals.diariasSemFarmacia ? (
                <span className="text-gray-400" title="Diárias (rateio) sem centro de custo de farmácia associado.">
                  (sem farmácia: {formatCurrency(totals.diariasSemFarmacia)})
                </span>
              ) : null}
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
            <div className="text-xs text-gray-500 mt-1">
              Despesas (ciclo): ISS {formatCurrency(totals.iss || 0)} • INSS {formatCurrency(totals.inss || 0)} • Bancárias (Coop){" "}
              {formatCurrency(totals.bancariasCoop || 0)} • Bancárias (Flux) {formatCurrency(totals.bancariasFlux || 0)} • Simples (Flux){" "}
              {formatCurrency(totals.simplesFlux || 0)}
            </div>
            <div className="text-xs text-gray-500 mt-1">
              Saldo (c/ despesas): <span className="font-semibold">{formatCurrency(totals.saldoOperacional || 0)}</span>
            </div>
            <div className="text-xs text-gray-500 mt-1">
              Saldo Cooperativa: <span className="font-semibold">{formatCurrency(totals.saldoCoop || 0)}</span> • Saldo Flux:{" "}
              <span className="font-semibold">{formatCurrency(totals.saldoFlux || 0)}</span>
            </div>
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

        {futureRows.length ? (
          <div className="bg-white rounded-xl border border-gray-100 overflow-hidden">
            <div className="p-4 border-b border-gray-100 flex flex-wrap items-center justify-between gap-3">
              <div>
                <div className="font-semibold">Pagamentos com ciclo futuro (revisar)</div>
                <div className="text-xs text-gray-500">
                  {(futureRows || []).length} farmácia(s) • Total:{" "}
                  <span className="font-semibold">{formatCurrency(futurePaymentTotal)}</span>
                </div>
              </div>
              <button
                type="button"
                className="px-3 py-2 text-sm rounded-lg border border-gray-200 hover:bg-gray-50"
                onClick={() => setShowFuturePayments((v) => !v)}
              >
                {showFuturePayments ? "Ocultar" : "Mostrar"}
              </button>
            </div>

            {showFuturePayments ? (
              <div className="overflow-auto">
                <table className="min-w-full text-sm">
                  <thead className="bg-gray-50 text-gray-700">
                    <tr>
                      <th className="text-left px-4 py-3">Farmácia</th>
                      <th className="text-right px-4 py-3">Pagamento (futuro)</th>
                      <th className="text-right px-4 py-3">Diárias (futuro)</th>
                      <th className="text-left px-4 py-3">Ciclos futuros</th>
                      <th className="text-left px-4 py-3">Obs.</th>
                      <th className="text-right px-4 py-3">Exportar</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(futureRows || [])
                      .slice()
                      .sort(
                        (a, b) =>
                          (Number(b?.pagamento_futuro) || 0) +
                          (Number(b?.diarias_futuro) || 0) -
                          ((Number(a?.pagamento_futuro) || 0) + (Number(a?.diarias_futuro) || 0))
                      )
                      .map((r) => {
                        const cycles = formatCyclesCompact(r?.ciclos_futuros || []);
                        return (
                          <tr
                            key={`future-${r.farmacia}`}
                            className="border-t border-gray-100 hover:bg-gray-50 cursor-pointer"
                            onClick={() => openFarmModal(r.farmacia)}
                          >
                            <td className="px-4 py-3 font-medium">{r.farmacia}</td>
                            <td className="px-4 py-3 text-right">{formatCurrency(r.pagamento_futuro || 0)}</td>
                            <td className="px-4 py-3 text-right">{formatCurrency(r.diarias_futuro || 0)}</td>
                            <td className="px-4 py-3 text-left">
                              <span className="text-xs text-gray-600" title={cycles.title || ""}>
                                {cycles.text}
                              </span>
                            </td>
                            <td className="px-4 py-3 text-left text-xs text-gray-600">
                              Pagamentos/diárias cujo período apurado é posterior à rodada selecionada (antecipação ou
                              lançamento fora do ciclo). Não entram no cálculo principal.
                            </td>
                            <td className="px-4 py-3 text-right">
                              <button
                                type="button"
                                className="px-3 py-1 text-xs rounded-lg border border-gray-200 hover:bg-gray-50"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  handleExportXlsx(r.farmacia);
                                }}
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

        {(unclassifiedPaymentsRows.length || unclassifiedDiariasRows.length || unclassifiedPaymentsTotal || unclassifiedDiariasTotal) ? (
          <div className="bg-white rounded-xl border border-gray-100 overflow-hidden">
            <div className="p-4 border-b border-gray-100 flex flex-wrap items-center justify-between gap-3">
              <div>
                <div className="font-semibold">Lançamentos sem farmácia (auditoria)</div>
                <div className="text-xs text-gray-500">
                  Pagamentos: <span className="font-semibold">{formatCurrency(unclassifiedPaymentsTotal)}</span> • Diárias:{" "}
                  <span className="font-semibold">{formatCurrency(unclassifiedDiariasTotal)}</span>
                </div>
              </div>
              <button
                type="button"
                className="px-3 py-2 text-sm rounded-lg border border-gray-200 hover:bg-gray-50"
                onClick={() => setShowUnclassified((v) => !v)}
              >
                {showUnclassified ? "Ocultar" : "Mostrar"}
              </button>
            </div>

            {showUnclassified ? (
              <div className="p-4 space-y-6">
                <div>
                  <div className="flex items-baseline justify-between gap-3 flex-wrap">
                    <div className="font-semibold text-sm">Pagamentos de entregadores sem farmácia</div>
                    <div className="text-xs text-gray-500">
                      {unclassifiedPaymentsRows.length} registro(s) • Total:{" "}
                      <span className="font-semibold">{formatCurrency(unclassifiedPaymentsTotal)}</span>
                    </div>
                  </div>
                  {unclassifiedPaymentsRows.length ? (
                    <div className="overflow-auto mt-3">
                      <table className="min-w-full text-sm">
                        <thead className="bg-gray-50 text-gray-700">
                          <tr>
                            <th className="text-right px-4 py-3">Valor</th>
                            <th className="text-left px-4 py-3">Período apurado</th>
                            <th className="text-left px-4 py-3">Centro (raw)</th>
                            <th className="text-left px-4 py-3">Parceiro</th>
                            <th className="text-left px-4 py-3">Referente</th>
                            <th className="text-left px-4 py-3">Doc/Registros</th>
                            <th className="text-left px-4 py-3">Data mov.</th>
                          </tr>
                        </thead>
                        <tbody>
                          {unclassifiedPaymentsRows
                            .slice()
                            .sort((a, b) => (Number(b?.valor_rateado) || 0) - (Number(a?.valor_rateado) || 0))
                            .map((r, idx) => (
                              <tr key={`un-pay-${idx}`} className="border-t border-gray-100">
                                <td className="px-4 py-3 text-right font-semibold">{formatCurrency(r?.valor_rateado || 0)}</td>
                                <td className="px-4 py-3 text-left">
                                  <span className="text-xs text-gray-600">{formatPeriodoCompact(r?.periodo_inicio, r?.periodo_fim)}</span>
                                </td>
                                <td className="px-4 py-3 text-left">{r?.centro_custo_raw || "—"}</td>
                                <td className="px-4 py-3 text-left">{r?.parceiro || "—"}</td>
                                <td className="px-4 py-3 text-left">
                                  <span className="text-xs text-gray-600" title={r?.referente || ""}>
                                    {r?.referente || "—"}
                                  </span>
                                </td>
                                <td className="px-4 py-3 text-left">
                                  <div className="text-xs text-gray-600">
                                    Docto: <span className="font-mono">{r?.numero_docto || "—"}</span>
                                  </div>
                                  <div className="text-xs text-gray-600">
                                    Mov: <span className="font-mono">{r?.registro_movimento || "—"}</span> • Comp:{" "}
                                    <span className="font-mono">{r?.registro_compromisso || "—"}</span>
                                  </div>
                                </td>
                                <td className="px-4 py-3 text-left text-xs text-gray-600">{formatDateLabel(String(r?.data_mov || "").slice(0, 10))}</td>
                              </tr>
                            ))}
                        </tbody>
                      </table>
                    </div>
                  ) : (
                    <div className="text-xs text-gray-500 mt-2">Nenhum registro.</div>
                  )}
                </div>

                <div>
                  <div className="flex items-baseline justify-between gap-3 flex-wrap">
                    <div className="font-semibold text-sm">Diárias sem farmácia</div>
                    <div className="text-xs text-gray-500">
                      {unclassifiedDiariasRows.length} registro(s) • Total:{" "}
                      <span className="font-semibold">{formatCurrency(unclassifiedDiariasTotal)}</span>
                    </div>
                  </div>
                  {unclassifiedDiariasRows.length ? (
                    <div className="overflow-auto mt-3">
                      <table className="min-w-full text-sm">
                        <thead className="bg-gray-50 text-gray-700">
                          <tr>
                            <th className="text-right px-4 py-3">Valor</th>
                            <th className="text-left px-4 py-3">Período apurado</th>
                            <th className="text-left px-4 py-3">Centro (raw)</th>
                            <th className="text-left px-4 py-3">Parceiro</th>
                            <th className="text-left px-4 py-3">Referente</th>
                            <th className="text-left px-4 py-3">Doc/Registros</th>
                            <th className="text-left px-4 py-3">Data mov.</th>
                          </tr>
                        </thead>
                        <tbody>
                          {unclassifiedDiariasRows
                            .slice()
                            .sort((a, b) => (Number(b?.valor_rateado) || 0) - (Number(a?.valor_rateado) || 0))
                            .map((r, idx) => (
                              <tr key={`un-dia-${idx}`} className="border-t border-gray-100">
                                <td className="px-4 py-3 text-right font-semibold">{formatCurrency(r?.valor_rateado || 0)}</td>
                                <td className="px-4 py-3 text-left">
                                  <span className="text-xs text-gray-600">{formatPeriodoCompact(r?.periodo_inicio, r?.periodo_fim)}</span>
                                </td>
                                <td className="px-4 py-3 text-left">{r?.centro_custo_raw || "—"}</td>
                                <td className="px-4 py-3 text-left">{r?.parceiro || "—"}</td>
                                <td className="px-4 py-3 text-left">
                                  <span className="text-xs text-gray-600" title={r?.referente || ""}>
                                    {r?.referente || "—"}
                                  </span>
                                </td>
                                <td className="px-4 py-3 text-left">
                                  <div className="text-xs text-gray-600">
                                    Docto: <span className="font-mono">{r?.numero_docto || "—"}</span>
                                  </div>
                                  <div className="text-xs text-gray-600">
                                    Mov: <span className="font-mono">{r?.registro_movimento || "—"}</span> • Comp:{" "}
                                    <span className="font-mono">{r?.registro_compromisso || "—"}</span>
                                  </div>
                                </td>
                                <td className="px-4 py-3 text-left text-xs text-gray-600">{formatDateLabel(String(r?.data_mov || "").slice(0, 10))}</td>
                              </tr>
                            ))}
                        </tbody>
                      </table>
                    </div>
                  ) : (
                    <div className="text-xs text-gray-500 mt-2">Nenhum registro.</div>
                  )}
                </div>
              </div>
            ) : null}
          </div>
        ) : null}

        <div className="bg-white rounded-xl border border-gray-100 overflow-hidden">
            <div className="p-4 border-b border-gray-100 flex flex-wrap items-center justify-between gap-3">
              <div>
                <div className="font-semibold">Pagamentos sem receita do ciclo</div>
                <div className="text-xs text-gray-500">
                  {noCyclePaymentRows.length} farmácia(s) • Total custo bruto: <span className="font-semibold">{formatCurrency(noCyclePaymentTotal)}</span>
                </div>
              </div>
              {noCyclePaymentRows.length ? (
                <button
                  type="button"
                  className="px-3 py-2 text-sm rounded-lg border border-gray-200 hover:bg-gray-50"
                  onClick={() => setShowNoCyclePayments((v) => !v)}
                >
                  {showNoCyclePayments ? "Ocultar" : "Mostrar"}
                </button>
              ) : (
                <div className="text-xs text-gray-500">Nenhum registro.</div>
              )}
            </div>

            {showNoCyclePayments && noCyclePaymentRows.length ? (
              <div className="overflow-auto">
                <table className="min-w-full text-sm">
                  <thead className="bg-gray-50 text-gray-700">
                    <tr>
                      <th className="text-left px-4 py-3">Farmácia</th>
                      <th className="text-right px-4 py-3">Receita Flux</th>
                      <th className="text-right px-4 py-3">Receita Total (ciclos)</th>
                      <th className="text-right px-4 py-3">% Flux/Total</th>
                      <th className="text-right px-4 py-3">ISS</th>
                      <th className="text-right px-4 py-3">INSS</th>
                      <th className="text-right px-4 py-3">Banc. (Coop)</th>
                      <th className="text-right px-4 py-3">Banc. (Flux)</th>
                      <th className="text-right px-4 py-3">Simples (Flux)</th>
                      <th className="text-right px-4 py-3">Saldo Coop</th>
                      <th className="text-right px-4 py-3">Saldo Flux</th>
                      <th
                        className="text-right px-4 py-3"
                        title="Receita Total (Flux+Coop, ciclos) - (Custo bruto + ISS + INSS + Banc. Coop + Banc. Flux + Simples Flux estimado)"
                      >
                        Saldo operacional
                      </th>
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
                        const pctFlux = r?.pct_flux_total_ciclos;
                        return (
                          <tr
                            key={`no-cycle-${r.farmacia}`}
                            className="border-t border-gray-100 hover:bg-gray-50 cursor-pointer"
                            onClick={() => openFarmModal(r.farmacia)}
                          >
                            <td className="px-4 py-3 font-medium">{r.farmacia}</td>
                            <td className="px-4 py-3 text-right">{formatCurrency(r?.receita_flux_ciclos_apurados || 0)}</td>
                            <td className="px-4 py-3 text-right">{formatCurrency(r?.receita_total_ciclos_apurados || 0)}</td>
                            <td className="px-4 py-3 text-right">
                              {pctFlux == null || !Number.isFinite(pctFlux) ? (
                                <span className="inline-flex px-2 py-1 rounded-full text-xs font-semibold bg-gray-100 text-gray-600">—</span>
                              ) : (
                                <span
                                  className={`inline-flex px-2 py-1 rounded-full text-xs font-semibold ${fluxShareBadgeClass(
                                    pctFlux
                                  )}`}
                                >
                                  {pctFlux.toFixed(2)}%
                                </span>
                              )}
                            </td>
                            <td className="px-4 py-3 text-right">{formatCurrency(r.iss_ciclo || 0)}</td>
                            <td className="px-4 py-3 text-right">{formatCurrency(r.inss_ciclo || 0)}</td>
                            <td className="px-4 py-3 text-right">{formatCurrency(r.despesas_bancarias_ciclo || 0)}</td>
                            <td className="px-4 py-3 text-right">{formatCurrency(r.despesas_bancarias_flux_ciclo || 0)}</td>
                            <td className="px-4 py-3 text-right">{formatCurrency(r.simples_flux_estimado_ciclo || 0)}</td>
                            <td className="px-4 py-3 text-right">{formatCurrency(r.saldo_coop || 0)}</td>
                            <td className="px-4 py-3 text-right">{formatCurrency(r.saldo_flux || 0)}</td>
                            <td className="px-4 py-3 text-right font-semibold">{formatCurrency(r.saldo_operacional || 0)}</td>
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
                                onClick={(e) => {
                                  e.stopPropagation();
                                  handleExportXlsx(r.farmacia);
                                }}
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

        <div className="bg-white rounded-xl border border-gray-100 overflow-hidden">
            <div className="p-4 border-b border-gray-100 flex flex-wrap items-center justify-between gap-3">
              <div>
                <div className="font-semibold">Receitas do ciclo sem pagamento na rodada</div>
                <div className="text-xs text-gray-500">
                  {cycleRevenueNoPaymentRows.length} farmácia(s) • Total receita (base):{" "}
                  <span className="font-semibold">{formatCurrency(cycleRevenueNoPaymentTotal)}</span>
                </div>
              </div>
              {cycleRevenueNoPaymentRows.length ? (
                <button
                  type="button"
                  className="px-3 py-2 text-sm rounded-lg border border-gray-200 hover:bg-gray-50"
                  onClick={() => setShowCycleRevenueNoPayment((v) => !v)}
                >
                  {showCycleRevenueNoPayment ? "Ocultar" : "Mostrar"}
                </button>
              ) : (
                <div className="text-xs text-gray-500">Nenhum registro.</div>
              )}
            </div>

            {showCycleRevenueNoPayment && cycleRevenueNoPaymentRows.length ? (
              <div className="overflow-auto">
                <table className="min-w-full text-sm">
                  <thead className="bg-gray-50 text-gray-700">
                    <tr>
                      <th className="text-left px-4 py-3">Farmácia</th>
                      <th className="text-right px-4 py-3">Receita Cooperativa</th>
                      <th className="text-right px-4 py-3">Receita Flux</th>
                      <th className="text-right px-4 py-3">Receita Total (ciclos)</th>
                      <th className="text-right px-4 py-3">% Flux/Total</th>
                      <th className="text-right px-4 py-3">ISS</th>
                      <th className="text-right px-4 py-3">INSS</th>
                      <th className="text-right px-4 py-3">Banc. (Coop)</th>
                      <th className="text-right px-4 py-3">Banc. (Flux)</th>
                      <th className="text-right px-4 py-3">Simples (Flux)</th>
                      <th className="text-right px-4 py-3">Saldo Coop</th>
                      <th className="text-right px-4 py-3">Saldo Flux</th>
                      <th
                        className="text-right px-4 py-3"
                        title="Receita Total (Flux+Coop, ciclos) - (Custo bruto + ISS + INSS + Banc. Coop + Banc. Flux + Simples Flux estimado)"
                      >
                        Saldo operacional
                      </th>
                      <th className="text-right px-4 py-3" title="Receita (base selecionada) - Custo bruto (pagamento + diárias)">
                        Saldo (base - bruto)
                      </th>
                      <th className="text-left px-4 py-3">Ciclos</th>
                      <th className="text-left px-4 py-3">Obs.</th>
                      <th className="text-right px-4 py-3">Exportar</th>
                    </tr>
                  </thead>
                  <tbody>
                    {cycleRevenueNoPaymentRows
                      .slice()
                      .sort((a, b) => (receitaBaseForRow(b) || 0) - (receitaBaseForRow(a) || 0))
                      .map((r) => {
                        const cycles = formatCyclesCompact(r?.ciclos || []);
                        const receitaBase = receitaBaseForRow(r);
                        const pctFlux = r?.pct_flux_total_ciclos;
                        return (
                          <tr
                            key={`cycle-no-pay-${r.farmacia}`}
                            className="border-t border-gray-100 hover:bg-gray-50 cursor-pointer"
                            onClick={() => openFarmModal(r.farmacia)}
                          >
                            <td className="px-4 py-3 font-medium">{r.farmacia}</td>
                            <td className="px-4 py-3 text-right">{formatCurrency(receitaBase || 0)}</td>
                            <td className="px-4 py-3 text-right">{formatCurrency(r?.receita_flux_ciclos_apurados || 0)}</td>
                            <td className="px-4 py-3 text-right">{formatCurrency(r?.receita_total_ciclos_apurados || 0)}</td>
                            <td className="px-4 py-3 text-right">
                              {pctFlux == null || !Number.isFinite(pctFlux) ? (
                                <span className="inline-flex px-2 py-1 rounded-full text-xs font-semibold bg-gray-100 text-gray-600">—</span>
                              ) : (
                                <span
                                  className={`inline-flex px-2 py-1 rounded-full text-xs font-semibold ${fluxShareBadgeClass(
                                    pctFlux
                                  )}`}
                                >
                                  {pctFlux.toFixed(2)}%
                                </span>
                              )}
                            </td>
                            <td className="px-4 py-3 text-right">{formatCurrency(r.iss_ciclo || 0)}</td>
                            <td className="px-4 py-3 text-right">{formatCurrency(r.inss_ciclo || 0)}</td>
                            <td className="px-4 py-3 text-right">{formatCurrency(r.despesas_bancarias_ciclo || 0)}</td>
                            <td className="px-4 py-3 text-right">{formatCurrency(r.despesas_bancarias_flux_ciclo || 0)}</td>
                            <td className="px-4 py-3 text-right">{formatCurrency(r.simples_flux_estimado_ciclo || 0)}</td>
                            <td className="px-4 py-3 text-right">{formatCurrency(r.saldo_coop || 0)}</td>
                            <td className="px-4 py-3 text-right">{formatCurrency(r.saldo_flux || 0)}</td>
                            <td className="px-4 py-3 text-right font-semibold">{formatCurrency(r.saldo_operacional || 0)}</td>
                            <td className="px-4 py-3 text-right">{formatCurrency(saldoBaseForRow(r) || 0)}</td>
                            <td className="px-4 py-3 text-left">
                              <span className="text-xs text-gray-600" title={cycles.title || ""}>
                                {cycles.text}
                              </span>
                            </td>
                            <td className="px-4 py-3 text-left text-xs text-gray-600">
                              Receita encontrada nos ciclos apurados, mas sem pagamento na rodada selecionada.
                            </td>
                            <td className="px-4 py-3 text-right">
                              <button
                                type="button"
                                className="px-3 py-1 text-xs rounded-lg border border-gray-200 hover:bg-gray-50"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  handleExportXlsx(r.farmacia);
                                }}
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

        <div className="bg-white rounded-xl border border-gray-100 overflow-hidden">
          <div className="p-4 border-b border-gray-100 flex items-center justify-between">
            <div className="font-semibold">Por farmácia</div>
            <div className="text-sm text-gray-500">
              {loading
                ? "Carregando..."
                : `${paidRows.length} com pagamento • ${cycleRevenueNoPaymentRows.length} com receita sem pagamento • ${noCyclePaymentRows.length} com pagamento sem receita`}
            </div>
          </div>

          <div className="overflow-auto">
            <table className="min-w-full text-sm">
              <thead className="bg-gray-50 text-gray-700">
                <tr>
                  <th className="text-left px-4 py-3">Farmácia</th>
                  <th className="text-right px-4 py-3">Receita Cooperativa</th>
                  <th className="text-right px-4 py-3">Receita Flux</th>
                  <th className="text-right px-4 py-3">Receita Total (ciclos)</th>
                  <th className="text-right px-4 py-3">% Flux/Total</th>
                  <th className="text-right px-4 py-3">Pagamento (rodada)</th>
                  <th className="text-right px-4 py-3">Diárias</th>
                  <th className="text-right px-4 py-3">ISS</th>
                  <th className="text-right px-4 py-3">INSS</th>
                  <th className="text-right px-4 py-3">Banc. (Coop)</th>
                  <th className="text-right px-4 py-3">Banc. (Flux)</th>
                  <th className="text-right px-4 py-3">Simples (Flux)</th>
                  <th className="text-right px-4 py-3">Saldo Coop</th>
                  <th className="text-right px-4 py-3">Saldo Flux</th>
                  <th
                    className="text-right px-4 py-3"
                    title="Receita Total (Flux+Coop, ciclos) - (Custo bruto + ISS + INSS + Banc. Coop + Banc. Flux + Simples Flux estimado)"
                  >
                    Saldo operacional
                  </th>
                  <th className="text-right px-4 py-3" title="Receita (base selecionada) - Custo bruto (pagamento + diárias)">
                    Saldo (base - bruto)
                  </th>
                  <th className="text-right px-4 py-3">% Repasse</th>
                  <th className="text-left px-4 py-3">Ciclos</th>
                  <th className="text-right px-4 py-3">Exportar</th>
                </tr>
              </thead>
              <tbody>
                {paidRowsSorted.map((r) => {
                  const pct = pctBaseForRow(r);
                  const cycles = formatCyclesCompact(r.ciclos || []);
                  const receitaBase = receitaBaseForRow(r);
                  const otherReceita = mode === "caixa" ? (r?.receita_ciclos_apurados || 0) : (r?.receita_semana_caixa || 0);
                  const otherLabel = mode === "caixa" ? "Ciclo" : "Caixa";
                  const pctFlux = r?.pct_flux_total_ciclos;
                  return (
                    <tr
                      key={r.farmacia}
                      className="border-t border-gray-100 hover:bg-gray-50 cursor-pointer"
                      onClick={() => openFarmModal(r.farmacia)}
                    >
                      <td className="px-4 py-3 font-medium">{r.farmacia}</td>
                      <td className="px-4 py-3 text-right" title={`${otherLabel}: ${formatCurrency(otherReceita)}`}>
                        {formatCurrency(receitaBase || 0)}
                      </td>
                      <td className="px-4 py-3 text-right" title="Baseado nos ciclos apurados do REFERENTE">
                        {formatCurrency(r?.receita_flux_ciclos_apurados || 0)}
                      </td>
                      <td className="px-4 py-3 text-right" title="Flux + Coop, baseado nos ciclos apurados do REFERENTE">
                        {formatCurrency(r?.receita_total_ciclos_apurados || 0)}
                      </td>
                      <td className="px-4 py-3 text-right" title="Receita Flux / (Receita Flux + Receita Coop), no ciclo">
                        {pctFlux == null || !Number.isFinite(pctFlux) ? (
                          <span className="inline-flex px-2 py-1 rounded-full text-xs font-semibold bg-gray-100 text-gray-600">—</span>
                        ) : (
                          <span
                            className={`inline-flex px-2 py-1 rounded-full text-xs font-semibold ${fluxShareBadgeClass(
                              pctFlux
                            )}`}
                          >
                            {pctFlux.toFixed(2)}%
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-right">{formatCurrency(r.pagamento_quinta || 0)}</td>
                      <td className="px-4 py-3 text-right">{formatCurrency(r.diarias_ciclo || 0)}</td>
                      <td className="px-4 py-3 text-right">{formatCurrency(r.iss_ciclo || 0)}</td>
                      <td className="px-4 py-3 text-right">{formatCurrency(r.inss_ciclo || 0)}</td>
                      <td className="px-4 py-3 text-right">{formatCurrency(r.despesas_bancarias_ciclo || 0)}</td>
                      <td className="px-4 py-3 text-right">{formatCurrency(r.despesas_bancarias_flux_ciclo || 0)}</td>
                      <td className="px-4 py-3 text-right">{formatCurrency(r.simples_flux_estimado_ciclo || 0)}</td>
                      <td className="px-4 py-3 text-right">{formatCurrency(r.saldo_coop || 0)}</td>
                      <td className="px-4 py-3 text-right">{formatCurrency(r.saldo_flux || 0)}</td>
                      <td className="px-4 py-3 text-right font-semibold">{formatCurrency(r.saldo_operacional || 0)}</td>
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
                          onClick={(e) => {
                            e.stopPropagation();
                            handleExportXlsx(r.farmacia);
                          }}
                          disabled={loading}
                        >
                          Excel
                        </button>
                      </td>
                    </tr>
                  );
                })}
                {!loading && paidRowsSorted.length === 0 ? (
                  <tr>
                    <td className="px-4 py-6 text-gray-500" colSpan={19}>
                      Nenhum dado disponível para os filtros selecionados.
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {farmModalOpen ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4">
          <div className="w-full max-w-7xl max-h-[92vh] overflow-auto bg-white rounded-xl shadow-2xl border border-gray-200">
            <div className="flex items-start justify-between gap-4 px-5 py-4 border-b border-gray-100">
              <div>
                <div className="text-base font-semibold text-gray-900">{farmModalFarm || "Farmácia"}</div>
                <div className="text-xs text-gray-500 mt-1">
                  Comparação do ciclo atual vs 4 anteriores • Rodada: {formatDateLabel(payDay)}
                </div>
              </div>
              <button
                type="button"
                className="px-3 py-2 text-sm rounded-lg border border-gray-200 hover:bg-gray-50"
                onClick={closeFarmModal}
              >
                Fechar
              </button>
            </div>

            <div className="p-5">
              {farmModalLoading ? (
                <div className="text-sm text-gray-600">Carregando histórico...</div>
              ) : farmModalError ? (
                <div className="bg-red-50 border border-red-200 text-red-700 rounded-xl p-4 text-sm">{farmModalError}</div>
              ) : farmHistory.length ? (
                (() => {
                  const current = farmHistory.find((c) => c.is_current) || farmHistory.at(-1);
                  const impostos = (Number(current?.iss || 0) || 0) + (Number(current?.inss || 0) || 0);
                  return (
                    <>
                      <div className="grid grid-cols-1 md:grid-cols-6 gap-3 mb-4">
                        <div className="bg-blue-50 border border-blue-100 rounded-xl p-3">
                          <div className="text-xs text-blue-700">Receita Coop (ciclo)</div>
                          <div className="text-lg font-semibold text-blue-900">{formatCurrency(current?.receita_coop || 0)}</div>
                          <div className="text-[11px] text-blue-700 mt-1">
                            Ciclo atual: {formatDateLabel(current?.periodo_inicio)}–{formatDateLabel(current?.periodo_fim)}
                          </div>
                        </div>
                        <div className="bg-indigo-50 border border-indigo-100 rounded-xl p-3">
                          <div className="text-xs text-indigo-700">Receita Flux (ciclo)</div>
                          <div className="text-lg font-semibold text-indigo-900">{formatCurrency(current?.receita_flux || 0)}</div>
                          <div className="text-[11px] text-indigo-700 mt-1">Mesmo critério do report (DATA MOVIMENTO no intervalo)</div>
                        </div>
                        <div className="bg-red-50 border border-red-100 rounded-xl p-3">
                          <div className="text-xs text-red-700">Custo bruto</div>
                          <div className="text-lg font-semibold text-red-900">{formatCurrency(current?.custo_bruto || 0)}</div>
                          <div className="text-[11px] text-red-700 mt-1">
                            Pagamento: {formatCurrency(current?.pagamento || 0)} • Diárias: {formatCurrency(current?.diarias || 0)}
                          </div>
                        </div>
                        <div className="bg-amber-50 border border-amber-100 rounded-xl p-3">
                          <div className="text-xs text-amber-800">ISS + INSS</div>
                          <div className="text-lg font-semibold text-amber-900">{formatCurrency(impostos)}</div>
                          <div className="text-[11px] text-amber-800 mt-1">
                            ISS: {formatCurrency(current?.iss || 0)} • INSS: {formatCurrency(current?.inss || 0)}
                          </div>
                        </div>
                        <div className="bg-emerald-50 border border-emerald-100 rounded-xl p-3">
                          <div className="text-xs text-emerald-700">Saldo Coop</div>
                          <div className="text-lg font-semibold text-emerald-900">{formatCurrency(current?.saldo_coop || 0)}</div>
                          <div className="text-[11px] text-emerald-700 mt-1">Receita - (bruto + ISS + INSS)</div>
                        </div>
                        <div className="bg-gray-50 border border-gray-100 rounded-xl p-3">
                          <div className="text-xs text-gray-600">% Repasse</div>
                          <div className="text-lg font-semibold text-gray-900">
                            {current?.pct_repasse_real == null || !Number.isFinite(current?.pct_repasse_real)
                              ? "—"
                              : `${current.pct_repasse_real.toFixed(2)}%`}
                          </div>
                          <div className="text-[11px] text-gray-600 mt-1">(Custo bruto / Receita Coop)</div>
                        </div>
                      </div>

                      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div className="bg-white rounded-xl border border-gray-100 p-4">
                          <div className="font-semibold text-sm mb-2">Receita vs Custo vs Saldo (5 ciclos)</div>
                          <div className="h-80">
                            <ResponsiveContainer width="100%" height="100%">
                              <ComposedChart data={farmHistory}>
                                <CartesianGrid strokeDasharray="3 3" />
                                <XAxis dataKey="label" tick={{ fontSize: 12 }} />
                                <YAxis tickFormatter={(v) => `R$ ${Number(v || 0).toLocaleString("pt-BR")}`} width={72} />
                                <Tooltip
                                  formatter={(v, name) => [formatCurrency(Number(v || 0)), name]}
                                  labelFormatter={(l) => `Ciclo: ${l}`}
                                />
                                <Bar dataKey="receita_coop" name="Receita Coop" fill="#2563EB" radius={[4, 4, 0, 0]} />
                                <Bar dataKey="custo_bruto" name="Custo bruto" fill="#DC2626" radius={[4, 4, 0, 0]} />
                                <Line type="monotone" dataKey="saldo_coop" name="Saldo Coop" stroke="#16A34A" strokeWidth={2} dot />
                              </ComposedChart>
                            </ResponsiveContainer>
                          </div>
                        </div>

                        <div className="bg-white rounded-xl border border-gray-100 p-4">
                          <div className="font-semibold text-sm mb-2">% Repasse (5 ciclos)</div>
                          <div className="h-80">
                            <ResponsiveContainer width="100%" height="100%">
                              <LineChart data={farmHistory}>
                                <CartesianGrid strokeDasharray="3 3" />
                                <XAxis dataKey="label" tick={{ fontSize: 12 }} />
                                <YAxis domain={[0, 120]} tickFormatter={(v) => `${v}%`} width={44} />
                                <Tooltip
                                  formatter={(v, name, item) => {
                                    const real = item?.payload?.pct_repasse_real;
                                    if (real == null || !Number.isFinite(Number(real))) return ["—", "% Repasse"];
                                    const capped = item?.payload?.pct_repasse_capped;
                                    return [capped ? `${Number(real).toFixed(2)}% (cap 120%)` : `${Number(real).toFixed(2)}%`, "% Repasse"];
                                  }}
                                  labelFormatter={(l) => `Ciclo: ${l}`}
                                />
                                <ReferenceLine y={70} stroke="#10B981" strokeDasharray="4 4" />
                                <ReferenceLine y={80} stroke="#F59E0B" strokeDasharray="4 4" />
                                <ReferenceLine y={90} stroke="#EF4444" strokeDasharray="4 4" />
                                <Line
                                  type="monotone"
                                  dataKey="pct_repasse_plot"
                                  name="% Repasse"
                                  stroke="#111827"
                                  strokeWidth={2}
                                  dot={(props) => {
                                    const { cx, cy, payload } = props || {};
                                    if (cx == null || cy == null) return null;
                                    const capped = payload?.pct_repasse_capped;
                                    return (
                                      <circle
                                        cx={cx}
                                        cy={cy}
                                        r={3.5}
                                        fill={capped ? "#DC2626" : "#111827"}
                                        stroke={capped ? "#FCA5A5" : "#E5E7EB"}
                                        strokeWidth={1}
                                      />
                                    );
                                  }}
                                  connectNulls
                                />
                              </LineChart>
                            </ResponsiveContainer>
                          </div>
                          <div className="text-xs text-gray-500 mt-2">
                            Linhas-guia: 70% (verde), 80% (amarelo), 90% (vermelho). Valores acima de 120% são marcados em vermelho (cap).
                          </div>
                        </div>
                      </div>
                    </>
                  );
                })()
              ) : (
                <div className="text-sm text-gray-600">Sem histórico para exibir.</div>
              )}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

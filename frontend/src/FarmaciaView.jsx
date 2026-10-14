import React, { useEffect, useState } from "react";
import { Calendar, Filter, AlertTriangle } from "lucide-react";
import {
    PieChart,
    Pie,
    Cell,
    BarChart,
    Bar,
    XAxis,
    YAxis,
    ResponsiveContainer,
    Tooltip,
    Legend,
    CartesianGrid,
} from "recharts";
import api from "./apiClient";
import { formatCurrency, getEvolutionColor, getHeatmapColor } from "./utils/helpers";
import * as XLSX from 'xlsx';

const FarmaciaView = ({ context = "flux" }) => {
    const [rankingData, setRankingData] = useState([]);
    const [rankingConsolidadoData, setRankingConsolidadoData] = useState([]);
    const [financeData, setFinanceData] = useState([]);
    const [loading, setLoading] = useState(true);
    const [viewMode, setViewMode] = useState("operacao"); // operacao | consolidado
    const [consolidadoSort, setConsolidadoSort] = useState("receita_total_desc"); // receita_total_desc | repasse_desc | repasse_asc
    const [filterMes, setFilterMes] = useState("");
    const [filterFarmacias, setFilterFarmacias] = useState([]);
    const [uniqueMeses, setUniqueMeses] = useState([]);
    const [uniqueFarmacias, setUniqueFarmacias] = useState([]);
    const [selectedFarmacia, setSelectedFarmacia] = useState(null);
    const [farmaciaDetail, setFarmaciaDetail] = useState(null);
    const [hoveredFarmacia, setHoveredFarmacia] = useState(null);
    const [hoverDetail, setHoverDetail] = useState(null);

    useEffect(() => {
        fetchRankingData();
        // ao trocar contexto ou visao, limpar selecoes para evitar "stale UI"
        setFilterMes("");
        setFilterFarmacias([]);
        setSelectedFarmacia(null);
        setFarmaciaDetail(null);
        setHoveredFarmacia(null);
        setHoverDetail(null);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [context, viewMode]);

    const fetchRankingData = async () => {
        setLoading(true);
        const updateFiltersFrom = (rows) => {
            const mesData = (rows || [])
                .map((item) => ({ mes_nome: item.mes_nome, mes_ano: item.mes_ano }))
                .filter((item) => item.mes_nome && item.mes_ano);

            const uniqueMesData = Array.from(
                new Map(mesData.map((item) => [item.mes_ano, item])).values()
            );

            const meses = uniqueMesData
                .sort((a, b) => a.mes_ano.localeCompare(b.mes_ano))
                .map((item) => item.mes_nome);

            const farmacias = [...new Set((rows || []).map((item) => item.farmacia))]
                .filter(Boolean)
                .sort();

            setUniqueMeses(meses);
            setUniqueFarmacias(farmacias);
        };

        try {
            if (viewMode === "consolidado") {
                const response = await api.get("/api/ranking_farmacias_consolidado");
                setRankingConsolidadoData(response.data || []);
                setRankingData([]);
                setFinanceData([]);
                updateFiltersFrom(response.data || []);
            } else {
                const response = await api.get("/api/ranking_farmacias", { params: { context } });
                setRankingData(response.data || []);
                setRankingConsolidadoData([]);
                updateFiltersFrom(response.data || []);

                try {
                    const fin = await api.get("/api/dre/financeiro", { params: { context } });
                    setFinanceData(fin.data || []);
                } catch (err) {
                    console.error("Erro ao buscar dados financeiros:", err);
                }
            }
        } catch (error) {
            console.error("Erro ao buscar ranking:", error);
        } finally {
            setLoading(false);
        }
    };

    const activeRankingData = viewMode === "consolidado" ? rankingConsolidadoData : rankingData;

    const filteredData = activeRankingData.filter((item) => {
        const matchMes = filterMes ? item.mes_nome === filterMes : true;
        const matchFarmacia = filterFarmacias.length ? filterFarmacias.includes(item.farmacia) : true;
        return matchMes && matchFarmacia;
    });

    const rentabilidades = filteredData
        .map((item) =>
            viewMode === "consolidado"
                ? (item.rentabilidade ?? 0)
                : (item.rentabilidade_competencia ?? item.rentabilidade)
        )
        .filter((v) => v !== null && v !== undefined);
    const minRent = rentabilidades.length ? Math.min(...rentabilidades) : 0;
    const maxRent = rentabilidades.length ? Math.max(...rentabilidades) : 0;

    const exportExcel = () => {
        if (!filteredData.length) {
            alert('Nenhum dado para exportar com os filtros atuais.');
            return;
        }

        const dataToExport = filteredData.map(row => ({
            'Mês': row.mes_nome,
            'Mês/Ano': row.mes_ano,
            'Farmácia': row.farmacia,
            'Receita Bruta': row.receita_bruta,
            'Despesa Variável': row.despesa_variavel_rateada,
            'Despesa Fixa': row.despesa_fixa_rateada,
            'Impostos': row.imposto_rateado_competencia || row.imposto_rateado,
            'Despesa Total': row.despesa_total || row.despesa_total_competencia,
            'Margem Contribuição (%)': (row.margem_contribuicao_pct_competencia || row.margem_contribuicao_pct || 0) / 100,
            'Rentabilidade (%)': (row.rentabilidade_competencia || row.rentabilidade || 0) / 100,
            'Participação Lucro (%)': (row.participacao_lucro || 0) / 100
        }));

        const ws = XLSX.utils.json_to_sheet(dataToExport);
        const wb = XLSX.utils.book_new();
        XLSX.utils.book_append_sheet(wb, ws, "Ranking");
        XLSX.writeFile(wb, "ranking_farmacias.xlsx");
    };

    const buildFarmaciaDetail = (farmacia, baseRow) => {
        if (!farmacia) return null;
        const rows = rankingData.filter((r) => r.farmacia === farmacia);
        if (!rows.length) return null;

        const mesAnoRef = baseRow?.mes_ano || rows[0]?.mes_ano || "";
        const yearRef = mesAnoRef.split("-")[0] || "";
        const rowsAno = rows.filter((r) => (r.mes_ano || "").startsWith(yearRef));

        const receitaYtd = rowsAno.reduce((acc, r) => acc + (r.receita_bruta || 0), 0);
        const impostoYtd = rowsAno.reduce(
            (acc, r) => acc + (r.imposto_rateado_competencia ?? r.imposto_rateado ?? 0),
            0
        );
        const despesasTotaisYtd = rowsAno.reduce(
            (acc, r) =>
                acc +
                (r.despesa_total_competencia ??
                    r.despesa_total ??
                    (r.despesas_rateadas_operacionais ?? r.despesa_rateada_total ?? 0) +
                        (r.imposto_rateado_competencia ?? r.imposto_rateado ?? 0)),
            0
        );
        const margemPct = receitaYtd > 0 ? ((receitaYtd - despesasTotaisYtd) / receitaYtd) * 100 : 0;
        const rentMedCalc =
            rowsAno.length
                ? rowsAno.reduce(
                      (acc, r) => acc + (r.rentabilidade_competencia ?? r.rentabilidade ?? 0),
                      0
                  ) / rowsAno.length
                : 0;

        const mensal = rowsAno
            .map((r) => ({
                mes_ano: r.mes_ano,
                mes_nome: r.mes_nome,
                receita: r.receita_bruta || 0,
                total_despesas:
                    r.despesa_total_competencia ??
                    r.despesa_total ??
                    (r.despesas_rateadas_operacionais ?? r.despesa_rateada_total ?? 0) +
                        (r.imposto_rateado_competencia ?? r.imposto_rateado ?? 0),
                rentabilidade: r.rentabilidade_competencia ?? r.rentabilidade ?? 0,
            }))
            .sort((a, b) => (a.mes_ano || "").localeCompare(b.mes_ano || ""));

        const maxReceita = mensal.length ? Math.max(...mensal.map((m) => m.receita)) : 0;
        const maiorMes = mensal.reduce(
            (best, cur) => (cur.receita > (best?.receita || 0) ? cur : best),
            null
        );
        const menorMes = mensal.reduce(
            (worst, cur) => (cur.receita < (worst?.receita ?? cur.receita) ? cur : worst),
            mensal[0] || null
        );

        const norm = (s) =>
            (s || "")
                .normalize("NFD")
                .replace(/[\u0300-\u036f]/g, "")
                .toUpperCase();
        const finFarm = financeData.filter(
            (f) =>
                norm(f.centro_custo) === norm(farmacia) &&
                norm(f.entrada_saida || f["ENTRADA/SAIDA"]) === "ENTRADA" &&
                (f.mes_ano || "").startsWith(yearRef)
        );
        const mix = { entregas: 0, setup: 0, reembolso: 0, outros: 0 };
        finFarm.forEach((f) => {
            const plano = norm(f.plano_contas || f["PLANO DE CONTAS"]);
            const val = Number(f.valor || f["VALOR MOV.CONTA"] || 0);
            if (plano.includes("RECEITAS COM ENTREGAS")) mix.entregas += val;
            else if (plano.includes("RECEITAS COM SETUP")) mix.setup += val;
            else if (plano.includes("RECEITAS COM REEMBOLSOS")) mix.reembolso += val;
            else mix.outros += val;
        });
        const mixTotal = mix.entregas + mix.setup + mix.reembolso + mix.outros;

        // Despesas rateadas (usa valores do ranking se disponíveis; fallback financeiro bruto da farmácia)
        const despVarRateada = baseRow?.despesa_variavel_rateada ?? 0;
        const despFixRateada = baseRow?.despesa_fixa_rateada ?? 0;
        const impostoRateadoRow = baseRow?.imposto_rateado_competencia ?? baseRow?.imposto_rateado ?? 0;

        let despVarYtd = despVarRateada;
        let despFixYtd = despFixRateada;
        let impostoYtdCalc = impostoRateadoRow;
        if (!despVarYtd && !despFixYtd) {
            const finFarmSaidas = financeData.filter(
                (f) =>
                    norm(f.centro_custo) === norm(farmacia) &&
                    norm(f.entrada_saida || f["ENTRADA/SAIDA"]) === "SAIDA" &&
                    (f.mes_ano || "").startsWith(yearRef)
            );
            finFarmSaidas.forEach((f) => {
                const tipo = norm(f.tipo_custo || f["TIPO_CUSTO"]);
                const plano = norm(f.plano_contas || f["PLANO DE CONTAS"]);
                const val = Math.abs(Number(f.valor || f["VALOR MOV.CONTA"] || 0));
                if (plano.includes("SIMPLES NACIONAL")) {
                    impostoYtdCalc += val;
                    return;
                }
                if (tipo === "VARIAVEL") despVarYtd += val;
                else if (tipo === "FIXO") despFixYtd += val;
            });
        }

        const alerts = [];
        // variação comparando o mês de referência (baseRow) com o mês anterior na série
        let variacaoMes = 0;
        if (baseRow) {
            const idxRef = mensal.findIndex((m) => m.mes_ano === baseRow.mes_ano);
            if (idxRef > 0) {
                const curr = mensal[idxRef].receita || 0;
                const prev = mensal[idxRef - 1].receita || 0;
                variacaoMes = prev > 0 ? ((curr - prev) / prev) * 100 : 0;
            }
        } else if (mensal.length >= 2) {
            const ult = mensal[mensal.length - 1].receita;
            const ant = mensal[mensal.length - 2].receita || 0;
            variacaoMes = ant > 0 ? ((ult - ant) / ant) * 100 : 0;
        }
        if (variacaoMes < -15) alerts.push("Queda >15% no mês de referência");
        if (baseRow?.imposto_estimado) alerts.push("Imposto estimado");
        if (mix.setup === 0) alerts.push("Sem receitas de setup");

        // Sazonalidade média por mês (todas as entradas da farmácia)
        const histReceitas = financeData.filter(
            (f) =>
                norm(f.centro_custo) === norm(farmacia) &&
                norm(f.entrada_saida || f["ENTRADA/SAIDA"]) === "ENTRADA"
        );
        const sazMap = {};
        histReceitas.forEach((f) => {
            const mes = (f.mes_nome || "").trim();
            const val = Number(f.valor || f["VALOR MOV.CONTA"] || 0);
            if (!mes) return;
            if (!sazMap[mes]) sazMap[mes] = { total: 0, count: 0 };
            sazMap[mes].total += val;
            sazMap[mes].count += 1;
        });
        const sazList = Object.entries(sazMap).map(([mes, obj]) => ({
            mes,
            media: obj.count ? obj.total / obj.count : 0,
        }));
        const sazTotal = sazList.reduce((acc, s) => acc + s.media, 0);
        const sazPerc = sazList.map((s) => ({
            mes: s.mes,
            pct: sazTotal > 0 ? (s.media / sazTotal) * 100 : 0,
        }));

        // Projeção anual simples: crescimento médio + sazonalidade relativa
        let projReceitaAnual = null;
        let projMinMes = null;
        let projMaxMes = null;
        if (mensal.length >= 2) {
            const rates = [];
            for (let i = 1; i < mensal.length; i++) {
                const prev = mensal[i - 1].receita || 0;
                const curr = mensal[i].receita || 0;
                if (prev > 0) rates.push((curr - prev) / prev);
            }
            const avg = rates.length ? rates.reduce((a, b) => a + b, 0) / rates.length : 0;
            const last = mensal[mensal.length - 1].receita || 0;
            const sazFactors =
                sazPerc.length > 0
                    ? sazPerc.map((s) => ({ mes: s.mes, factor: s.pct / 100 }))
                    : Array.from({ length: 12 }, () => ({ factor: 1 / 12 }));
            const forecasts = sazFactors.map((s) => last * (1 + avg) * s.factor * 12);
            projReceitaAnual = forecasts.reduce((acc, v) => acc + v, 0);
            projMinMes = Math.min(...forecasts);
            projMaxMes = Math.max(...forecasts);
        }

        return {
            farm: farmacia,
            year: yearRef,
            receitaYtd,
            impostoYtd,
            despesasTotaisYtd,
            despVarYtd,
            despFixYtd,
            impostoYtdCalc: impostoYtdCalc || impostoYtd,
            mensal,
            maxReceita,
            maiorMes,
            menorMes,
            rentMed: rentMedCalc,
            margemPct,
            mix,
            mixTotal,
            alerts,
            sazPerc,
            projReceitaAnual,
            projMinMes,
            projMaxMes,
            variacaoMes,
        };
    };

    useEffect(() => {
        if (viewMode !== "operacao") {
            setFarmaciaDetail(null);
            return;
        }
        if (selectedFarmacia) {
            const baseRow =
                filteredData.find((r) => r.farmacia === selectedFarmacia) ||
                rankingData.find((r) => r.farmacia === selectedFarmacia);
            setFarmaciaDetail(buildFarmaciaDetail(selectedFarmacia, baseRow));
        } else {
            setFarmaciaDetail(null);
        }
    }, [selectedFarmacia, filteredData, rankingData, viewMode]);

    const getRepasseHeatmapClass = (pct) => {
        const p = Number(pct || 0);
        if (p >= 81) return "bg-rose-100 text-rose-800 border border-rose-200";
        if (p >= 71) return "bg-amber-100 text-amber-800 border border-amber-200";
        return "bg-emerald-100 text-emerald-800 border border-emerald-200";
    };

    const consolidatedAggRows = React.useMemo(() => {
        if (viewMode !== "consolidado") return [];
        const map = new Map();
        (filteredData || []).forEach((r) => {
            const key = r.farmacia || "";
            const prev = map.get(key) || {
                farmacia: key,
                receita_flux: 0,
                receita_coop: 0,
                receita_total: 0,
                despesas_op_flux: 0,
                despesas_op_coop: 0,
                despesas_op_total: 0,
                imposto_flux: 0,
                imposto_coop: 0,
                imposto_total: 0,
                despesa_total: 0,
                repasse_entregador_total: 0,
                imposto_estimado: false,
            };
            prev.receita_flux += Number(r.receita_flux || 0);
            prev.receita_coop += Number(r.receita_coop || 0);
            prev.receita_total += Number(r.receita_total || 0);
            prev.despesas_op_flux += Number(r.despesas_op_flux || 0);
            prev.despesas_op_coop += Number(r.despesas_op_coop || 0);
            prev.despesas_op_total += Number(r.despesas_op_total || 0);
            prev.imposto_flux += Number(r.imposto_flux || 0);
            prev.imposto_coop += Number(r.imposto_coop || 0);
            prev.imposto_total += Number(r.imposto_total || 0);
            prev.despesa_total += Number(r.despesa_total || 0);
            prev.repasse_entregador_total += Number(r.repasse_entregador_total || 0);
            prev.imposto_estimado = Boolean(prev.imposto_estimado || r.imposto_estimado);
            map.set(key, prev);
        });
        const arr = Array.from(map.values());
        arr.forEach((r) => {
            const receita = r.receita_total || 0;
            const receitaCiclo = (r.receita_flux || 0) + (r.receita_coop || 0);
            const rol = receita - (r.despesa_total || 0);
            r.rol = rol;
            r.rentabilidade = receita ? (rol / receita) * 100 : 0;
            const margem = receita - (r.despesas_op_total || 0);
            r.margem_contribuicao = margem;
            r.margem_contribuicao_pct = receita ? (margem / receita) * 100 : 0;
            r.pct_flux_do_total = receitaCiclo ? (Number(r.receita_flux || 0) / receitaCiclo) * 100 : 0;
            r.repasse_entregador_pct = r.receita_coop ? (Number(r.repasse_entregador_total || 0) / Number(r.receita_coop || 0)) * 100 : 0;
        });
        if (consolidadoSort === "repasse_desc") {
            arr.sort((a, b) => (Number(b.repasse_entregador_pct || 0) - Number(a.repasse_entregador_pct || 0)));
        } else if (consolidadoSort === "repasse_asc") {
            arr.sort((a, b) => (Number(a.repasse_entregador_pct || 0) - Number(b.repasse_entregador_pct || 0)));
        } else {
            arr.sort((a, b) => (Number(b.receita_total || 0) - Number(a.receita_total || 0)));
        }
        return arr;
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [viewMode, filteredData, consolidadoSort]);

    if (loading) {
        return (
            <div className="flex items-center justify-center h-96">
                <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600"></div>
            </div>
        );
    }

    return (
        <div className="space-y-6 p-6 bg-gray-50 min-h-screen">
            <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-100">
                <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
                    <h2 className="text-2xl font-bold text-gray-800 flex items-center">
                        <Filter className="w-6 h-6 mr-2 text-blue-500" />
                        Ranking de Farmacias
                    </h2>

                    <div className="flex flex-col md:flex-row gap-3 items-center">
                        <select
                            className="bg-gray-50 border border-gray-200 text-gray-700 text-sm rounded-lg focus:ring-blue-500 focus:border-blue-500 block p-2.5 outline-none min-w-[220px]"
                            value={viewMode}
                            onChange={(e) => setViewMode(e.target.value)}
                        >
                            <option value="operacao">Operacao (contexto atual)</option>
                            <option value="consolidado">Consolidado (Flux + Cooperativa)</option>
                        </select>

                        <div className="relative">
                            <Calendar className="absolute left-3 top-2.5 h-4 w-4 text-gray-400" />
                            <select
                                className="pl-9 bg-gray-50 border border-gray-200 text-gray-700 text-sm rounded-lg focus:ring-blue-500 focus:border-blue-500 block p-2.5 outline-none min-w-[180px]"
                                value={filterMes}
                                onChange={(e) => setFilterMes(e.target.value)}
                            >
                                <option value="">Todos os Meses</option>
                                {uniqueMeses.map((m) => (
                                    <option key={m} value={m}>
                                        {m}
                                    </option>
                                ))}
                            </select>
                        </div>

                        <select
                            multiple
                            className="bg-gray-50 border border-gray-200 text-gray-700 text-sm rounded-lg focus:ring-blue-500 focus:border-blue-500 block p-2.5 outline-none min-w-[200px] h-28"
                            value={filterFarmacias}
                            onChange={(e) =>
                                setFilterFarmacias(Array.from(e.target.selectedOptions).map((o) => o.value))
                            }
                        >
                            {uniqueFarmacias.map((p) => (
                                <option key={p} value={p}>
                                    {p}
                                </option>
                            ))}
                        </select>
                        <button
                            onClick={() => setFilterFarmacias([])}
                            className="px-3 py-2 bg-gray-100 text-gray-700 rounded-lg hover:bg-gray-200 transition text-sm"
                        >
                            Limpar seleção
                        </button>
                        {viewMode === "operacao" && (
                            <button
                                onClick={exportExcel}
                                className="px-4 py-2 bg-emerald-600 text-white rounded-lg hover:bg-emerald-700 transition text-sm"
                            >
                                Exportar Excel
                            </button>
                        )}
                    </div>
                </div>
            </div>

            {viewMode === "operacao" && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="bg-blue-50 border border-blue-100 p-4 rounded-lg">
                    <h4 className="text-sm font-semibold text-blue-800 mb-1">Margem de Contribuição</h4>
                    <p className="text-xs text-blue-700">
                        Receita menos despesas variáveis e fixas rateadas. Mede quanto sobra para cobrir impostos e gerar lucro operacional.
                    </p>
                </div>
                <div className="bg-purple-50 border border-purple-100 p-4 rounded-lg">
                    <h4 className="text-sm font-semibold text-purple-800 mb-1">Participação no Lucro</h4>
                    <p className="text-xs text-purple-700">
                        Peso da receita da farmácia em relação ao total do mês. Usada para ratear despesas e impostos proporcionais.
                    </p>
                </div>
            </div>
            )}

            {viewMode === "consolidado" && (
                <div className="space-y-4">
                    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                        <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-4">
                            <div className="flex items-center justify-between mb-3">
                                <h3 className="text-sm font-semibold text-gray-800">Receita (Flux + Cooperativa)</h3>
                                <span className="text-xs text-gray-500">
                                    {filterMes ? `Mês: ${filterMes}` : "Período: todos os meses"}
                                </span>
                            </div>
                            <div className="h-72">
                                <ResponsiveContainer width="100%" height="100%">
                                    <BarChart data={consolidatedAggRows.slice(0, 12)} margin={{ left: 8, right: 8 }}>
                                        <CartesianGrid strokeDasharray="3 3" vertical={false} />
                                        <XAxis dataKey="farmacia" hide />
                                        <YAxis />
                                        <Tooltip formatter={(v) => formatCurrency(v)} />
                                        <Legend />
                                        <Bar dataKey="receita_flux" name="Flux" stackId="rec" fill="#10B981" />
                                        <Bar dataKey="receita_coop" name="Cooperativa" stackId="rec" fill="#3B82F6" />
                                    </BarChart>
                                </ResponsiveContainer>
                            </div>
                            <p className="text-[11px] text-gray-500 mt-2">
                                Top 12 farmácias por receita total no período filtrado.
                            </p>
                        </div>

                        <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-4">
                            <div className="flex items-center justify-between mb-3">
                                <h3 className="text-sm font-semibold text-gray-800">Despesas (operacionais + impostos)</h3>
                                <span className="text-xs text-gray-500">
                                    {filterMes ? `Mês: ${filterMes}` : "Período: todos os meses"}
                                </span>
                            </div>
                            <div className="h-72">
                                <ResponsiveContainer width="100%" height="100%">
                                    <BarChart data={consolidatedAggRows.slice(0, 12)} margin={{ left: 8, right: 8 }}>
                                        <CartesianGrid strokeDasharray="3 3" vertical={false} />
                                        <XAxis dataKey="farmacia" hide />
                                        <YAxis />
                                        <Tooltip formatter={(v) => formatCurrency(v)} />
                                        <Legend />
                                        <Bar dataKey="despesas_op_flux" name="Desp. Flux" stackId="desp" fill="#EF4444" />
                                        <Bar dataKey="despesas_op_coop" name="Desp. Coop" stackId="desp" fill="#F97316" />
                                        <Bar dataKey="imposto_total" name="Impostos" stackId="desp" fill="#6B7280" />
                                    </BarChart>
                                </ResponsiveContainer>
                            </div>
                            <p className="text-[11px] text-gray-500 mt-2">
                                Impostos seguem regra de competência (pagamento no mês seguinte). Se não houver, são estimados.
                            </p>
                        </div>
                    </div>

                    <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
                        <div className="px-4 py-3 bg-gradient-to-r from-emerald-50 to-blue-50 flex items-center justify-between">
                            <h3 className="text-sm font-semibold text-gray-800">Consolidado por Farmácia</h3>
                            <div className="flex items-center gap-3">
                                <select
                                    className="bg-white border border-gray-200 text-gray-700 text-xs rounded-lg focus:ring-blue-500 focus:border-blue-500 block py-1.5 px-2 outline-none"
                                    value={consolidadoSort}
                                    onChange={(e) => setConsolidadoSort(e.target.value)}
                                >
                                    <option value="receita_total_desc">Ordenar: Receita Total</option>
                                    <option value="repasse_desc">Ordenar: % Repasse (maior)</option>
                                    <option value="repasse_asc">Ordenar: % Repasse (menor)</option>
                                </select>
                                <span className="text-xs text-gray-600">
                                    {filterMes ? `Filtro: ${filterMes}` : "Filtro: todos os meses"} • {consolidatedAggRows.length} farmácias
                                </span>
                            </div>
                        </div>
                        <div className="overflow-x-auto">
                            <table className="min-w-full divide-y divide-gray-200">
                                <thead className="bg-white">
                                    <tr>
                                        <th className="px-2 py-2 text-left text-[10px] font-bold text-gray-700 uppercase">#</th>
                                        <th className="px-2 py-2 text-left text-[10px] font-bold text-gray-700 uppercase">Farmácia</th>
                                        <th className="px-2 py-2 text-right text-[10px] font-bold text-gray-700 uppercase">Fat. Flux</th>
                                        <th className="px-2 py-2 text-right text-[10px] font-bold text-gray-700 uppercase">Rec. Coop</th>
                                        <th className="px-2 py-2 text-right text-[10px] font-bold text-gray-700 uppercase">Rec. Total</th>
                                        <th className="px-2 py-2 text-right text-[10px] font-bold text-gray-700 uppercase">% Flux/Total</th>
                                        <th className="px-2 py-2 text-right text-[10px] font-bold text-gray-700 uppercase">% Repasse</th>
                                        <th className="px-2 py-2 text-right text-[10px] font-bold text-gray-700 uppercase">Desp. Flux</th>
                                        <th className="px-2 py-2 text-right text-[10px] font-bold text-gray-700 uppercase">Desp. Coop</th>
                                        <th className="px-2 py-2 text-right text-[10px] font-bold text-gray-700 uppercase">Impostos</th>
                                        <th className="px-2 py-2 text-right text-[10px] font-bold text-gray-700 uppercase">ROL</th>
                                        <th className="px-2 py-2 text-right text-[10px] font-bold text-gray-700 uppercase">Rentab. %</th>
                                        <th className="px-2 py-2 text-center text-[10px] font-bold text-gray-700 uppercase">Obs.</th>
                                    </tr>
                                </thead>
                                <tbody className="bg-white divide-y divide-gray-100">
                                    {consolidatedAggRows.length === 0 ? (
                                        <tr>
                                            <td colSpan="13" className="px-6 py-8 text-center text-gray-500">
                                                Nenhum dado disponível para os filtros selecionados.
                                            </td>
                                        </tr>
                                    ) : (
                                        consolidatedAggRows.map((row, idx) => {
                                            const heatmapClass = getHeatmapColor(row.rentabilidade ?? 0, 0, 100);
                                            return (
                                                <tr key={row.farmacia || idx} className="hover:bg-gray-50">
                                                    <td className="px-2 py-2 text-xs text-gray-600">{idx + 1}</td>
                                                    <td className="px-2 py-2 text-xs font-semibold text-gray-800 whitespace-nowrap">
                                                        {row.farmacia}
                                                    </td>
                                                    <td className="px-2 py-2 text-xs text-right text-emerald-700">{formatCurrency(row.receita_flux || 0)}</td>
                                                    <td className="px-2 py-2 text-xs text-right text-blue-700">{formatCurrency(row.receita_coop || 0)}</td>
                                                    <td className="px-2 py-2 text-xs text-right font-semibold text-gray-900">{formatCurrency(row.receita_total || 0)}</td>
                                                    <td className="px-2 py-2 text-xs text-right text-gray-700">
                                                        {(row.pct_flux_do_total ?? 0).toFixed(2)}%
                                                    </td>
                                                    <td className="px-2 py-2 text-xs text-right">
                                                        <span
                                                            className={`px-2 py-0.5 rounded-full text-[10px] font-bold ${getRepasseHeatmapClass(
                                                                row.repasse_entregador_pct ?? 0
                                                            )}`}
                                                        >
                                                            {(row.repasse_entregador_pct ?? 0).toFixed(2)}%
                                                        </span>
                                                    </td>
                                                    <td className="px-2 py-2 text-xs text-right text-red-700">{formatCurrency(row.despesas_op_flux || 0)}</td>
                                                    <td className="px-2 py-2 text-xs text-right text-orange-700">{formatCurrency(row.despesas_op_coop || 0)}</td>
                                                    <td className="px-2 py-2 text-xs text-right text-gray-700">{formatCurrency(row.imposto_total || 0)}</td>
                                                    <td className="px-2 py-2 text-xs text-right font-semibold text-gray-900">{formatCurrency(row.rol || 0)}</td>
                                                    <td className="px-2 py-2 text-xs text-right">
                                                        <span className={`px-2 py-0.5 rounded-full text-[10px] font-bold ${heatmapClass}`}>
                                                            {(row.rentabilidade ?? 0).toFixed(2)}%
                                                        </span>
                                                    </td>
                                                    <td className="px-2 py-2 text-center text-[10px] text-gray-700">
                                                        {row.imposto_estimado ? (
                                                            <span className="px-2 py-1 rounded bg-amber-50 text-amber-800 border border-amber-200">
                                                                imposto estimado
                                                            </span>
                                                        ) : (
                                                            <span className="text-gray-400">—</span>
                                                        )}
                                                    </td>
                                                </tr>
                                            );
                                        })
                                    )}
                                </tbody>
                            </table>
                        </div>
                    </div>
                </div>
            )}

            {viewMode === "operacao" && (
            <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
                <div className="overflow-x-auto">
                    <table className="min-w-full divide-y divide-gray-200">
                        <thead className="bg-gradient-to-r from-blue-50 to-indigo-50">
                            <tr>
                                <th className="px-2 py-2 text-left text-[10px] font-bold text-gray-700 uppercase">#</th>
                                <th className="px-2 py-2 text-left text-[10px] font-bold text-gray-700 uppercase">Farmácia</th>
                                <th className="px-2 py-2 text-left text-[10px] font-bold text-gray-700 uppercase">Mês</th>
                                <th className="px-2 py-2 text-right text-[10px] font-bold text-gray-700 uppercase">Receita</th>
                                <th className="px-2 py-2 text-right text-[10px] font-bold text-gray-700 uppercase">Desp. Rat.</th>
                                <th className="px-2 py-2 text-right text-[10px] font-bold text-gray-700 uppercase">Total Desp.</th>
                                <th className="px-2 py-2 text-right text-[10px] font-bold text-gray-700 uppercase">Margem %</th>
                                <th className="px-2 py-2 text-right text-[10px] font-bold text-gray-700 uppercase">ROL</th>
                                <th className="px-2 py-2 text-right text-[10px] font-bold text-gray-700 uppercase">Rentab. %</th>
                                <th className="px-2 py-2 text-right text-[10px] font-bold text-gray-700 uppercase">Part. %</th>
                                <th className="px-2 py-2 text-right text-[10px] font-bold text-gray-700 uppercase">Imposto</th>
                            </tr>
                        </thead>
                        <tbody className="bg-white divide-y divide-gray-200">
                            {filteredData.length === 0 ? (
                                <tr>
                                    <td colSpan="11" className="px-6 py-8 text-center text-gray-500">
                                        Nenhum dado disponivel para os filtros selecionados.
                                    </td>
                                </tr>
                            ) : (
                        filteredData.map((row, idx) => {
                            const rentabilidadeValor = row.rentabilidade_competencia ?? row.rentabilidade ?? 0;
                            const heatmapClass = getHeatmapColor(rentabilidadeValor, minRent, maxRent);
                            const evolColor = getEvolutionColor(row.rol_competencia ?? row.rol ?? 0);

                            const despesaRateada =
                                row.despesas_rateadas_operacionais ?? row.despesa_rateada_total ?? 0;
                            const impostoRateado = row.imposto_rateado_competencia ?? row.imposto_rateado ?? 0;
                            const totalDespesas =
                                row.despesa_total_competencia ?? row.despesa_total ?? despesaRateada + impostoRateado;

                            const isSelected = selectedFarmacia === row.farmacia;

                            return (
                                <tr
                                    key={idx}
                                    className={`hover:bg-gray-50 transition-colors cursor-pointer ${isSelected ? "bg-blue-50" : ""}`}
                                    onClick={() => setSelectedFarmacia(row.farmacia)}
                                    onMouseEnter={() => setHoverDetail(buildFarmaciaDetail(row.farmacia, row))}
                                    onMouseLeave={() => setHoverDetail(null)}
                                >
                                            <td className="px-2 py-2 whitespace-nowrap text-xs font-bold text-gray-900">{idx + 1}</td>
                                            <td className="px-2 py-2 whitespace-nowrap text-xs font-medium text-gray-900">{row.farmacia}</td>
                                            <td className="px-2 py-2 whitespace-nowrap text-xs text-gray-600">{row.mes_nome}</td>
                                            <td className="px-2 py-2 whitespace-nowrap text-xs text-right font-semibold text-green-600">
                                                {formatCurrency(row.receita_bruta)}
                                            </td>
                                            <td className="px-2 py-2 whitespace-nowrap text-xs text-right font-medium text-red-600">
                                                {formatCurrency(despesaRateada)}
                                            </td>
                                            <td className="px-2 py-2 whitespace-nowrap text-xs text-right font-medium text-red-600">
                                                {formatCurrency(totalDespesas)}
                                            </td>
                                            <td className="px-2 py-2 whitespace-nowrap text-xs text-right font-medium text-blue-600">
                                                {(row.margem_contribuicao_pct_competencia ?? row.margem_contribuicao_pct ?? 0).toFixed(2)}%
                                            </td>
                                            <td className={`px-2 py-2 whitespace-nowrap text-xs text-right font-medium ${evolColor}`}>
                                                {formatCurrency(row.rol_competencia ?? row.rol ?? 0)}
                                            </td>
                                            <td className="px-2 py-2 whitespace-nowrap text-xs text-right">
                                                <span className={`px-2 py-0.5 rounded-full text-[10px] font-bold ${heatmapClass}`}>
                                                    {(rentabilidadeValor || 0).toFixed(2)}%
                                                </span>
                                            </td>
                                            <td className="px-2 py-2 whitespace-nowrap text-xs text-right font-medium text-purple-600">
                                                {row.participacao_lucro?.toFixed(2) || "0.00"}%
                                            </td>
                                            <td className="px-2 py-2 whitespace-nowrap text-xs text-right text-gray-600">
                                                <div className="flex items-center justify-end gap-1">
                                                    {formatCurrency(impostoRateado)}
                                                    {row.imposto_estimado ? (
                                                        <span className="text-[9px] px-1 py-0.5 rounded bg-amber-100 text-amber-800">Est.</span>
                                                    ) : null}
                                                </div>
                                            </td>
                                        </tr>
                                    );
                                })
                            )}
                        </tbody>
                    </table>
                </div>
            </div>

            )}

            {viewMode === "operacao" && farmaciaDetail && (
                <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-5">
                    <div className="flex items-start justify-between gap-3 mb-4">
                        <div>
                            <h3 className="text-xl font-bold text-gray-800">{farmaciaDetail.farm}</h3>
                            <p className="text-sm text-gray-500">
                                Ano {farmaciaDetail.year || "atual"} • visão detalhada •{" "}
                                <span className="inline-flex items-center px-2 py-0.5 rounded-full bg-blue-100 text-blue-800 text-xs">
                                    Modo: Competência
                                </span>
                            </p>
                        </div>
                        <button
                            onClick={() => setSelectedFarmacia(null)}
                            className="px-3 py-2 text-sm bg-gray-100 text-gray-700 rounded-lg hover:bg-gray-200"
                        >
                            Fechar
                        </button>
                    </div>

                    <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-4">
                        <div className="bg-blue-50 border border-blue-100 rounded-lg p-3">
                            <p className="text-xs text-blue-700">Receita YTD</p>
                            <p className="text-lg font-bold text-blue-900">{formatCurrency(farmaciaDetail.receitaYtd)}</p>
                        </div>
                        <div className="bg-amber-50 border border-amber-100 rounded-lg p-3">
                            <p className="text-xs text-amber-700">Despesas Totais YTD</p>
                            <p className="text-lg font-bold text-amber-900">{formatCurrency(farmaciaDetail.despesasTotaisYtd)}</p>
                        </div>
                        <div className="bg-purple-50 border border-purple-100 rounded-lg p-3">
                            <p className="text-xs text-purple-700">Rentabilidade média</p>
                            <p className="text-lg font-bold text-purple-900">
                                {(farmaciaDetail.rentMed || 0).toFixed(2)}%
                            </p>
                        </div>
                        <div className="bg-green-50 border border-green-100 rounded-lg p-3">
                            <p className="text-xs text-green-700">Margem YTD</p>
                            <p className="text-lg font-bold text-green-900">
                                {(farmaciaDetail.margemPct || 0).toFixed(2)}%
                            </p>
                        </div>
                        <div className="bg-gray-50 border border-gray-100 rounded-lg p-3">
                            <p className="text-xs text-gray-600">Projeção receita anual</p>
                            <p className="text-lg font-bold text-gray-900">
                                {farmaciaDetail.projReceitaAnual ? formatCurrency(farmaciaDetail.projReceitaAnual) : "—"}
                            </p>
                        </div>
                    </div>

                    <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                        <div>
                            <div className="flex items-center justify-between mb-2">
                                <h4 className="text-sm font-semibold text-gray-800">Receita mensal ({farmaciaDetail.year || "ano"})</h4>
                                {farmaciaDetail.maiorMes && (
                                    <span className="text-xs text-gray-500">
                                        Pico: {farmaciaDetail.maiorMes.mes_nome} • Fundo: {farmaciaDetail.menorMes?.mes_nome}
                                    </span>
                                )}
                            </div>
                            <div className="space-y-2">
                                {farmaciaDetail.mensal.map((m) => {
                                    const widthPct =
                                        farmaciaDetail.maxReceita > 0
                                            ? Math.max((m.receita / farmaciaDetail.maxReceita) * 100, 4)
                                            : 4;
                                    return (
                                        <div key={m.mes_ano} className="flex items-center gap-2">
                                            <span className="w-16 text-xs text-gray-600">{m.mes_nome}</span>
                                            <div className="flex-1 bg-gray-100 h-2 rounded">
                                                <div
                                                    className="h-2 rounded bg-blue-500"
                                                    style={{ width: `${widthPct}%` }}
                                                ></div>
                                            </div>
                                            <span className="text-xs text-gray-700 w-24 text-right">
                                                {formatCurrency(m.receita)}
                                            </span>
                                        </div>
                                    );
                                })}
                            </div>
                        </div>

                        <div className="bg-gray-50 border border-gray-100 rounded-lg p-3">
                            <h4 className="text-sm font-semibold text-gray-800 mb-2">Indicadores</h4>
                            <ul className="text-sm text-gray-700 space-y-2">
                                <li>
                                    <span className="font-semibold">Imposto YTD: </span>
                                    {formatCurrency(farmaciaDetail.impostoYtd)}
                                </li>
                                <li>
                                    <span className="font-semibold">Maior mês de receita: </span>
                                    {farmaciaDetail.maiorMes
                                        ? `${farmaciaDetail.maiorMes.mes_nome} (${formatCurrency(farmaciaDetail.maiorMes.receita)})`
                                        : "—"}
                                </li>
                                <li>
                                    <span className="font-semibold">Menor mês de receita: </span>
                                    {farmaciaDetail.menorMes
                                        ? `${farmaciaDetail.menorMes.mes_nome} (${formatCurrency(farmaciaDetail.menorMes.receita)})`
                                        : "—"}
                                </li>
                                <li>
                                    <span className="font-semibold">Rentabilidade média do ano: </span>
                                    {(farmaciaDetail.rentMed || 0).toFixed(2)}%
                                </li>
                                <li>
                                    <span className="font-semibold">Projeção próxima receita: </span>
                                    {farmaciaDetail.projProxMes ? formatCurrency(farmaciaDetail.projProxMes) : "—"}
                                </li>
                            </ul>
                            <div className="mt-3">
                                <p className="text-xs font-semibold text-gray-700 mb-1">Sazonalidade média</p>
                                <div className="flex flex-wrap gap-2">
                                    {farmaciaDetail.sazPerc?.map((s, i) => (
                                        <div key={i} className="bg-white border border-gray-200 rounded px-2 py-1 text-[11px]">
                                            {s.mes}: {s.pct.toFixed(1)}%
                                        </div>
                                    ))}
                                </div>
                            </div>
                            <div className="mt-3">
                                <p className="text-xs font-semibold text-gray-700 mb-1">Projeção mensal (faixa)</p>
                                <p className="text-xs text-gray-800">
                                    Min: {farmaciaDetail.projMinMes ? formatCurrency(farmaciaDetail.projMinMes) : "—"} •
                                    Máx: {farmaciaDetail.projMaxMes ? formatCurrency(farmaciaDetail.projMaxMes) : "—"}
                                </p>
                            </div>
                            <div className="mt-3">
                                <p className="text-xs font-semibold text-gray-700 mb-1">Mix de Receitas</p>
                                <div className="flex flex-wrap gap-2 text-xs">
                                    <span className="px-2 py-1 rounded bg-blue-100 text-blue-800">
                                        Entregas: {formatCurrency(farmaciaDetail.mix?.entregas || 0)}
                                    </span>
                                    <span className="px-2 py-1 rounded bg-emerald-100 text-emerald-800">
                                        Setup: {formatCurrency(farmaciaDetail.mix?.setup || 0)}
                                    </span>
                                    <span className="px-2 py-1 rounded bg-amber-100 text-amber-800">
                                        Reembolso: {formatCurrency(farmaciaDetail.mix?.reembolso || 0)}
                                    </span>
                                    <span className="px-2 py-1 rounded bg-gray-100 text-gray-800">
                                        Outros: {formatCurrency(farmaciaDetail.mix?.outros || 0)}
                                    </span>
                                </div>
                            </div>
                            {farmaciaDetail.alerts?.length ? (
                                <div className="mt-3 flex flex-wrap gap-2">
                                    {farmaciaDetail.alerts.map((a, i) => (
                                        <span key={i} className="flex items-center gap-1 px-2 py-1 text-[11px] rounded bg-amber-50 text-amber-800 border border-amber-200">
                                            <AlertTriangle className="w-3 h-3" /> {a}
                                        </span>
                                    ))}
                                </div>
                            ) : null}
                        </div>
                    </div>

                    <div className="mt-4 flex flex-wrap gap-3">
                        <a
                            href="#"
                            onClick={(e) => {
                                e.preventDefault();
                                if (!financeData.length || !farmaciaDetail) return;
                                const norm = (s) =>
                                    (s || "")
                                        .normalize("NFD")
                                        .replace(/[\u0300-\u036f]/g, "")
                                        .toUpperCase();
                                const rows = financeData.filter(
                                    (f) =>
                                        norm(f.centro_custo) === norm(farmaciaDetail.farm) &&
                                        (f.mes_ano || "").startsWith(farmaciaDetail.year)
                                );
                                if (!rows.length) return;
                                const asCsv = rows.map((r) => ({
                                    mes_ano: r.mes_ano,
                                    data_mov: r.data_mov,
                                    centro_custo: r.centro_custo,
                                    plano_contas: r.plano_contas,
                                    entrada_saida: r.entrada_saida,
                                    valor: r.valor,
                                    tipo_custo: r.tipo_custo,
                                }));
                                const ws = XLSX.utils.json_to_sheet(asCsv);
                                const wb = XLSX.utils.book_new();
                                XLSX.utils.book_append_sheet(wb, ws, "Dados");
                                XLSX.writeFile(wb, `detalhe_${farmaciaDetail.farm}_${farmaciaDetail.year}.xlsx`);
                            }}
                            className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700"
                        >
                            Exportar dados da farmácia (CSV/Excel)
                        </a>
                        <a
                            href="#"
                            onClick={(e) => {
                                e.preventDefault();
                                const norm = (s) =>
                                    (s || "")
                                        .normalize("NFD")
                                        .replace(/[\u0300-\u036f]/g, "")
                                        .toUpperCase();
                                const rows = financeData.filter(
                                    (f) =>
                                        norm(f.centro_custo) === norm(farmaciaDetail.farm) &&
                                        (f.mes_ano || "").startsWith(farmaciaDetail.year)
                                );
                                if (!rows.length) return;
                                const escapeXml = (v) =>
                                    String(v ?? "")
                                        .replace(/&/g, "&amp;")
                                        .replace(/</g, "&lt;")
                                        .replace(/>/g, "&gt;")
                                        .replace(/\"/g, "&quot;")
                                        .replace(/'/g, "&apos;");
                                const xmlRows = rows
                                    .map(
                                        (r) =>
                                            `<item><mes_ano>${escapeXml(r.mes_ano)}</mes_ano><data_mov>${escapeXml(
                                                r.data_mov
                                            )}</data_mov><centro_custo>${escapeXml(
                                                r.centro_custo
                                            )}</centro_custo><plano_contas>${escapeXml(
                                                r.plano_contas
                                            )}</plano_contas><entrada_saida>${escapeXml(
                                                r.entrada_saida
                                            )}</entrada_saida><valor>${escapeXml(
                                                r.valor
                                            )}</valor><tipo_custo>${escapeXml(r.tipo_custo)}</tipo_custo></item>`
                                    )
                                    .join("");
                                const xml = `<?xml version="1.0" encoding="UTF-8"?><farmacia nome="${escapeXml(
                                    farmaciaDetail.farm
                                )}" ano="${escapeXml(farmaciaDetail.year)}">${xmlRows}</farmacia>`;
                                const blob = new Blob([xml], { type: "application/xml" });
                                const url = URL.createObjectURL(blob);
                                const a = document.createElement("a");
                                a.href = url;
                                a.download = `detalhe_${farmaciaDetail.farm}_${farmaciaDetail.year}.xml`;
                                a.click();
                                URL.revokeObjectURL(url);
                            }}
                            className="px-4 py-2 bg-indigo-600 text-white rounded-lg text-sm hover:bg-indigo-700"
                        >
                            Exportar XML
                        </a>
                    </div>
                </div>
            )}

            {viewMode === "operacao" && hoverDetail && (
                <div className="fixed inset-0 z-50 flex items-center justify-center pointer-events-none">
                    <div className="w-96 bg-white shadow-2xl border border-gray-200 rounded-lg p-4 pointer-events-auto">
                    <div className="flex items-center justify-between mb-2">
                        <div>
                            <h4 className="text-sm font-bold text-gray-800">{hoverDetail.farm}</h4>
                            <p className="text-xs text-gray-500">Ano {hoverDetail.year || "atual"} • visão rápida</p>
                        </div>
                        <button
                            onClick={() => setHoverDetail(null)}
                            className="text-gray-400 hover:text-gray-600 text-sm"
                        >
                            ×
                        </button>
                    </div>

                    <div className="grid grid-cols-2 gap-2 mb-3">
                        <div className="bg-blue-50 border border-blue-100 rounded p-2">
                            <p className="text-[11px] text-blue-700">Receita YTD</p>
                            <p className="text-sm font-bold text-blue-900">{formatCurrency(hoverDetail.receitaYtd)}</p>
                        </div>
                        <div className="bg-emerald-50 border border-emerald-100 rounded p-2">
                            <p className="text-[11px] text-emerald-700">Proj. Receita Ano</p>
                            <p className="text-sm font-bold text-emerald-900">
                                {hoverDetail.projReceitaAnual ? formatCurrency(hoverDetail.projReceitaAnual) : "—"}
                            </p>
                        </div>
                    </div>

                    <div className="mb-3">
                        <p className="text-xs font-semibold text-gray-700 mb-1">Receita mensal</p>
                                <div className="flex items-end gap-1 h-16">
                                    {hoverDetail.mensal.map((m, i) => {
                                        const hPct = hoverDetail.maxReceita > 0 ? Math.max((m.receita / hoverDetail.maxReceita) * 100, 4) : 4;
                                        const isMax = hoverDetail.maiorMes && hoverDetail.maiorMes.mes_nome === m.mes_nome;
                                        const isMin = hoverDetail.menorMes && hoverDetail.menorMes.mes_nome === m.mes_nome;
                                return (
                                    <div key={i} className="flex flex-col items-center">
                                        <div
                                            className={`w-4 rounded-t ${isMax ? "bg-green-500" : isMin ? "bg-red-400" : "bg-blue-400"}`}
                                            style={{ height: `${hPct}%` }}
                                        ></div>
                                        <span className="text-[10px] text-gray-500 mt-1">{m.mes_nome.slice(0,3)}</span>
                                    </div>
                                );
                            })}
                        </div>
                    </div>

                    <div className="mb-3">
                        <p className="text-xs font-semibold text-gray-700 mb-1">Sazonalidade média</p>
                        <div className="flex flex-wrap gap-1 text-[10px]">
                            {hoverDetail.sazPerc?.map((s, i) => (
                                <span key={i} className="px-2 py-1 rounded bg-gray-100 text-gray-700 border border-gray-200">
                                    {s.mes}: {s.pct.toFixed(1)}%
                                </span>
                            ))}
                        </div>
                    </div>

                    <div className="mb-3">
                        <p className="text-xs font-semibold text-gray-700 mb-1">Mix de Receitas</p>
                        <div className="flex flex-wrap gap-1 text-[10px]">
                            <span className="px-2 py-1 rounded bg-blue-100 text-blue-800">Entregas: {formatCurrency(hoverDetail.mix?.entregas || 0)}</span>
                            <span className="px-2 py-1 rounded bg-emerald-100 text-emerald-800">Setup: {formatCurrency(hoverDetail.mix?.setup || 0)}</span>
                            <span className="px-2 py-1 rounded bg-amber-100 text-amber-800">Reembolso: {formatCurrency(hoverDetail.mix?.reembolso || 0)}</span>
                            <span className="px-2 py-1 rounded bg-gray-100 text-gray-800">Outros: {formatCurrency(hoverDetail.mix?.outros || 0)}</span>
                        </div>
                        <p className="text-[10px] text-gray-600 mt-1">
                            Variação vs mês anterior:
                            <span
                                className={`ml-1 px-2 py-1 rounded-full ${
                                    hoverDetail.variacaoMes > 0
                                        ? "bg-green-100 text-green-800"
                                        : hoverDetail.variacaoMes < 0
                                        ? "bg-red-100 text-red-800"
                                        : "bg-gray-100 text-gray-800"
                                }`}
                            >
                                {hoverDetail.variacaoMes.toFixed(1)}%
                            </span>
                        </p>
                    </div>

                    <div className="mb-3">
                        <p className="text-xs font-semibold text-gray-700 mb-1">Despesas Rateadas</p>
                        <div className="flex flex-col gap-1 text-[11px]">
                            <div className="flex items-center gap-2">
                                <span className="w-16 text-gray-600">Variável</span>
                                <div className="flex-1 bg-gray-100 h-2 rounded">
                                    <div className="h-2 rounded bg-red-400" style={{ width: "100%" }}></div>
                                </div>
                                <span className="w-24 text-right text-gray-700">{formatCurrency(hoverDetail.despVarYtd || 0)}</span>
                            </div>
                            <div className="flex items-center gap-2">
                                <span className="w-16 text-gray-600">Fixa</span>
                                <div className="flex-1 bg-gray-100 h-2 rounded">
                                    <div className="h-2 rounded bg-orange-400" style={{ width: "100%" }}></div>
                                </div>
                                <span className="w-24 text-right text-gray-700">{formatCurrency(hoverDetail.despFixYtd || 0)}</span>
                            </div>
                            <div className="flex items-center gap-2">
                                <span className="w-16 text-gray-600">Imposto</span>
                                <div className="flex-1 bg-gray-100 h-2 rounded">
                                    <div className="h-2 rounded bg-gray-500" style={{ width: "100%" }}></div>
                                </div>
                                <span className="w-24 text-right text-gray-700">{formatCurrency(hoverDetail.impostoYtdCalc || 0)}</span>
                            </div>
                        </div>
                    </div>

                    {hoverDetail.alerts?.length ? (
                        <div className="flex flex-wrap gap-1">
                            {hoverDetail.alerts.map((a, i) => (
                                <span key={i} className="flex items-center gap-1 px-2 py-1 text-[10px] rounded bg-amber-50 text-amber-800 border border-amber-200">
                                    <AlertTriangle className="w-3 h-3" /> {a}
                                </span>
                            ))}
                        </div>
                    ) : null}
                    </div>
                </div>
            )}
        </div>
    );
};

export default FarmaciaView;

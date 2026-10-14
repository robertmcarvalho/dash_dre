import React, { useEffect, useState } from "react";
import {
    Bar,
    BarChart,
    CartesianGrid,
    Cell,
    Legend,
    Pie,
    PieChart,
    ResponsiveContainer,
    Tooltip,
    XAxis,
    YAxis,
} from "recharts";
import { DollarSign, Filter, Percent, TrendingDown, TrendingUp, Users, Wallet } from "lucide-react";
import Card from "./components/Card";
import api from "./apiClient";
import {
    formatCurrency,
    formatMonthName,
    getUniqueYears,
    normalizeEntradaSaida,
    normalizeUpper,
} from "./utils/helpers";

const COLORS = [
    "#0088FE",
    "#00C49F",
    "#FFBB28",
    "#FF8042",
    "#8884d8",
    "#82ca9d",
    "#ffc658",
    "#ff6b9d",
    "#6366F1",
    "#F97316",
    "#14B8A6",
    "#A855F7",
    "#DB2777",
    "#0EA5E9",
    "#D97706",
    "#10B981",
    "#EF4444",
    "#22C55E",
];

const GlobalView = ({ context = "flux" }) => {
    const [data, setData] = useState([]);
    const [kpis, setKpis] = useState({
        receita: 0,
        despesa: 0,
        lucroOperacional: 0,
        margem: 0,
        saldoAcumulado: 0,
        distribuicao: 0,
        distribuicaoSocios: 0,
        distribuicaoFuncionarios: 0,
        distribuicaoMarcelo: 0,
        distribuicaoLiliane: 0,
    });

    const [expenseKpis, setExpenseKpis] = useState({
        total: 0,
        pessoal: 0,
        impostos: 0,
        servicos: 0,
    });

    const [chartData, setChartData] = useState([]);
    const [receitaPorAtividade, setReceitaPorAtividade] = useState([]);
    const [top5Despesas, setTop5Despesas] = useState([]);
    const [top5Receitas, setTop5Receitas] = useState([]);
    const [expenseComposition, setExpenseComposition] = useState([]);
    const [expenseEvolution, setExpenseEvolution] = useState([]);
    const [fixedVsVariable, setFixedVsVariable] = useState([]);
    const [paretoData, setParetoData] = useState([]);

    const [loading, setLoading] = useState(true);
    const [selectedYear, setSelectedYear] = useState("");
    const [availableYears, setAvailableYears] = useState([]);
    const [coopResumo, setCoopResumo] = useState(null);

    useEffect(() => {
        fetchData();
    }, [context]);

    useEffect(() => {
        if (data.length > 0) {
            processData(data, selectedYear);
        }
        if (context === "coop") {
            fetchCoopResumo();
        } else {
            setCoopResumo(null);
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [selectedYear, data]);

    const pick = (item, keys) => {
        for (const k of keys) {
            if (item[k] !== undefined) return item[k];
        }
        return undefined;
    };

    const fetchData = async () => {
        setLoading(true);
        try {
            const response = await api.get("/api/dre/financeiro", { params: { context } });
            const incoming = response.data || [];
            setData(incoming);
            const years = getUniqueYears(incoming);
            setAvailableYears(years);
            const defaultYear = years[0] || "";
            setSelectedYear(defaultYear);
            processData(incoming, defaultYear);
        } catch (error) {
            console.error("Erro ao buscar dados:", error);
            setLoading(false);
        }
    };

    const fetchCoopResumo = async () => {
        if (context !== "coop") return;
        try {
            const resp = await api.get("/api/coop/resumo", {
                params: { ano: selectedYear || undefined },
            });
            setCoopResumo(resp.data);
        } catch (error) {
            console.error("Erro ao buscar resumo da cooperativa", error);
        }
    };

    const processData = (rawData, yearFilter) => {
        let filteredData = rawData;
        if (yearFilter) {
            filteredData = rawData.filter((item) =>
                (item.MES_ANO || item.mes_ano || "").startsWith(yearFilter),
            );
        }

        let receita = 0;
        let despesa = 0;
        let distribuicaoLucro = 0;
        let distribuicaoSocios = 0;
        let distribuicaoFuncionarios = 0;
        let distribuicaoMarcelo = 0;
        let distribuicaoLiliane = 0;
        let despesaPessoal = 0;
        let despesaImpostos = 0;
        let despesaServicos = 0;

        const monthlyData = {};
        const receitasMap = {};
        const despesasMap = {};
        const expenseByMonth = {};
        const fixedVsVarByMonth = {};

        filteredData.forEach((item) => {
            const valorRaw =
                pick(item, ["valor", "VALOR MOV.CONTA", "valor_mov_conta"]) || 0;
            const valor = Number.isFinite(Number(valorRaw)) ? Number(valorRaw) : 0;

            const tipoRaw = pick(item, [
                "entrada_saida",
                "ENTRADA/SAIDA",
                "ENTRADA/SAÍDA",
                "ENTRADA/SA?DA",
                "ENTRADA/SA�DA",
            ]);
            const tipo = (normalizeEntradaSaida(tipoRaw) || "").trim();

            const mesAno = pick(item, ["mes_ano", "MES_ANO"]) || "";
            const mesNome = pick(item, ["mes_nome", "MES_NOME"]) || formatMonthName(mesAno);
            const mesKey = mesAno || mesNome || "N/A";

            const planoContas = pick(item, ["plano_contas", "PLANO DE CONTAS"]) || "Outros";
            const planoUpper = normalizeUpper(planoContas);
            const tipoCustoNorm = normalizeUpper(pick(item, ["tipo_custo", "TIPO_CUSTO"]) || "");
            const parceiro = pick(item, ["parceiro", "PARCEIRO"]) || "";
            const parceiroUpper = normalizeUpper(parceiro);
            const isLiliane = parceiroUpper.includes("LILIANE");
            const isMarcelo = parceiroUpper.includes("MARCELO");
            const isSocio = isLiliane || isMarcelo;
            const isDistribuicaoPlr = tipoCustoNorm === "DISTRIBUICAO_PLR";

            const isTransfer =
                tipoCustoNorm === "TRANSFERENCIA" || planoUpper.includes("TRANSFERENCIA");
            const isDistribLucro =
                tipoCustoNorm === "DISTRIBUICAO" ||
                planoUpper.includes("DISTRIBUICAO DE LUCRO") ||
                planoUpper.includes("DISTRIBUICAO DE LUCROS");
            const isReceitaCategoria = planoUpper.startsWith("RECEITAS ");

            if (!monthlyData[mesKey]) {
                monthlyData[mesKey] = {
                    name: mesNome,
                    mesAno,
                    receita: 0,
                    despesa: 0,
                    lucro: 0,
                };
            }

            if (tipo === "ENTRADA") {
                if (!isTransfer && !isDistribLucro) {
                    receita += valor;
                    monthlyData[mesKey].receita += valor;
                    receitasMap[planoContas] = (receitasMap[planoContas] || 0) + valor;
                }
                return;
            }

            if (tipo === "SAIDA") {
                const valorAbs = Math.abs(valor);

                if (isDistribLucro) {
                    if (isDistribuicaoPlr) {
                        return;
                    }
                    distribuicaoLucro += valorAbs;
                    if (isMarcelo) {
                        distribuicaoMarcelo += valorAbs;
                        distribuicaoSocios += valorAbs;
                    } else if (isLiliane) {
                        distribuicaoLiliane += valorAbs;
                        distribuicaoSocios += valorAbs;
                    } else {
                        distribuicaoFuncionarios += valorAbs;
                    }
                    return;
                }
                if (isTransfer || isReceitaCategoria) {
                    return;
                }

                despesa += valorAbs;
                monthlyData[mesKey].despesa += valorAbs;

                despesasMap[planoContas] = (despesasMap[planoContas] || 0) + valorAbs;

                if (planoUpper.includes("PRO LABORE") || planoUpper.includes("FUNCIONARIO")) {
                    despesaPessoal += valorAbs;
                } else if (planoUpper.includes("SIMPLES NACIONAL")) {
                    despesaImpostos += valorAbs;
                } else if (planoUpper.includes("COMISSOES")) {
                    despesaServicos += valorAbs; // inclui comissoes dentro de servicos
                } else if (planoUpper.includes("PUBLICIDADE") || planoUpper.includes("SOFTWARE")) {
                    despesaServicos += valorAbs;
                }

                expenseByMonth[mesKey] = (expenseByMonth[mesKey] || 0) + valorAbs;

                if (!fixedVsVarByMonth[mesKey]) {
                    fixedVsVarByMonth[mesKey] = {
                        name: mesNome,
                        mesAno,
                        Fixo: 0,
                        Variavel: 0,
                    };
                }

                if (tipoCustoNorm === "FIXO") {
                    fixedVsVarByMonth[mesKey].Fixo += valorAbs;
                } else {
                    fixedVsVarByMonth[mesKey].Variavel += valorAbs;
                }
                return;
            }

            if (isDistribLucro) {
                distribuicaoLucro += Math.abs(valor);
            }
        });

        const lucroOperacional = receita - despesa;
        const margem = receita > 0 ? (lucroOperacional / receita) * 100 : 0;
        const saldoAcumulado = lucroOperacional;

        const chartDataArr = Object.values(monthlyData).sort((a, b) =>
            (a.mesAno || "").localeCompare(b.mesAno || ""),
        );
        chartDataArr.forEach((d) => {
            d.lucro = d.receita - d.despesa;
        });

        const sortedReceitas = Object.entries(receitasMap)
            .map(([name, value]) => ({ name, value }))
            .sort((a, b) => b.value - a.value);

        const sortedDespesas = Object.entries(despesasMap)
            .map(([name, value]) => ({ name, value }))
            .sort((a, b) => b.value - a.value);

        const expenseComp = sortedDespesas.slice(0, 20);

        const expenseEvol = Object.entries(expenseByMonth)
            .map(([mesKey, value]) => {
                const info = monthlyData[mesKey];
                return { name: info?.name || mesKey, mesAno: info?.mesAno || mesKey, despesa: value };
            })
            .sort((a, b) => (a.mesAno || "").localeCompare(b.mesAno || ""));

        const fixedVsVar = Object.values(fixedVsVarByMonth).sort((a, b) =>
            (a.mesAno || "").localeCompare(b.mesAno || ""),
        );

        setKpis({
            receita,
            despesa,
            lucroOperacional,
            margem,
            saldoAcumulado,
            distribuicao: distribuicaoLucro,
            distribuicaoSocios,
            distribuicaoFuncionarios,
            distribuicaoMarcelo,
            distribuicaoLiliane,
        });
        setExpenseKpis({
            total: despesa,
            pessoal: despesaPessoal,
            impostos: despesaImpostos,
            servicos: despesaServicos,
        });

        setChartData(chartDataArr);
        setReceitaPorAtividade(sortedReceitas);
        setTop5Receitas(sortedReceitas.slice(0, 5));
        setTop5Despesas(sortedDespesas.slice(0, 5));
        setExpenseComposition(expenseComp);
        setExpenseEvolution(expenseEvol);
        setFixedVsVariable(fixedVsVar);
        setParetoData(sortedDespesas.slice(0, 10));
        setLoading(false);
    };

    if (loading) {
        return (
            <div className="flex items-center justify-center h-screen">
                <div className="animate-spin rounded-full h-32 w-32 border-b-2 border-blue-600"></div>
            </div>
        );
    }

    return (
        <div className="space-y-6 p-6 bg-gray-50 min-h-screen">
            <div className="flex justify-between items-center">
                <h2 className="text-2xl font-bold text-gray-800">Visao Global</h2>
                <div className="flex items-center gap-2">
                    <Filter className="w-5 h-5 text-gray-500" />
                    <select
                        className="bg-white border border-gray-200 text-gray-700 text-sm rounded-lg focus:ring-blue-500 focus:border-blue-500 block p-2.5 outline-none"
                        value={selectedYear}
                        onChange={(e) => setSelectedYear(e.target.value)}
                    >
                        <option value="">Todos os Anos</option>
                        {availableYears.map((year) => (
                            <option key={year} value={year}>
                                {year}
                            </option>
                        ))}
                    </select>
                </div>
            </div>

            {context === "coop" && coopResumo && (
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
                    <Card title="Entrada de Cotas" icon={TrendingUp} borderColor="border-emerald-500">
                        <div className="text-xl font-bold text-emerald-600">
                            {formatCurrency(coopResumo.entradas_cota || 0)}
                        </div>
                        <div className="text-xs text-gray-500">Capital social aportado</div>
                    </Card>
                    <Card title="Saída de Cotas" icon={TrendingDown} borderColor="border-amber-500">
                        <div className="text-xl font-bold text-amber-600">
                            {formatCurrency(Math.abs(coopResumo.saidas_cota || 0))}
                        </div>
                        <div className="text-xs text-gray-500">Resgates/devoluções</div>
                    </Card>
                    <Card title="Saldo Capital Social" icon={Wallet} borderColor="border-indigo-500">
                        <div className="text-xl font-bold text-indigo-600">
                            {formatCurrency(coopResumo.saldo_cota || 0)}
                        </div>
                        <div className="text-xs text-gray-500">Entradas - Saídas de cotas</div>
                    </Card>
                    <Card title="Adiantamentos do Mês" icon={DollarSign} borderColor="border-blue-500">
                        <div className="text-sm text-gray-700">
                            Adiantado:{" "}
                            <span className="font-semibold text-blue-600">
                                {formatCurrency(coopResumo.adiantado || 0)}
                            </span>
                        </div>
                        <div className="text-sm text-gray-700">
                            Reembolsado:{" "}
                            <span className="font-semibold text-blue-600">
                                {formatCurrency(coopResumo.reembolsado || 0)}
                            </span>
                        </div>
                        <div className="text-sm text-gray-700">
                            Saldo:{" "}
                            <span className="font-semibold text-blue-600">
                                {formatCurrency(coopResumo.saldo_adiantamentos || 0)}
                            </span>
                        </div>
                    </Card>
                </div>
            )}

            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-4">
                <Card title="Receita Total" icon={TrendingUp} borderColor="border-green-500">
                    <div className="text-2xl font-bold text-green-600">
                        {formatCurrency(kpis.receita)}
                    </div>
                    <div className="text-sm text-gray-500">Entradas operacionais</div>
                </Card>
                <Card title="Despesas Totais" icon={TrendingDown} borderColor="border-red-500">
                    <div className="text-2xl font-bold text-red-600">
                        {formatCurrency(kpis.despesa)}
                    </div>
                    <div className="text-sm text-gray-500">Saidas operacionais</div>
                </Card>
                <Card title="Lucro Operacional" icon={DollarSign} borderColor="border-blue-500">
                    <div
                        className={`text-2xl font-bold ${kpis.lucroOperacional >= 0 ? "text-blue-600" : "text-red-600"
                            }`}
                    >
                        {formatCurrency(kpis.lucroOperacional)}
                    </div>
                    <div className="text-sm text-gray-500">Receita - Despesas</div>
                </Card>
                <Card title="Margem Operacional" icon={Percent} borderColor="border-purple-500">
                    <div className="text-2xl font-bold text-purple-600">
                        {(kpis.margem ?? 0).toFixed(2)}%
                    </div>
                    <div className="text-sm text-gray-500">Eficiencia operacional</div>
                </Card>
                <Card title="Saldo Acumulado" icon={Wallet} borderColor="border-indigo-500">
                    <div className="text-2xl font-bold text-indigo-600">
                        {formatCurrency(kpis.saldoAcumulado)}
                    </div>
                    <div className="text-sm text-gray-500">No periodo</div>
                </Card>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-100">
                    <h3 className="text-lg font-semibold text-gray-800 mb-4">
                        Evolucao Mensal (Receita x Despesa)
                    </h3>
                    <div className="h-80">
                        <ResponsiveContainer width="100%" height="100%">
                            <BarChart data={chartData}>
                                <CartesianGrid strokeDasharray="3 3" vertical={false} />
                                <XAxis dataKey="name" />
                                <YAxis />
                                <Tooltip formatter={(value) => formatCurrency(value)} />
                                <Legend />
                                <Bar dataKey="receita" name="Receita" fill="#10B981" radius={[4, 4, 0, 0]} />
                                <Bar dataKey="despesa" name="Despesa" fill="#EF4444" radius={[4, 4, 0, 0]} />
                            </BarChart>
                        </ResponsiveContainer>
                    </div>
                </div>

                <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-100">
                    <h3 className="text-lg font-semibold text-gray-800 mb-4">Receita por Tipo de Atividade</h3>
                    <div className="h-80">
                        <ResponsiveContainer width="100%" height="100%">
                            <PieChart>
                                <Pie
                                    data={receitaPorAtividade.slice(0, 6)}
                                    cx="50%"
                                    cy="50%"
                                    labelLine={false}
                                    outerRadius={100}
                                    fill="#8884d8"
                                    dataKey="value"
                                >
                                    {receitaPorAtividade.slice(0, 6).map((entry, index) => (
                                        <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
                                    ))}
                                </Pie>
                                <Tooltip formatter={(value) => formatCurrency(value)} />
                                <Legend />
                            </PieChart>
                        </ResponsiveContainer>
                    </div>
                </div>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-100">
                    <h3 className="text-lg font-semibold text-gray-800 mb-4">Top 5 Receitas</h3>
                    <div className="space-y-4">
                        {top5Receitas.map((item, index) => (
                            <div
                                key={index}
                                className="flex items-center justify-between p-3 bg-green-50 rounded-lg"
                            >
                                <span className="text-sm font-medium text-gray-700 truncate w-1/2" title={item.name}>
                                    {item.name}
                                </span>
                                <span className="text-sm font-bold text-green-600">
                                    {formatCurrency(item.value)}
                                </span>
                            </div>
                        ))}
                    </div>
                </div>

                <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-100">
                    <h3 className="text-lg font-semibold text-gray-800 mb-4">Top 5 Despesas</h3>
                    <div className="space-y-4">
                        {top5Despesas.map((item, index) => (
                            <div key={index} className="flex items-center justify-between p-3 bg-red-50 rounded-lg">
                                <span className="text-sm font-medium text-gray-700 truncate w-1/2" title={item.name}>
                                    {item.name}
                                </span>
                                <span className="text-sm font-bold text-red-600">{formatCurrency(item.value)}</span>
                            </div>
                        ))}
                    </div>
                </div>
            </div>

            {kpis.distribuicao > 0 && (
                <div className="space-y-3">
                    <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                        <Card title="Distribuicao Lucros - Socios" icon={Users} color="green">
                            <div className="text-2xl font-bold text-green-600">
                                {formatCurrency(kpis.distribuicaoSocios)}
                            </div>
                            <div className="text-xs text-gray-500">Total socios</div>
                        </Card>
                        <Card title="Distribuicao Lucros - Marcelo" icon={Users} color="green">
                            <div className="text-2xl font-bold text-green-600">
                                {formatCurrency(kpis.distribuicaoMarcelo)}
                            </div>
                        </Card>
                        <Card title="Distribuicao Lucros - Liliane" icon={Users} color="green">
                            <div className="text-2xl font-bold text-green-600">
                                {formatCurrency(kpis.distribuicaoLiliane)}
                            </div>
                        </Card>
                        <Card title="Distribuicao Lucros - Funcionarios" icon={Users} color="purple">
                            <div className="text-2xl font-bold text-purple-600">
                                {formatCurrency(kpis.distribuicaoFuncionarios)}
                            </div>
                            <div className="text-xs text-gray-500">Demais beneficiarios</div>
                        </Card>
                    </div>
                    <div className="bg-blue-50 p-3 rounded-lg border border-blue-100 text-center">
                        <span className="text-blue-800 font-medium">Total distribuido no periodo:</span>
                        <span className="text-blue-900 font-bold text-lg ml-2">
                            {formatCurrency(kpis.distribuicao)}
                        </span>
                        <span className="text-xs text-blue-600 block mt-1">
                            (Nao deduzido do Resultado Liquido acima)
                        </span>
                    </div>
                </div>
            )}
        </div>
    );
};

export default GlobalView;

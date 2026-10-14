import React, { useEffect, useState } from "react";
import {
    Bar,
    BarChart,
    CartesianGrid,
    Cell,
    Legend,
    Line,
    LineChart,
    Pie,
    PieChart,
    ResponsiveContainer,
    Tooltip,
    XAxis,
    YAxis,
} from "recharts";
import { Briefcase, Calendar, DollarSign, FileText, Filter, Users } from "lucide-react";
import Card from "./components/Card";
import api from "./apiClient";
import {
    formatCurrency,
    formatMonthName,
    getUniqueYears,
    normalizeEntradaSaida,
    normalizeUpper,
} from "./utils/helpers";

const MONTH_OPTIONS = [
    { value: "01", label: "Janeiro" },
    { value: "02", label: "Fevereiro" },
    { value: "03", label: "Marco" },
    { value: "04", label: "Abril" },
    { value: "05", label: "Maio" },
    { value: "06", label: "Junho" },
    { value: "07", label: "Julho" },
    { value: "08", label: "Agosto" },
    { value: "09", label: "Setembro" },
    { value: "10", label: "Outubro" },
    { value: "11", label: "Novembro" },
    { value: "12", label: "Dezembro" },
];

const COLOR_PALETTE = [
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

const DespesasView = ({ context = "flux" }) => {
    const [data, setData] = useState([]);
    const [loading, setLoading] = useState(true);

    const [selectedYear, setSelectedYear] = useState("");
    const [selectedMonth, setSelectedMonth] = useState("");
    const [availableYears, setAvailableYears] = useState([]);
    const [availableMonths, setAvailableMonths] = useState(MONTH_OPTIONS);

    const [expenseKpis, setExpenseKpis] = useState({
        total: 0,
        pessoal: 0,
        impostos: 0,
        servicos: 0,
        comissoes: 0,
    });

    const [expenseComposition, setExpenseComposition] = useState([]);
    const [expenseEvolution, setExpenseEvolution] = useState([]);
    const [fixedVsVariable, setFixedVsVariable] = useState([]);
    const [paretoData, setParetoData] = useState([]);
    const [expenseTable, setExpenseTable] = useState([]);

    useEffect(() => {
        fetchData();
    }, [context]);

    useEffect(() => {
        if (data.length > 0) {
            processData(data);
        }
    }, [data, selectedYear, selectedMonth]);

    const pick = (item, keys) => {
        for (const k of keys) {
            if (item[k] !== undefined) return item[k];
        }
        return undefined;
    };

    const fetchData = async () => {
        try {
            const response = await api.get("/api/dre/financeiro", { params: { context } });
            const incoming = response.data || [];
            setData(incoming);
            const years = getUniqueYears(incoming);
            setAvailableYears(years);
            // Restringe o seletor aos meses que realmente possuem dados
            const monthSet = new Set();
            incoming.forEach((item) => {
                const mesAno = pick(item, ["MES_ANO", "mes_ano"]) || "";
                const mes = mesAno.split("-")[1];
                if (mes) monthSet.add(mes);
            });
            const filteredMonths =
                monthSet.size > 0 ? MONTH_OPTIONS.filter((m) => monthSet.has(m.value)) : MONTH_OPTIONS;
            setAvailableMonths(filteredMonths);
            if (filteredMonths.length > 0 && selectedMonth && !monthSet.has(selectedMonth)) {
                setSelectedMonth("");
            }
            if (years.length > 0) {
                setSelectedYear((prev) => prev || years[0]);
            }
        } catch (error) {
            console.error("Erro ao buscar dados:", error);
        } finally {
            setLoading(false);
        }
    };

    const processData = (rawData) => {
        let filteredData = rawData.filter((item) => {
            const tipoRaw = pick(item, [
                "entrada_saida",
                "ENTRADA/SAIDA",
                "ENTRADA/SAÍDA",
                "ENTRADA/SA?DA",
                "ENTRADA/SA�DA",
            ]);
            return (normalizeEntradaSaida(tipoRaw) || "").trim() === "SAIDA";
        });

        // Lógica de Competência para Impostos (Simples Nacional ou ISS no contexto coop)
        // Se um mês for selecionado (ex: Setembro), impostos devem ser do mês seguinte (Outubro)
        // Para outras despesas, mantém o mês selecionado

        let targetMonthStr = null;
        let targetTaxMonthStr = null;

        if (selectedYear && selectedMonth) {
            targetMonthStr = `${selectedYear}-${selectedMonth}`;

            // Calcular próximo mês para imposto (Competência)
            const [y, m] = targetMonthStr.split('-').map(Number);
            const date = new Date(y, m - 1 + 1, 1); // +1 mês
            const nextY = date.getFullYear();
            const nextM = String(date.getMonth() + 1).padStart(2, '0');
            targetTaxMonthStr = `${nextY}-${nextM}`;
        }

        filteredData = filteredData.filter((item) => {
            const mesAno = pick(item, ["MES_ANO", "mes_ano"]) || "";
            const planoContas = pick(item, ["PLANO DE CONTAS", "plano_contas"]) || "";
            const planoUpper = normalizeUpper(planoContas);
            const isTax =
                context === "coop"
                    ? planoUpper.includes("ISS")
                    : planoUpper.includes("SIMPLES NACIONAL");

            if (targetMonthStr) {
                // Visão Mensal: Lógica Híbrida
                if (isTax) {
                    return mesAno === targetTaxMonthStr;
                } else {
                    return mesAno === targetMonthStr;
                }
            } else if (selectedYear) {
                // Visão Anual: Manter filtro simples por ano (Caixa)
                return mesAno.startsWith(selectedYear);
            }

            return true;
        });

        let total = 0;
        let pessoal = 0;
        let impostos = 0;
        let servicos = 0;
        let comissoes = 0;

        const despesasMap = {};
        const expenseByMonth = {};
        const fixedVsVarByMonth = {};
        const tipoPorCategoria = {};

        filteredData.forEach((item) => {
            const valorRaw =
                pick(item, ["valor", "VALOR MOV.CONTA", "valor_mov_conta"]) || 0;
            const valor = Number.isFinite(Number(valorRaw)) ? Math.abs(Number(valorRaw)) : 0;

            const planoContas = pick(item, ["PLANO DE CONTAS", "plano_contas"]) || "Outros";
            const tipoCustoNorm = normalizeUpper(pick(item, ["tipo_custo", "TIPO_CUSTO"]) || "");
            const tipoCusto = tipoCustoNorm === "FIXO" ? "Fixo" : "Variavel";
            const mesAno = pick(item, ["MES_ANO", "mes_ano"]);
            const mesNome = pick(item, ["MES_NOME", "mes_nome"]) || formatMonthName(mesAno);
            const mesKey = mesAno || mesNome || "N/A";

            const planoUpper = normalizeUpper(planoContas);
            const isTransfer =
                tipoCustoNorm === "TRANSFERENCIA" || planoUpper.includes("TRANSFERENCIA");
            const isDistribLucro =
                tipoCustoNorm === "DISTRIBUICAO" ||
                planoUpper.includes("DISTRIBUICAO DE LUCRO") ||
                planoUpper.includes("DISTRIBUICAO DE LUCROS");
            const isReceitaCategoria = planoUpper.startsWith("RECEITAS ");

            if (isTransfer || isDistribLucro || isReceitaCategoria) {
                return;
            }

            total += valor;

            if (planoUpper.includes("PRO LABORE") || planoUpper.includes("FUNCIONARIO")) {
                pessoal += valor;
            } else if (
                (context === "coop" && planoUpper.includes("ISS")) ||
                (context !== "coop" && planoUpper.includes("SIMPLES NACIONAL"))
            ) {
                impostos += valor;
            } else if (planoUpper.includes("COMISSOES")) {
                comissoes += valor;
            } else if (planoUpper.includes("PUBLICIDADE") || planoUpper.includes("SOFTWARE")) {
                servicos += valor;
            }

            despesasMap[planoContas] = (despesasMap[planoContas] || 0) + valor;
            tipoPorCategoria[planoContas] = tipoCusto;

            if (mesKey) {
                if (!expenseByMonth[mesKey]) {
                    expenseByMonth[mesKey] = { name: mesNome, mesAno: mesAno || mesKey, despesa: 0 };
                }
                expenseByMonth[mesKey].despesa += valor;

                if (!fixedVsVarByMonth[mesKey]) {
                    fixedVsVarByMonth[mesKey] = {
                        name: mesNome,
                        mesAno: mesAno || mesKey,
                        Fixo: 0,
                        Variavel: 0,
                    };
                }
                fixedVsVarByMonth[mesKey][tipoCusto] += valor;
            }
        });

        const compositionData = Object.entries(despesasMap)
            .map(([name, value]) => ({ name, value, tipo: tipoPorCategoria[name] || "Variavel" }))
            .sort((a, b) => b.value - a.value);

        const top10 = compositionData.slice(0, 10);
        const outrosTotal = compositionData.slice(10).reduce((acc, cur) => acc + cur.value, 0);
        const compositionDisplay =
            outrosTotal > 0 ? [...top10, { name: "Outros", value: outrosTotal, tipo: "Variavel" }] : top10;

        const evolutionData = Object.values(expenseByMonth).sort((a, b) =>
            (a.mesAno || "").localeCompare(b.mesAno || ""),
        );

        const fixedVarData = Object.values(fixedVsVarByMonth).sort((a, b) =>
            (a.mesAno || "").localeCompare(b.mesAno || ""),
        );

        const pareto = compositionData.slice(0, 10);
        const allTable = compositionData;

        setExpenseKpis({ total, pessoal, impostos, servicos, comissoes });
        setExpenseComposition(compositionDisplay);
        setExpenseEvolution(evolutionData);
        setFixedVsVariable(fixedVarData);
        setParetoData(pareto);
        setExpenseTable(allTable);
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
            <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-100 flex flex-col md:flex-row justify-between items-center gap-4">
                <h2 className="text-2xl font-bold text-gray-800 flex items-center gap-2">
                    <Filter className="w-6 h-6 text-blue-500" />
                    Detalhamento de Despesas
                </h2>

                <div className="flex gap-3">
                    <div className="relative">
                        <Calendar className="absolute left-3 top-2.5 h-4 w-4 text-gray-400" />
                        <select
                            className="pl-9 bg-gray-50 border border-gray-200 text-gray-700 text-sm rounded-lg focus:ring-blue-500 focus:border-blue-500 block p-2.5 outline-none"
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
                    <select
                        className="bg-gray-50 border border-gray-200 text-gray-700 text-sm rounded-lg focus:ring-blue-500 focus:border-blue-500 block p-2.5 outline-none"
                        value={selectedMonth}
                        onChange={(e) => setSelectedMonth(e.target.value)}
                    >
                        <option value="">Todos os Meses</option>
                        {availableMonths.map((month) => (
                            <option key={month.value} value={month.value}>
                                {month.label}
                            </option>
                        ))}
                    </select>
                </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-4">
                <Card title="Despesa Total" icon={DollarSign} color="red">
                    <div className="text-2xl font-bold text-red-600">{formatCurrency(expenseKpis.total)}</div>
                    <div className="text-sm text-gray-500">Saidas operacionais</div>
                </Card>
                <Card title="Despesa Pessoal" icon={Users} color="orange">
                    <div className="text-2xl font-bold text-orange-600">{formatCurrency(expenseKpis.pessoal)}</div>
                    <div className="text-xs text-gray-500">Pro-labore + Funcionarios</div>
                </Card>
                <Card title="Impostos" icon={FileText} color="yellow">
                    <div className="text-2xl font-bold text-yellow-600">{formatCurrency(expenseKpis.impostos)}</div>
                    <div className="text-xs text-gray-500">
                        {context === "coop" ? "ISS" : "Simples Nacional"}
                    </div>
                </Card>
                <Card title="Servicos" icon={Briefcase} color="purple">
                    <div className="text-2xl font-bold text-purple-600">{formatCurrency(expenseKpis.servicos)}</div>
                    <div className="text-xs text-gray-500">Publicidade + Software</div>
                </Card>
                <Card title="Comissoes" icon={DollarSign} color="blue">
                    <div className="text-2xl font-bold text-blue-600">{formatCurrency(expenseKpis.comissoes)}</div>
                    <div className="text-xs text-gray-500">Comissoes de representantes</div>
                </Card>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-100">
                    <h3 className="text-lg font-semibold text-gray-800 mb-4">Evolucao da Despesa Total</h3>
                    <div className="h-80">
                        <ResponsiveContainer width="100%" height="100%">
                            <LineChart data={expenseEvolution}>
                                <CartesianGrid strokeDasharray="3 3" />
                                <XAxis dataKey="name" />
                                <YAxis />
                                <Tooltip formatter={(value) => formatCurrency(value)} />
                                <Legend />
                                <Line type="monotone" dataKey="despesa" name="Despesa" stroke="#EF4444" strokeWidth={2} />
                            </LineChart>
                        </ResponsiveContainer>
                    </div>
                </div>

                <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-100">
                    <h3 className="text-lg font-semibold text-gray-800 mb-4">Despesa Fixa vs Variavel</h3>
                    <div className="h-80">
                        <ResponsiveContainer width="100%" height="100%">
                            <BarChart data={fixedVsVariable}>
                                <CartesianGrid strokeDasharray="3 3" vertical={false} />
                                <XAxis dataKey="name" />
                                <YAxis />
                                <Tooltip formatter={(value) => formatCurrency(value)} />
                                <Legend />
                                <Bar dataKey="Fixo" stackId="a" fill="#8B5CF6" />
                                <Bar dataKey="Variavel" stackId="a" fill="#F59E0B" />
                            </BarChart>
                        </ResponsiveContainer>
                    </div>
                </div>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-100">
                    <h3 className="text-lg font-semibold text-gray-800 mb-4">Composicao da Despesa</h3>
                    <div className="h-80">
                        <ResponsiveContainer width="100%" height="100%">
                            <PieChart>
                                <Pie
                                    data={expenseComposition}
                                    cx="45%"
                                    cy="50%"
                                    innerRadius={60}
                                    outerRadius={110}
                                    paddingAngle={3}
                                    dataKey="value"
                                    labelLine={false}
                                    label={false}
                                >
                                    {expenseComposition.map((entry, index) => (
                                        <Cell key={`cell-${index}`} fill={COLOR_PALETTE[index % COLOR_PALETTE.length]} />
                                    ))}
                                </Pie>
                                <Tooltip formatter={(value) => formatCurrency(value)} />
                                <Legend
                                    layout="vertical"
                                    align="right"
                                    verticalAlign="middle"
                                    wrapperStyle={{ right: 0 }}
                                />
                            </PieChart>
                        </ResponsiveContainer>
                    </div>
                </div>

                <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-100">
                    <h3 className="text-lg font-semibold text-gray-800 mb-4">Top 10 Despesas (Pareto)</h3>
                    <div className="h-80">
                        <ResponsiveContainer width="100%" height="100%">
                            <BarChart data={paretoData} layout="vertical">
                                <CartesianGrid strokeDasharray="3 3" horizontal={false} />
                                <XAxis type="number" />
                                <YAxis dataKey="name" type="category" width={160} tick={{ fontSize: 11 }} />
                                <Tooltip formatter={(value) => formatCurrency(value)} />
                                <Legend />
                                <Bar dataKey="value" fill="#EF4444" radius={[0, 4, 4, 0]} />
                            </BarChart>
                        </ResponsiveContainer>
                    </div>
                </div>
            </div>

            <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-100">
                <h3 className="text-lg font-semibold text-gray-800 mb-4">Todas as Categorias (Fixo x Variavel)</h3>
                <div className="overflow-auto max-h-96">
                    <table className="min-w-full text-sm text-gray-700">
                        <thead className="bg-gray-50 text-xs uppercase tracking-wide text-gray-500">
                            <tr>
                                <th className="px-4 py-2 text-left">Categoria</th>
                                <th className="px-4 py-2 text-left">Tipo</th>
                                <th className="px-4 py-2 text-right">Valor</th>
                            </tr>
                        </thead>
                        <tbody>
                            {expenseTable.map((item, idx) => (
                                <tr key={idx} className={idx % 2 === 0 ? "bg-white" : "bg-gray-50"}>
                                    <td className="px-4 py-2">{item.name}</td>
                                    <td className="px-4 py-2">
                                        <span
                                            className={`px-2 py-1 rounded text-xs ${(item.tipo || "Variavel") === "Fixo"
                                                    ? "bg-indigo-100 text-indigo-700"
                                                    : "bg-amber-100 text-amber-700"
                                                }`}
                                        >
                                            {(item.tipo || "Variavel") === "Fixo" ? "Fixo" : "Variavel"}
                                        </span>
                                    </td>
                                    <td className="px-4 py-2 text-right font-semibold">{formatCurrency(item.value)}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    );
};

export default DespesasView;

import React, { useEffect, useState } from 'react';
import Card from './components/Card';
import InfoCard from './components/InfoCard';
import api from './apiClient';
import { formatCurrency, formatMonthName } from './utils/helpers';
import { TrendingUp, DollarSign, BarChart3, Calculator, Activity } from 'lucide-react';
import {
    LineChart, Line, XAxis, YAxis, Tooltip, CartesianGrid, AreaChart, Area,
    BarChart as ReBarChart, Bar, Legend, ResponsiveContainer,
} from 'recharts';

const PredictiveView = ({ context = "flux" }) => {
    const [forecast, setForecast] = useState(null);
    const [valuation, setValuation] = useState(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        const fetchData = async () => {
            try {
                const [fRes, vRes] = await Promise.all([
                    api.get('/api/predictive/forecast', { params: { context } }),
                    api.get('/api/valuation/data', { params: { context } }),
                ]);
                setForecast(fRes.data);
                setValuation(vRes.data);
            } catch (e) {
                console.error('Erro ao carregar dados preditivos', e);
            } finally {
                setLoading(false);
            }
        };
        fetchData();
    }, []);

    if (loading) {
        return <div className="text-center py-8">Carregando dados preditivos...</div>;
    }

    // ---- KPI Cards ----
    const kpis = [];
    if (forecast && forecast.mrr_forecast) {
        const mrr = forecast.mrr_forecast.forecast;
        const sum3 = mrr.slice(0, 3).reduce((a, b) => a + b, 0);
        const sum6 = mrr.slice(0, 6).reduce((a, b) => a + b, 0);
        const sum12 = mrr.slice(0, 12).reduce((a, b) => a + b, 0);
        kpis.push({ label: 'MRR 3 meses', value: formatCurrency(sum3) });
        kpis.push({ label: 'MRR 6 meses', value: formatCurrency(sum6) });
        kpis.push({ label: 'MRR 12 meses', value: formatCurrency(sum12) });
    }
    if (valuation?.scenarios?.base?.valuation) {
        kpis.push({ label: 'Valuation Base', value: formatCurrency(valuation.scenarios.base.valuation) });
    }

    // ---- Line Chart (Receita real x forecast) ----
    const lineData = [];
    if (forecast && forecast.mrr_historico) {
        const historico = Object.entries(forecast.mrr_historico)
            .sort(([a], [b]) => a.localeCompare(b))
            .map(([mesAno, val]) => ({
                mes: formatMonthName(mesAno),
                real: val,
            }));
        lineData.push(...historico);

        if (forecast.mrr_forecast) {
            const lastHistoricDate = Object.keys(forecast.mrr_historico).sort().pop();
            const forecastData = forecast.mrr_forecast.forecast.map((val, i) => {
                // Gerar nome do mês futuro
                const [year, month] = lastHistoricDate.split('-').map(Number);
                const futureDate = new Date(year, month + i, 1);
                const futureMonth = String(futureDate.getMonth() + 1).padStart(2, '0');
                const futureYear = futureDate.getFullYear();
                const futureMesAno = `${futureYear}-${futureMonth}`;

                return {
                    mes: formatMonthName(futureMesAno),
                    forecast: val,
                    lower: forecast.mrr_forecast.confidence_lower?.[i],
                    upper: forecast.mrr_forecast.confidence_upper?.[i],
                };
            });
            lineData.push(...forecastData);
        }
    }

    // ---- Area Chart (ROL Projetado) ----
    const rolData = [];
    if (forecast && forecast.mrr_forecast && forecast.despesa_forecast) {
        const mrr = forecast.mrr_forecast.forecast;
        const desp = forecast.despesa_forecast.forecast;
        for (let i = 0; i < Math.min(mrr.length, desp.length); i++) {
            rolData.push({
                mes: `Mês ${i + 1}`,
                rol: mrr[i] - desp[i],
            });
        }
    }

    // ---- Bar Chart (Setup vs MRR) ----
    const barData = [];
    if (forecast && forecast.receitas_por_tipo) {
        const grouped = {};
        forecast.receitas_por_tipo.forEach((row) => {
            const mes = formatMonthName(row.MES_ANO);
            if (!grouped[mes]) grouped[mes] = { mes, MRR: 0, Setup: 0 };
            if (row.TIPO_RECEITA === 'MRR') grouped[mes].MRR += row['VALOR MOV.CONTA'];
            if (row.TIPO_RECEITA === 'Setup') grouped[mes].Setup += row['VALOR MOV.CONTA'];
        });
        barData.push(...Object.values(grouped));
    }

    return (
        <div className="space-y-6">
            {/* KPI Cards */}
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
                {kpis.map((kpi, i) => (
                    <Card key={i} title={kpi.label}>
                        <div className="text-2xl font-bold text-blue-600">{kpi.value}</div>
                    </Card>
                ))}
            </div>

            {/* Cards Explicativos */}
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                <InfoCard
                    title="MRR - Monthly Recurring Revenue"
                    icon={TrendingUp}
                    bgColor="bg-green-50"
                    iconColor="text-green-600"
                    borderColor="border-green-200"
                    content="Receita recorrente mensal. Representa a receita previsível e estável proveniente de contratos de assinatura ou serviços mensais. É o principal indicador de saúde financeira para empresas SaaS e de serviços recorrentes."
                />
                <InfoCard
                    title="ARR - Annual Recurring Revenue"
                    icon={DollarSign}
                    bgColor="bg-blue-50"
                    iconColor="text-blue-600"
                    borderColor="border-blue-200"
                    content="Receita recorrente anual (MRR × 12). Métrica fundamental para valuation de empresas de receita recorrente. Investidores e compradores avaliam o negócio principalmente com base no ARR e sua taxa de crescimento."
                />
                <InfoCard
                    title="Forecast e Intervalos de Confiança"
                    icon={Activity}
                    bgColor="bg-indigo-50"
                    iconColor="text-indigo-600"
                    borderColor="border-indigo-200"
                    content="Forecast (linha verde): Projeção de receita usando Prophet. IC Inferior/Superior (linhas amarela/laranja): Intervalo de confiança de 80% - há 80% de probabilidade da receita real ficar entre esses limites."
                />
                <InfoCard
                    title="Modelo Prophet (Meta)"
                    icon={BarChart3}
                    bgColor="bg-purple-50"
                    iconColor="text-purple-600"
                    borderColor="border-purple-200"
                    content="Utilizamos o Prophet (Meta) para projeção de receitas. Este modelo de machine learning considera sazonalidade, tendências e padrões históricos para gerar previsões de 12 meses com alta precisão."
                />
                <InfoCard
                    title="Metodologia de Valuation"
                    icon={Calculator}
                    bgColor="bg-orange-50"
                    iconColor="text-orange-600"
                    borderColor="border-orange-200"
                    content="Valuation = ARR Projetado × Múltiplo. Usamos 3 cenários: Conservador (3x), Base (5x) e Agressivo (8x). Múltiplos baseados em benchmarks de mercado para empresas SaaS B2B com receita recorrente."
                />
            </div>

            {/* Gráfico: Receita Real x Forecast */}
            <Card title="Receita (Real x Forecast)">
                <ResponsiveContainer width="100%" height={300}>
                    <LineChart data={lineData}>
                        <CartesianGrid strokeDasharray="3 3" />
                        <XAxis dataKey="mes" />
                        <YAxis />
                        <Tooltip formatter={(value) => formatCurrency(value)} />
                        <Legend />
                        <Line type="monotone" dataKey="real" stroke="#3b82f6" name="Real" strokeWidth={2} />
                        <Line type="monotone" dataKey="forecast" stroke="#10b981" name="Forecast" strokeWidth={2} strokeDasharray="5 5" />
                        <Line type="monotone" dataKey="lower" stroke="#eab308" strokeWidth={2} strokeDasharray="2 2" name="IC Inferior" />
                        <Line type="monotone" dataKey="upper" stroke="#f97316" strokeWidth={2} strokeDasharray="2 2" name="IC Superior" />
                    </LineChart>
                </ResponsiveContainer>
            </Card>

            {/* Gráfico: ROL Projetado */}
            <Card title="Resultado Operacional Projetado (ROL)">
                <ResponsiveContainer width="100%" height={300}>
                    <AreaChart data={rolData}>
                        <CartesianGrid strokeDasharray="3 3" />
                        <XAxis dataKey="mes" />
                        <YAxis />
                        <Tooltip formatter={(value) => formatCurrency(value)} />
                        <Area
                            type="monotone"
                            dataKey="rol"
                            stroke="#8b5cf6"
                            fill="#8b5cf6"
                            fillOpacity={0.3}
                            name="ROL"
                        />
                    </AreaChart>
                </ResponsiveContainer>
            </Card>

            {/* Gráfico: Setup vs MRR */}
            <Card title="Setup vs MRR (por mês)">
                <ResponsiveContainer width="100%" height={300}>
                    <ReBarChart data={barData}>
                        <CartesianGrid strokeDasharray="3 3" />
                        <XAxis dataKey="mes" />
                        <YAxis />
                        <Tooltip formatter={(value) => formatCurrency(value)} />
                        <Legend />
                        <Bar dataKey="MRR" fill="#10b981" name="MRR (Recorrente)" />
                        <Bar dataKey="Setup" fill="#f59e0b" name="Setup (Não Recorrente)" />
                    </ReBarChart>
                </ResponsiveContainer>
            </Card>
        </div>
    );
};

export default PredictiveView;

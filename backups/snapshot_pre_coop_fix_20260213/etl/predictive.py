"""
Módulo de Análise Preditiva e Valuation
Implementa forecasting de receitas/despesas usando Prophet
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import json
import logging

# Configurar logging para suprimir warnings do Prophet
logging.getLogger('prophet').setLevel(logging.ERROR)
logging.getLogger('cmdstanpy').setLevel(logging.ERROR)

try:
    from prophet import Prophet
    PROPHET_AVAILABLE = True
except ImportError:
    PROPHET_AVAILABLE = False
    print("AVISO: Prophet não instalado. Usando forecast simples.")

def classify_revenue_type(plano_contas):
    """Classifica receita como MRR (recorrente) ou Setup (não recorrente)"""
    if pd.isna(plano_contas):
        return 'Outros'
    
    plano_upper = str(plano_contas).upper()
    
    # MRR - Receitas Recorrentes
    mrr_keywords = ['ENTREGAS', 'GESTAO', 'MENSALIDADE', 'ASSINATURA']
    if any(keyword in plano_upper for keyword in mrr_keywords):
        return 'MRR'
    
    # Setup - Receitas Não Recorrentes
    setup_keywords = ['SETUP', 'REEMBOLSO', 'IMPLANTACAO', 'INSTALACAO']
    if any(keyword in plano_upper for keyword in setup_keywords):
        return 'Setup'
    
    return 'Outros'

def prophet_forecast(historical_series, periods=12):
    """
    Forecast usando Prophet (Meta)
    Retorna forecast com intervalos de confiança
    """
    if not PROPHET_AVAILABLE or len(historical_series) < 3:
        return simple_forecast_fallback(historical_series, periods)
    
    try:
        # Preparar dados no formato Prophet (ds, y)
        df = pd.DataFrame({
            'ds': pd.date_range(end=datetime.now(), periods=len(historical_series), freq='MS'),
            'y': historical_series
        })
        
        # Criar e treinar modelo
        model = Prophet(
            yearly_seasonality=True,
            weekly_seasonality=False,
            daily_seasonality=False,
            seasonality_mode='multiplicative',
            interval_width=0.80  # 80% de intervalo de confiança
        )
        
        # Suprimir output do Prophet
        import sys
        from io import StringIO
        old_stdout = sys.stdout
        sys.stdout = StringIO()
        
        model.fit(df)
        
        # Restaurar stdout
        sys.stdout = old_stdout
        
        # Criar dataframe de datas futuras
        future = model.make_future_dataframe(periods=periods, freq='MS')
        
        # Fazer predição
        forecast = model.predict(future)
        
        # Extrair apenas os valores futuros
        future_forecast = forecast.tail(periods)
        
        return {
            'forecast': future_forecast['yhat'].tolist(),
            'confidence_lower': future_forecast['yhat_lower'].tolist(),
            'confidence_upper': future_forecast['yhat_upper'].tolist()
        }
    
    except Exception as e:
        print(f"Erro ao usar Prophet: {e}. Usando fallback.")
        return simple_forecast_fallback(historical_series, periods)

def simple_forecast_fallback(historical_data, periods=12):
    """
    Forecast simples usando tendência linear (fallback)
    """
    if len(historical_data) < 3:
        return None
    
    # Calcular tendência linear simples
    x = np.arange(len(historical_data))
    y = np.array(historical_data)
    
    # Regressão linear
    coeffs = np.polyfit(x, y, 1)
    trend = coeffs[0]
    intercept = coeffs[1]
    
    # Projetar valores futuros
    forecast = []
    last_index = len(historical_data) - 1
    
    for i in range(1, periods + 1):
        predicted_value = intercept + trend * (last_index + i)
        forecast.append(max(0, predicted_value))
    
    # Calcular intervalo de confiança (±15%)
    confidence_interval = [(val * 0.85, val * 1.15) for val in forecast]
    
    return {
        'forecast': forecast,
        'confidence_lower': [ci[0] for ci in confidence_interval],
        'confidence_upper': [ci[1] for ci in confidence_interval]
    }

def calculate_valuation(mrr_current, mrr_projected_12m):
    """
    Calcula valuation baseado em ARR e múltiplos
    
    Metodologia:
    - ARR (Annual Recurring Revenue) = MRR médio × 12
    - Valuation = ARR × Múltiplo de mercado
    - Múltiplos baseados em benchmarks de SaaS B2B
    """
    # ARR = Annual Recurring Revenue
    arr_current = mrr_current * 12
    arr_projected = sum(mrr_projected_12m)
    
    # Múltiplos de mercado (baseados em SaaS B2B)
    multiples = {
        'conservador': 3.0,   # Empresas early-stage ou baixo crescimento
        'base': 5.0,          # Empresas com crescimento moderado
        'agressivo': 8.0      # Empresas high-growth ou mercado aquecido
    }
    
    valuations = {
        'arr_current': arr_current,
        'arr_projected': arr_projected,
        'scenarios': {
            'conservador': {
                'multiple': multiples['conservador'],
                'valuation': arr_projected * multiples['conservador']
            },
            'base': {
                'multiple': multiples['base'],
                'valuation': arr_projected * multiples['base']
            },
            'agressivo': {
                'multiple': multiples['agressivo'],
                'valuation': arr_projected * multiples['agressivo']
            }
        }
    }
    
    return valuations

def process_predictive_data(df_financeiro, df_gerencial):
    """
    Processa dados para análise preditiva
    """
    # 1. Classificar receitas por tipo
    df_financeiro['TIPO_RECEITA'] = df_financeiro['PLANO DE CONTAS'].apply(classify_revenue_type)
    
    # 2. Agregar receitas mensais por tipo
    receitas_mensais = df_financeiro[df_financeiro['ENTRADA/SAIDA'] == 'ENTRADA'].groupby(
        ['MES_ANO', 'TIPO_RECEITA']
    )['VALOR MOV.CONTA'].sum().reset_index()
    
    # 3. Agregar despesas mensais
    # Normalizar para garantir match (o banco deve ter SAIDA sem acento)
    despesas_mensais = df_financeiro[
        df_financeiro['ENTRADA/SAIDA'].apply(lambda x: str(x).upper().replace('Í', 'I')) == 'SAIDA'
    ].groupby('MES_ANO')['VALOR MOV.CONTA'].sum().reset_index()
    
    despesas_mensais.columns = ['MES_ANO', 'DESPESA_TOTAL']
    despesas_mensais['DESPESA_TOTAL'] = despesas_mensais['DESPESA_TOTAL'].abs()
    
    # 4. Calcular MRR histórico
    mrr_historico = receitas_mensais[receitas_mensais['TIPO_RECEITA'] == 'MRR'].groupby(
        'MES_ANO'
    )['VALOR MOV.CONTA'].sum().sort_index()
    
    # 5. Fazer forecast com Prophet (12 meses)
    if len(mrr_historico) >= 3:
        mrr_forecast = prophet_forecast(mrr_historico.values.tolist(), periods=12)
    else:
        mrr_forecast = None
    
    if len(despesas_mensais) >= 3:
        despesa_forecast = prophet_forecast(despesas_mensais['DESPESA_TOTAL'].values.tolist(), periods=12)
    else:
        despesa_forecast = None
    
    # 6. Calcular Valuation
    mrr_current = mrr_historico.tail(3).mean() if len(mrr_historico) >= 3 else 0
    mrr_projected = mrr_forecast['forecast'] if mrr_forecast else [mrr_current] * 12
    valuation = calculate_valuation(mrr_current, mrr_projected)
    
    result = {
        'mrr_historico': mrr_historico.to_dict(),
        'mrr_forecast': mrr_forecast,
        'despesa_forecast': despesa_forecast,
        'valuation': valuation,
        'receitas_por_tipo': receitas_mensais.to_dict('records')
    }
    
    # Sanitize for JSON (replace NaN/Inf with None, convert numpy types)
    import json
    def sanitize(obj):
        if obj is None:
            return None
        if isinstance(obj, (np.integer, np.floating)):
            if np.isnan(obj) or np.isinf(obj):
                return None
            return float(obj)
        if isinstance(obj, float):
            if pd.isna(obj) or np.isinf(obj):
                return None
            return obj
        if isinstance(obj, (int, str, bool)):
            return obj
        if isinstance(obj, dict):
            return {k: sanitize(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [sanitize(v) for v in obj]
        # Try to convert to JSON-serializable type
        try:
            json.dumps(obj)
            return obj
        except (TypeError, ValueError):
            return str(obj)
        
    return sanitize(result)

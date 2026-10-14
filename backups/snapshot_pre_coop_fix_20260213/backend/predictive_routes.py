"""
Rotas de API para Análise Preditiva e Valuation
"""

from fastapi import APIRouter, HTTPException
import json

router = APIRouter()

def get_predictive_data_from_redis(r):
    """Helper para buscar dados preditivos do Redis"""
    try:
        data = r.get("predictive_data")
        if not data:
            return None
        return json.loads(data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao buscar dados preditivos: {str(e)}")

@router.get("/api/predictive/forecast")
def get_forecast(r):
    """
    Retorna séries de receita e despesa projetadas
    """
    predictive_data = get_predictive_data_from_redis(r)
    
    if not predictive_data:
        return {
            "mrr_forecast": None,
            "despesa_forecast": None,
            "message": "Dados preditivos não disponíveis"
        }
    
    return {
        "mrr_historico": predictive_data.get('mrr_historico', {}),
        "mrr_forecast": predictive_data.get('mrr_forecast'),
        "despesa_forecast": predictive_data.get('despesa_forecast'),
        "receitas_por_tipo": predictive_data.get('receitas_por_tipo', [])
    }

@router.get("/api/predictive/clusters")
def get_clusters(r):
    """
    Retorna dados de clustering de clientes
    """
    predictive_data = get_predictive_data_from_redis(r)
    
    if not predictive_data:
        return []
    
    return predictive_data.get('clusters', [])

@router.get("/api/valuation/data")
def get_valuation(r):
    """
    Retorna dados de valuation (ARR, múltiplos, cenários)
    """
    predictive_data = get_predictive_data_from_redis(r)
    
    if not predictive_data:
        return {
            "arr_current": 0,
            "arr_projected": 0,
            "scenarios": {}
        }
    
    return predictive_data.get('valuation', {})

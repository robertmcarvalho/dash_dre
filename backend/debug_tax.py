import pandas as pd
import unicodedata
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.database import get_db, init_db
from backend.db_helpers import get_dre_financeiro_data

def norm(text):
    text = '' if text is None else str(text)
    nfkd = unicodedata.normalize('NFKD', text)
    return ''.join(c for c in nfkd if not unicodedata.combining(c)).upper()

print("Iniciando debug...")
init_db()
db = get_db()
financeiro = get_dre_financeiro_data(db)
df_fin = pd.DataFrame(financeiro)

print(f"Total registros: {len(df_fin)}")

rename_map = {
    'entrada_saida': 'entrada_saida',
    'valor': 'valor',
    'centro_custo': 'centro_custo',
    'tipo_custo': 'tipo_custo',
    'plano_contas': 'plano_contas',
    'mes_ano': 'mes_ano',
    'mes_nome': 'mes_nome',
}
df_fin = df_fin.rename(columns=rename_map)

# Verificar SIMPLES NACIONAL
simples = df_fin[df_fin['plano_contas'].str.contains('SIMPLES NACIONAL', na=False, case=False)]
print(f"Total SIMPLES NACIONAL: {len(simples)}")
if not simples.empty:
    print("Amostra SIMPLES:")
    print(simples[['centro_custo', 'valor', 'mes_ano']].head())
    
    # Verificar normalização do centro de custo
    simples['centro_norm'] = simples['centro_custo'].apply(norm)
    print("Centros normalizados:")
    print(simples['centro_norm'].value_counts())

# Testar filtro de impostos para rateio
df_impostos = df_fin[
    (df_fin['plano_contas'].str.contains('SIMPLES NACIONAL', na=False, case=False)) &
    (df_fin['centro_custo'].apply(lambda x: norm(x) == 'NAO ESPECIFICADO'))
]
print(f"Impostos para rateio: {len(df_impostos)}")
if not df_impostos.empty:
    impostos_por_mes = df_impostos.groupby('mes_ano')['valor'].sum().abs().to_dict()
    print(f"Impostos por mes: {impostos_por_mes}")
else:
    print("Nenhum imposto para rateio encontrado com filtro 'NAO ESPECIFICADO'")

# Testar filtro de farmácias
df_farmacias = df_fin[df_fin['centro_custo'].apply(lambda x: norm(x) != 'NAO ESPECIFICADO')]
df_farmacias = df_farmacias[df_farmacias['centro_custo'].str.contains('FARM', na=False, case=False)]
print(f"Total farmácias: {len(df_farmacias)}")

df_farmacias['entrada_norm'] = df_farmacias['entrada_saida'].apply(norm)
receita_total_mes = df_farmacias[df_farmacias['entrada_norm'] == 'ENTRADA'].groupby('mes_ano')['valor'].sum().to_dict()
print(f"Receita total por mes: {receita_total_mes}")

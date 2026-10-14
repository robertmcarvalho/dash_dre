import pandas as pd

# Read Excel
df = pd.read_excel('/app/data/relatorio_flux.xlsx')

# Find ENTRADA/SAÍDA column
entrada_cols = [c for c in df.columns if 'ENTRADA' in str(c).upper() or 'SAIDA' in str(c).upper() or 'SAÍDA' in str(c).upper()]
print(f"Colunas com ENTRADA/SAÍDA: {entrada_cols}")

if entrada_cols:
    col = entrada_cols[0]
    print(f"\n=== Valores únicos em '{col}' ===")
    print(df[col].value_counts())
    
    print(f"\n=== Primeiros 10 valores ===")
    print(df[col].head(10).tolist())

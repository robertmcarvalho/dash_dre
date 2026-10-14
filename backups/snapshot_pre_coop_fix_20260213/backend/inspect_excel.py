import pandas as pd

# Read Excel
df = pd.read_excel('/app/data/relatorio_flux.xlsx')

# Find columns
cols = [c for c in df.columns if 'CENTRO' in str(c).upper() or 'PARCEIRO' in str(c).upper()]
print('Colunas relevantes:', cols)

# Show sample data
for col in cols:
    print(f'\n=== {col} ===')
    print(df[col].head(10).tolist())

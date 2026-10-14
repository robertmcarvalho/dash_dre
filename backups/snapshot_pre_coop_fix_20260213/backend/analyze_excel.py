import pandas as pd

# Read Excel
df = pd.read_excel('/app/data/relatorio_flux.xlsx')

# Find columns
centro_col = [c for c in df.columns if 'CENTRO DE CUSTO' in str(c).upper()][0]
parceiro_col = [c for c in df.columns if 'PARCEIROV' in str(c).upper()][0]

print(f"Centro de Custo: {centro_col}")
print(f"Parceiro: {parceiro_col}")

print("\n=== Valores únicos em CENTRO DE CUSTO (primeiros 20) ===")
print(df[centro_col].unique()[:20])

print("\n=== Valores únicos em PARCEIRO (primeiros 20) ===")
print(df[parceiro_col].unique()[:20])

# Check if CENTRO DE CUSTO contains "FARMÁCIA"
centro_farmacias = df[df[centro_col].str.contains('FARM', na=False)][centro_col].unique()
print(f"\n=== CENTRO DE CUSTO com 'FARM' ({len(centro_farmacias)} valores) ===")
print(centro_farmacias[:10])

# Check if PARCEIRO contains "FARMÁCIA"
parceiro_farmacias = df[df[parceiro_col].str.contains('FARM', na=False)][parceiro_col].unique()
print(f"\n=== PARCEIRO com 'FARM' ({len(parceiro_farmacias)} valores) ===")
print(parceiro_farmacias[:10])

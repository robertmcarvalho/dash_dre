import json
import sys

# Read both data files
with open('data/dre_financeiro.json', 'r', encoding='utf-8') as f:
    financeiro = json.load(f)

with open('data/dre_gerencial.json', 'r', encoding='utf-8') as f:
    gerencial = json.load(f)

# Extract unique Plano de Contas
planos_fin = set([item.get('PLANO DE CONTAS', 'N/A') for item in financeiro if item.get('PLANO DE CONTAS')])
planos_ger = set([item.get('PLANO DE CONTAS', 'N/A') for item in gerencial if item.get('PLANO DE CONTAS')])

all_planos = sorted(planos_fin.union(planos_ger))

print("=" * 80)
print("PLANOS DE CONTAS ENCONTRADOS:")
print("=" * 80)
for i, plano in enumerate(all_planos, 1):
    print(f"{i:2d}. {plano}")

print(f"\nTotal: {len(all_planos)} categorias")

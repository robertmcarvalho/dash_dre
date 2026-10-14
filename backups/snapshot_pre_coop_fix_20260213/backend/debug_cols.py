import pandas as pd
import os

file_path = 'data/relatorio_flux.xlsx'
try:
    df = pd.read_excel(file_path)
    print([repr(c) for c in df.columns])
except Exception as e:
    print(f"Error: {e}")

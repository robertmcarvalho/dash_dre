-- Conciliação semanal (Cooperativa): Entregas x Pagamento de Entregadores
-- Execute no SQL Editor do Supabase.

CREATE TABLE IF NOT EXISTS coop_conciliacao_entregas (
    id SERIAL PRIMARY KEY,
    semana_inicio DATE,
    semana_fim DATE,
    periodo_inicio DATE,
    periodo_fim DATE,
    data_mov DATE,
    data_pag DATE,
    entrada_saida VARCHAR(10),
    plano_contas VARCHAR(255),
    parceiro VARCHAR(255),
    referente TEXT,
    numero_docto VARCHAR(64),
    registro_movimento VARCHAR(64),
    registro_compromisso VARCHAR(64),
    centro_custo_raw VARCHAR(255),
    centro_custo_final VARCHAR(255),
    valor_mov NUMERIC(15, 2),
    valor_rateado NUMERIC(15, 2),
    linha_tipo VARCHAR(10), -- MOV ou RATEIO
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_coop_conc_semana_fim ON coop_conciliacao_entregas(semana_fim);
CREATE INDEX IF NOT EXISTS idx_coop_conc_centro ON coop_conciliacao_entregas(centro_custo_final);
CREATE INDEX IF NOT EXISTS idx_coop_conc_plano ON coop_conciliacao_entregas(plano_contas);
CREATE INDEX IF NOT EXISTS idx_coop_conc_entrada ON coop_conciliacao_entregas(entrada_saida);


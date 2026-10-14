-- DRE Executivo Flux Farma - Supabase Schema
-- Execute este script no SQL Editor do Supabase
-- Tabela de Usuários
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    nome VARCHAR(255) UNIQUE NOT NULL,
    senha VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_users_nome ON users(nome);
-- Tabela DRE Financeiro
CREATE TABLE IF NOT EXISTS dre_financeiro (
    id SERIAL PRIMARY KEY,
    mes_ano VARCHAR(7) NOT NULL,
    mes_nome VARCHAR(20),
    data_mov DATE,
    centro_custo VARCHAR(255),
    parceiro VARCHAR(255),
    plano_contas VARCHAR(255),
    entrada_saida VARCHAR(10),
    valor NUMERIC(15, 2),
    tipo_custo VARCHAR(20),
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_dre_fin_mes ON dre_financeiro(mes_ano);
CREATE INDEX IF NOT EXISTS idx_dre_fin_centro ON dre_financeiro(centro_custo);
CREATE INDEX IF NOT EXISTS idx_dre_fin_entrada ON dre_financeiro(entrada_saida);
-- Tabela DRE Gerencial
CREATE TABLE IF NOT EXISTS dre_gerencial (
    id SERIAL PRIMARY KEY,
    mes_ano VARCHAR(7) NOT NULL,
    mes_nome VARCHAR(20),
    centro_custo VARCHAR(255),
    receita_total NUMERIC(15, 2),
    despesa_total NUMERIC(15, 2),
    lucro NUMERIC(15, 2),
    margem_liquida NUMERIC(5, 2),
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_dre_ger_mes ON dre_gerencial(mes_ano);
CREATE INDEX IF NOT EXISTS idx_dre_ger_centro ON dre_gerencial(centro_custo);
-- Tabela Predictive Data (JSON)
CREATE TABLE IF NOT EXISTS predictive_data (
    id SERIAL PRIMARY KEY,
    data_type VARCHAR(50) UNIQUE NOT NULL,
    data_json JSONB NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_pred_type ON predictive_data(data_type);
-- Inserir usuário admin padrão (senha: admin)
INSERT INTO users (nome, senha)
VALUES ('admin', 'admin') ON CONFLICT (nome) DO NOTHING;
-- Comentários nas tabelas
COMMENT ON TABLE users IS 'Usuários do sistema';
COMMENT ON TABLE dre_financeiro IS 'Dados financeiros detalhados do DRE';
COMMENT ON TABLE dre_gerencial IS 'Dados gerenciais agregados do DRE';
COMMENT ON TABLE predictive_data IS 'Dados de análise preditiva e valuation';
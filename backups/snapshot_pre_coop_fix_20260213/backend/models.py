"""
SQLAlchemy models for Supabase PostgreSQL
"""
from sqlalchemy import Column, Integer, String, Numeric, Date, DateTime, Text, JSON
from sqlalchemy.dialects.postgresql import JSONB
from datetime import datetime
from database import Base

class User(Base):
    """Users table for authentication"""
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    nome = Column(String(255), unique=True, nullable=False, index=True)
    senha = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.now)

class DREFinanceiro(Base):
    """Financial DRE data"""
    __tablename__ = "dre_financeiro"
    
    id = Column(Integer, primary_key=True, index=True)
    mes_ano = Column(String(7), nullable=False, index=True)
    mes_nome = Column(String(20))
    data_mov = Column(Date)
    centro_custo = Column(String(255), index=True)
    parceiro = Column(String(255))
    plano_contas = Column(String(255))
    entrada_saida = Column(String(10))
    valor = Column(Numeric(15, 2))
    tipo_custo = Column(String(20))
    created_at = Column(DateTime, default=datetime.now)

class DREGerencial(Base):
    """Management DRE data"""
    __tablename__ = "dre_gerencial"
    
    id = Column(Integer, primary_key=True, index=True)
    mes_ano = Column(String(7), nullable=False, index=True)
    mes_nome = Column(String(20))
    centro_custo = Column(String(255), index=True)
    receita_total = Column(Numeric(15, 2))
    despesa_total = Column(Numeric(15, 2))
    lucro = Column(Numeric(15, 2))
    margem_liquida = Column(Numeric(5, 2))
    created_at = Column(DateTime, default=datetime.now)

class PredictiveData(Base):
    """Predictive analytics data stored as JSON"""
    __tablename__ = "predictive_data"
    
    id = Column(Integer, primary_key=True, index=True)
    data_type = Column(String(50), nullable=False, unique=True, index=True)
    data_json = Column(JSONB, nullable=False)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

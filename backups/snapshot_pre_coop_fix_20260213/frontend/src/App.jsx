import React, { useState } from 'react';
import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom';
import Login from './Login';
import Layout from './Layout';
import GlobalView from './GlobalView';
import DespesasView from './DespesasView';
import FarmaciaView from './FarmaciaView';
import PredictiveView from './PredictiveView';
import ConfigView from './ConfigView';
import Sidebar from './components/Sidebar';

function Dashboard() {
  const [activeTab, setActiveTab] = useState('global');
  const [perfil, setPerfil] = useState(() => localStorage.getItem('perfil') || 'operacional');
  const [username, setUsername] = useState(() => localStorage.getItem('username') || 'Usuário');
  const [contexto, setContexto] = useState(() => localStorage.getItem('contexto') || 'flux');

  React.useEffect(() => {
    if (perfil === 'operacional' && activeTab === 'config') {
      setActiveTab('global');
    }
    // se contexto for coop, esconder aba preditiva
    if (contexto === 'coop' && activeTab === 'predictive') {
      setActiveTab('global');
    }
  }, [perfil, activeTab, contexto]);

  const getTitle = () => {
    switch (activeTab) {
      case 'global': return 'Visão Global';
      case 'despesas': return 'Detalhamento de Despesas';
      case 'farmacia': return 'Visão por Farmácia';
      default: return 'Dashboard';
    }
  };

  const getSubtitle = () => {
    switch (activeTab) {
      case 'global': return 'Acompanhe os principais indicadores financeiros.';
      case 'despesas': return 'Análise profunda de custos fixos, variáveis e categorias.';
      case 'farmacia': return 'Análise detalhada de custos e receitas por unidade.';
      default: return '';
    }
  };

  if (perfil === 'operacional' && activeTab === 'config') {
    setActiveTab('global');
  }

  return (
    <div className="flex min-h-screen bg-slate-50">
      <Sidebar activeTab={activeTab} setActiveTab={setActiveTab} perfil={perfil} contexto={contexto} />
      <Sidebar activeTab={activeTab} setActiveTab={setActiveTab} perfil={perfil} contexto={contexto} />

      <div className="flex-1 ml-64 p-8 overflow-y-auto h-screen">
        <header className="mb-8 flex justify-between items-center">
          <div>
            <h2 className="text-2xl font-bold text-gray-800">
              {getTitle()}
            </h2>
            <p className="text-gray-500 text-sm mt-1">
              {getSubtitle()}
            </p>
          </div>
          <div className="flex items-center gap-3">
            <div className="flex items-center bg-gray-100 rounded-lg px-3 py-2 text-sm">
              <span className="text-gray-700 mr-2">Contexto:</span>
              <select
                className="bg-white border border-gray-200 rounded px-2 py-1 text-sm"
                value={contexto}
                onChange={(e) => {
                  setContexto(e.target.value);
                  localStorage.setItem('contexto', e.target.value);
                  if (e.target.value === 'coop' && activeTab === 'predictive') {
                    setActiveTab('global');
                  }
                }}
              >
                <option value="flux">Flux</option>
                <option value="coop">Cooperativa</option>
              </select>
            </div>
            <div className="text-right hidden md:block">
              <p className="text-sm font-medium text-gray-700">Seja bem vindo {username}!</p>
            </div>
            <div className="h-10 w-10 rounded-full bg-blue-100 flex items-center justify-center text-blue-600 font-bold">
              {username.charAt(0).toUpperCase()}
            </div>
          </div>
        </header>

        <main className="animate-fade-in">
          {activeTab === 'global' && <GlobalView key={`${contexto}-global`} context={contexto} />}
          {activeTab === 'despesas' && <DespesasView key={`${contexto}-despesas`} context={contexto} />}
          {activeTab === 'farmacia' && <FarmaciaView key={`${contexto}-farmacia`} context={contexto} />}
          {activeTab === 'predictive' && <PredictiveView key={`${contexto}-predictive`} context={contexto} />}
          {activeTab === 'config' && perfil === 'adm' && <ConfigView key={`${contexto}-config`} />}
        </main>
      </div>
    </div>
  );
}

function App() {
  return (
    <Router>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/" element={<Layout />}>
          <Route index element={<Dashboard />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Router>
  );
}

export default App;

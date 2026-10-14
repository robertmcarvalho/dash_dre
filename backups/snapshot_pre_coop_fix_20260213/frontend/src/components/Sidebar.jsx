import React from "react";
import { LayoutDashboard, Store, LogOut, PieChart, DollarSign, BarChart2, Settings } from "lucide-react";
import { useNavigate } from "react-router-dom";

const Sidebar = ({ activeTab, setActiveTab, perfil = "operacional", contexto = "flux" }) => {
    const navigate = useNavigate();

    const handleLogout = () => {
        localStorage.removeItem("token");
        localStorage.removeItem("perfil");
        navigate("/login");
    };

    const menuItems = [
        { id: "global", label: "Visão Global", icon: LayoutDashboard },
        { id: "despesas", label: "Detalhamento Despesas", icon: DollarSign },
        { id: "farmacia", label: "Visão por Farmácia", icon: Store },
        { id: "predictive", label: "Análise Preditiva & Valuation", icon: BarChart2 },
        { id: "config", label: "Configurações", icon: Settings },
    ];

    let filteredMenu = perfil === "adm" ? menuItems : menuItems.filter((item) => item.id !== "config");
    if (contexto === "coop") {
        filteredMenu = filteredMenu.filter((item) => item.id !== "predictive");
    }

    return (
        <div className="w-64 bg-slate-900 text-white flex flex-col h-screen fixed left-0 top-0 shadow-xl z-50">
            <div className="p-6 border-b border-slate-800 flex items-center gap-3">
                <div className="bg-blue-600 p-2 rounded-lg">
                    <PieChart className="w-6 h-6 text-white" />
                </div>
                <h1 className="text-xl font-bold tracking-tight">DRE Executivo Flux Farma</h1>
            </div>

            <nav className="flex-1 p-4 space-y-2">
                {filteredMenu.map((item) => {
                    const Icon = item.icon;
                    const isActive = activeTab === item.id;
                    return (
                        <button
                            key={item.id}
                            onClick={() => setActiveTab(item.id)}
                            className={`w-full flex items-center gap-3 px-4 py-3 rounded-lg transition-all duration-200 group ${isActive ? "bg-blue-600 text-white shadow-lg shadow-blue-900/20" : "text-slate-400 hover:bg-slate-800 hover:text-white"}`}
                        >
                            <Icon className={`w-5 h-5 ${isActive ? "text-white" : "text-slate-400 group-hover:text-white"}`} />
                            <span className="font-medium">{item.label}</span>
                        </button>
                    );
                })}
            </nav>

            <div className="p-4 border-t border-slate-800">
                <button
                    onClick={handleLogout}
                    className="w-full flex items-center gap-3 px-4 py-3 rounded-lg text-red-400 hover:bg-red-900/20 hover:text-red-300 transition-colors"
                >
                    <LogOut className="w-5 h-5" />
                    <span className="font-medium">Sair</span>
                </button>
            </div>
        </div>
    );
};

export default Sidebar;

import React, { useState, useEffect } from 'react';
import Card from './components/Card';
import { Upload, Users, CheckCircle, AlertCircle, Trash2 } from 'lucide-react';
import api from './apiClient';

const ConfigView = () => {
    const [file, setFile] = useState(null);
    const [uploading, setUploading] = useState(false);
    const [uploadStatus, setUploadStatus] = useState(null);
    const [contexto, setContexto] = useState(() => localStorage.getItem('contexto') || 'flux');

    // User management
    const [newUser, setNewUser] = useState({ nome: '', senha: '', perfil: 'operacional' });
    const [userStatus, setUserStatus] = useState(null);
    const [users, setUsers] = useState([]);
    const [loadingUsers, setLoadingUsers] = useState(false);

    useEffect(() => {
        fetchUsers();
    }, []);

    const fetchUsers = async () => {
        setLoadingUsers(true);
        try {
            const response = await api.get('/api/users');
            setUsers(response.data.users || []);
        } catch (error) {
            console.error('Erro ao carregar usuários', error);
        } finally {
            setLoadingUsers(false);
        }
    };

    const handleFileChange = (e) => {
        setFile(e.target.files[0]);
        setUploadStatus(null);
    };

    const handleUpload = async () => {
        if (!file) {
            setUploadStatus({ type: 'error', message: 'Selecione um arquivo' });
            return;
        }

        const formData = new FormData();
        formData.append('file', file);
        formData.append('context', contexto);

        setUploading(true);
        setUploadStatus(null);

        try {
            await api.post('/api/upload', formData, {
                headers: { 'Content-Type': 'multipart/form-data' }
            });
            setUploadStatus({ type: 'success', message: 'Arquivo enviado e processado com sucesso!' });
            setFile(null);
        } catch (error) {
            setUploadStatus({
                type: 'error',
                message: error.response?.data?.detail || 'Erro ao enviar arquivo'
            });
        } finally {
            setUploading(false);
        }
    };

    const handleCreateUser = async () => {
        if (!newUser.nome || !newUser.senha) {
            setUserStatus({ type: 'error', message: 'Preencha nome e senha' });
            return;
        }

        try {
            await api.post('/api/users', newUser);
            setUserStatus({ type: 'success', message: 'Usuário criado com sucesso!' });
            setNewUser({ nome: '', senha: '', perfil: 'operacional' });
            fetchUsers();
        } catch (error) {
            setUserStatus({
                type: 'error',
                message: error.response?.data?.detail || 'Erro ao criar usuário'
            });
        }
    };

    const handleDeleteUser = async (username) => {
        if (!confirm(`Tem certeza que deseja excluir o usuário "${username}"?`)) {
            return;
        }

        try {
            await api.delete(`/api/users/${username}`);
            setUserStatus({ type: 'success', message: `Usuário ${username} excluído com sucesso!` });
            fetchUsers();
        } catch (error) {
            setUserStatus({
                type: 'error',
                message: error.response?.data?.detail || 'Erro ao excluir usuário'
            });
        }
    };

    return (
        <div className="space-y-6">
            {/* Upload de Dados */}
            <Card title="Upload de Dados">
                <div className="space-y-4">
                    <div className="flex items-center gap-4">
                        <Upload className="w-6 h-6 text-blue-600" />
                        <div className="flex-1">
                            <p className="text-sm text-gray-600 mb-2">
                                Envie o arquivo Excel (.xlsx) para atualizar os dados do dashboard
                            </p>
                            <div className="flex items-center gap-3 mb-2">
                                <span className="text-sm text-gray-700">Contexto:</span>
                                <select
                                    value={contexto}
                                    onChange={(e) => {
                                        setContexto(e.target.value);
                                        localStorage.setItem('contexto', e.target.value);
                                    }}
                                    className="bg-white border border-gray-200 rounded px-2 py-1 text-sm"
                                >
                                    <option value="flux">Flux</option>
                                    <option value="coop">Cooperativa</option>
                                </select>
                            </div>
                            <input
                                type="file"
                                accept=".xlsx,.xls"
                                onChange={handleFileChange}
                                className="block w-full text-sm text-gray-500
                                    file:mr-4 file:py-2 file:px-4
                                    file:rounded-lg file:border-0
                                    file:text-sm file:font-semibold
                                    file:bg-blue-50 file:text-blue-700
                                    hover:file:bg-blue-100
                                    cursor-pointer"
                            />
                        </div>
                    </div>

                    {file && (
                        <div className="bg-gray-50 p-3 rounded-lg">
                            <p className="text-sm text-gray-700">
                                Arquivo selecionado: <span className="font-medium">{file.name}</span>
                            </p>
                        </div>
                    )}

                    <button
                        onClick={handleUpload}
                        disabled={uploading}
                        className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition duration-200 disabled:opacity-60"
                    >
                        {uploading ? 'Processando...' : 'Enviar e Processar'}
                    </button>

                    {uploadStatus && (
                        <div className={`flex items-center gap-2 text-sm ${uploadStatus.type === 'success' ? 'text-green-600' : 'text-red-600'}`}>
                            {uploadStatus.type === 'success' ? <CheckCircle className="w-4 h-4" /> : <AlertCircle className="w-4 h-4" />}
                            <span>{uploadStatus.message}</span>
                        </div>
                    )}
                </div>
            </Card>

            {/* Gestão de Usuários */}
            <Card title="Gestão de Usuários">
                <div className="space-y-4">
                    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                        <input
                            type="text"
                            placeholder="Nome do usuário"
                            value={newUser.nome}
                            onChange={(e) => setNewUser({ ...newUser, nome: e.target.value })}
                            className="w-full px-4 py-2 border rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                        />
                        <input
                            type="password"
                            placeholder="Senha"
                            value={newUser.senha}
                            onChange={(e) => setNewUser({ ...newUser, senha: e.target.value })}
                            className="w-full px-4 py-2 border rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                        />
                        <select
                            value={newUser.perfil}
                            onChange={(e) => setNewUser({ ...newUser, perfil: e.target.value })}
                            className="w-full px-4 py-2 border rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                        >
                            <option value="operacional">Operacional</option>
                            <option value="adm">Administrador</option>
                        </select>
                    </div>

                    <button
                        onClick={handleCreateUser}
                        className="px-4 py-2 bg-emerald-600 text-white rounded-lg hover:bg-emerald-700 transition duration-200"
                    >
                        Criar Usuário
                    </button>

                    {userStatus && (
                        <div className={`flex items-center gap-2 text-sm ${userStatus.type === 'success' ? 'text-green-600' : 'text-red-600'}`}>
                            {userStatus.type === 'success' ? <CheckCircle className="w-4 h-4" /> : <AlertCircle className="w-4 h-4" />}
                            <span>{userStatus.message}</span>
                        </div>
                    )}

                    <div className="mt-4">
                        <div className="flex items-center gap-2 mb-2">
                            <Users className="w-5 h-5 text-gray-600" />
                            <h4 className="text-sm font-semibold text-gray-700">Usuários Cadastrados</h4>
                        </div>

                        {loadingUsers ? (
                            <p className="text-sm text-gray-500">Carregando usuários...</p>
                        ) : users.length === 0 ? (
                            <p className="text-sm text-gray-500">Nenhum usuário cadastrado.</p>
                        ) : (
                            <div className="space-y-2">
                                {users.map((u) => (
                                    <div
                                        key={u.nome || u}
                                        className="flex items-center justify-between bg-gray-50 px-4 py-3 rounded-lg"
                                    >
                                        <div>
                                            <p className="text-sm font-medium text-gray-800">{u.nome || u}</p>
                                            <p className="text-xs text-gray-500">Perfil: {u.perfil || 'operacional'}</p>
                                        </div>
                                        <button
                                            onClick={() => handleDeleteUser(u.nome || u)}
                                            className="text-red-500 hover:text-red-700"
                                        >
                                            <Trash2 className="w-4 h-4" />
                                        </button>
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>
                </div>
            </Card>
        </div>
    );
};

export default ConfigView;

import axios from 'axios';

// When frontend is served by the same server as the API, use empty baseURL
// This allows relative URLs like /api/dre/financeiro to work correctly
const baseURL = '';

const api = axios.create({
  baseURL,
});

export default api;

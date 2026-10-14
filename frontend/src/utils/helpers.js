// Funcoes utilitarias para o dashboard DRE

export const formatMonthName = (mesAno) => {
    if (!mesAno) return "N/A";
    const [ano, mes] = mesAno.split("-");
    const meses = {
        "01": "Janeiro",
        "02": "Fevereiro",
        "03": "Marco",
        "04": "Abril",
        "05": "Maio",
        "06": "Junho",
        "07": "Julho",
        "08": "Agosto",
        "09": "Setembro",
        "10": "Outubro",
        "11": "Novembro",
        "12": "Dezembro",
    };
    return `${meses[mes]}/${ano}`;
};

export const formatCurrency = (value) => {
    return new Intl.NumberFormat("pt-BR", {
        style: "currency",
        currency: "BRL",
    }).format(value);
};

// Normaliza texto removendo acentos e coloca em maiusculas
const normalizeText = (text) => {
    if (!text) return "";
    return text
        .normalize("NFD")
        .replace(/[\u0300-\u036f]/g, "")
        .toUpperCase();
};

export const normalizeEntradaSaida = (tipo) => {
    const value = normalizeText(tipo);

    // Alguns registros chegam com caracteres corrompidos (ex.: "SAÖDA").
    // Forçamos um mapeamento defensivo para manter a lógica de filtro.
    if (value.startsWith("SAI")) return "SAIDA";
    if (value.startsWith("ENTR")) return "ENTRADA";

    return value;
};

export const normalizeUpper = (text) => normalizeText(text);

export const getUniqueYears = (data) => {
    const years = new Set();
    data.forEach((item) => {
        const mesAno = item.MES_ANO || item.mes_ano;
        if (mesAno) {
            years.add(mesAno.split("-")[0]);
        }
    });
    return Array.from(years).sort().reverse();
};

export const getHeatmapColor = (value, min, max) => {
    if (value === null || value === undefined) return "bg-gray-100";

    if (value < 0) {
        return "bg-red-100 text-red-800";
    }

    const base = Math.max(0, min);
    const span = max - base;
    if (span === 0) {
        return "bg-green-100 text-green-800";
    }

    const normalized = (value - base) / span;

    if (normalized < 0.33) {
        return "bg-red-100 text-red-800";
    } else if (normalized < 0.67) {
        return "bg-yellow-100 text-yellow-800";
    }
    return "bg-green-100 text-green-800";
};

export const getEvolutionColor = (value) => {
    if (value === null || value === undefined || value === 0) return "text-gray-600";
    return value > 0 ? "text-green-600" : "text-red-600";
};

export const calculatePercentageChange = (current, previous) => {
    if (!previous || previous === 0) return null;
    return ((current - previous) / Math.abs(previous)) * 100;
};

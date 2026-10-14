"""
Helper functions for database queries using Supabase Client
"""
from supabase import Client


def _fetch_all(table_name: str, db: Client, chunk: int = 1000):
    """Busca todos os registros via paginação (evita limites padrão)."""
    all_rows = []
    start = 0
    while True:
        resp = (
            db.table(table_name)
            .select("*")
            .range(start, start + chunk - 1)
            .execute()
        )
        batch = resp.data or []
        all_rows.extend(batch)
        if len(batch) < chunk:
            break
        start += chunk
    return all_rows


def get_dre_financeiro_data(db: Client):
    """Get all financial DRE data"""
    return _fetch_all("dre_financeiro", db)


def get_dre_financeiro_coop(db: Client):
    """Get all financial DRE data for cooperativa"""
    return _fetch_all("dre_financeiro_coop", db)


def get_dre_gerencial_data(db: Client):
    """Get all management DRE data"""
    return _fetch_all("dre_gerencial", db)


def get_dre_gerencial_coop(db: Client):
    """Get all management DRE data for cooperativa"""
    return _fetch_all("dre_gerencial_coop", db)


def get_predictive_data(db: Client):
    """Get predictive analytics data"""
    response = db.table("predictive_data").select("data_json").eq("data_type", "main").execute()
    if response.data:
        return response.data[0]['data_json']
    return {}


def get_predictive_data_coop(db: Client):
    response = db.table("predictive_data_coop").select("data_json").eq("data_type", "main").execute()
    if response.data:
        return response.data[0]["data_json"]
    return {}


def get_adiantamentos_coop(db: Client):
    return _fetch_all("coop_adiantamentos_saldo", db)

def get_user(db: Client, nome: str):
    """Get user by name"""
    response = db.table("users").select("*").eq("nome", nome).execute()
    if response.data:
        user = response.data[0]
        if "perfil" not in user:
            user["perfil"] = "adm" if nome.lower() == "robert" else "operacional"
        return user
    return None

def create_user(db: Client, nome: str, senha: str, perfil: str = "operacional"):
    """Create new user"""
    data = {"nome": nome, "senha": senha, "perfil": perfil}
    try:
        response = db.table("users").insert(data).execute()
        return response.data[0] if response.data else None
    except Exception:
        # fallback se coluna perfil não existir
        response = db.table("users").insert({"nome": nome, "senha": senha}).execute()
        user = response.data[0] if response.data else None
        if user is not None:
            # tentar setar perfil via update se coluna existir
            try:
                db.table("users").update({"perfil": perfil}).eq("id", user.get("id")).execute()
                user["perfil"] = perfil
            except Exception:
                user["perfil"] = perfil
        return user

def delete_user(db: Client, nome: str):
    """Delete user by name"""
    response = db.table("users").delete().eq("nome", nome).execute()
    return len(response.data) > 0

def list_users(db: Client):
    """List all usernames"""
    try:
        response = db.table("users").select("nome, perfil").execute()
        users = response.data or []
    except Exception:
        # fallback se a coluna perfil não existir
        response = db.table("users").select("nome").execute()
        users = response.data or []

    for u in users:
        if "perfil" not in u:
            u["perfil"] = "operacional"
    return users

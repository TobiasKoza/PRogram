import os
import bcrypt

# --- KONFIGURACE CESTY ---
# Používáme raw string (r""), aby Windows cesty nezlobily
slozka = r"C:\Users\junio\Tenis_Web\PRogram\.streamlit"
soubor = os.path.join(slozka, "secrets.toml")

# --- DATA HRÁČŮ ---
hraci = {
    "tobi": {"name": "Tobi", "pass": "AdminTobinek21"},
    "kuba": {"name": "Kuba", "pass": "KubikJeBobik13"},
    "jirka": {"name": "Jirka", "pass": "JirkaLegendSite32"},
    "kavic": {"name": "Kávič", "pass": "KralChopu54"},
    "risa": {"name": "Ríša", "pass": "ClutchLord99"}
}

# --- GENEROVÁNÍ OBSAHU ---
toml_content = "[credentials]\nusernames = {\n"

for user_id, data in hraci.items():
    # Vytvoření hashe
    hash_hesla = bcrypt.hashpw(data["pass"].encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    toml_content += f'    {user_id} = {{ email = "{user_id}@tenis.cz", name = "{data["name"]}", password = "{hash_hesla}" }},\n'

toml_content += "}\n\n[cookie]\nexpiry_days = 30\nkey = \"tajny_klic_123\"\nname = \"tennis_elo_auth\"\n"

# --- ZÁPIS DO SOUBORU ---
try:
    # Pokud složka neexistuje, vytvoříme ji
    if not os.path.exists(slozka):
        os.makedirs(slozka)
    
    with open(soubor, "w", encoding="utf-8") as f:
        f.write(toml_content)
    
    print(f"✅ HOTOVO! Soubor byl vytvořen zde: {soubor}")
    print("Teď už by přihlašování mělo fungovat.")

except Exception as e:
    print(f"❌ CHYBA: Nepodařilo se zapsat soubor. Důvod: {e}")
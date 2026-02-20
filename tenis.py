import pandas as pd
from datetime import datetime
import os
import math
import gspread
import streamlit as st
from google.oauth2.service_account import Credentials


SHEET_NAME = "tennis_elo_template"
WORKSHEET = "tennis_elo_template"
KEYFILE = "teniselo-98a88e562ec1.json"

def get_ws():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    creds = None

    # Streamlit Cloud (Secrets) cus
    try:
        if "gcp_service_account" in st.secrets:
            creds = Credentials.from_service_account_info(
                st.secrets["gcp_service_account"],
                scopes=scopes
            )
    except Exception:
        creds = None

    # Lokálně (soubor)
    if creds is None:
        if not os.path.exists(KEYFILE):
            st.error("Chybí Streamlit Secrets (gcp_service_account) a lokální KEYFILE neexistuje.")
            st.stop()
        creds = Credentials.from_service_account_file(KEYFILE, scopes=scopes)

    gc = gspread.authorize(creds)
    return gc.open(SHEET_NAME).worksheet(WORKSHEET)


# --- KONFIGURACE ---
K_SINGLES = 24
K_DOUBLES = 36
SCALE = 400
CSV_PATH = "tennis_elo_template.csv"

INITIAL_RATINGS = {
    "Tobi": 1200, "Kuba": 1100, "Jirka": 1040, 
    "Kávič": 1040, "Ríša": 1030, "Novas": 1030
}

# --- FUNKCE PRO DATA ---
COLUMNS = ["date", "type", "team_a", "team_b", "winner", "score", "sets", "reason"]

def load_data():
    ws = get_ws()
    values = ws.get_all_values()

    if not values:
        ws.append_row(COLUMNS)
        return pd.DataFrame(columns=COLUMNS)

    header = values[0]
    rows = values[1:]
    df = pd.DataFrame(rows, columns=header).fillna("")

    # kdyby náhodou někde chyběl sloupec
    for c in COLUMNS:
        if c not in df.columns:
            df[c] = ""
    return df[COLUMNS]

def save_match(row):
    ws = get_ws()

    # doplň chybějící pole, aby byl vždy stejný tvar
    full = {c: "" for c in COLUMNS}
    full.update(row)

    ws.append_row([full[c] for c in COLUMNS], value_input_option="USER_ENTERED")
from datetime import datetime, date, timedelta

def delete_sheet_row(sheet_row: int):
    ws = get_ws()
    ws.delete_rows(sheet_row)

def compute_elo_with_meta():
    ratings = INITIAL_RATINGS.copy()
    df = load_data()
    # baseline (startovní ELO) – pro výpočet "ELO změna celkem"
    base_rating = {p: float(v) for p, v in INITIAL_RATINGS.items()}

    # pro hráče, co nejsou v INITIAL_RATINGS
    def initial_for(p: str) -> float:
        return float(INITIAL_RATINGS.get(p, 1000.0))

    last_date = {}        # p -> datetime/date string
    last_delta = {}       # p -> float (delta z posledního ELO zápasu / adjust)
    played_elo_match = set()

    for _, r in df.iterrows():
        rtype = str(r.get("type", "")).strip()

        # --- adjust ---
        if rtype == "adjust":
            p = str(r.get("team_a", "")).strip()
            if not p:
                continue

            reason = str(r.get("reason", "")).strip()

            try:
                delta = float(r.get("team_b", 0) or 0)
            except:
                delta = 0.0

            # default baseline pro hráče, který ještě nemá
            if p not in base_rating:
                base_rating[p] = initial_for(p)

            ratings[p] = ratings.get(p, base_rating[p]) + delta

            # Pokud je to "Přidání hráče(...)", ber to jako nastavení startu (baseline),
            # ne jako "změnu" do statistik
            if reason.startswith("Přidání hráče"):
                base_rating[p] = float(ratings[p])      # start = aktuální rating po adjustu
                last_delta[p] = 0.0                     # poslední změna = 0
            else:
                last_delta[p] = float(delta)

            last_date[p] = str(r.get("date", "")).strip()
            continue

        # --- ELO zápasy ---
        if rtype in ["singles", "doubles"]:
            winner = str(r.get("winner", "")).strip()
            team_a = [p.strip() for p in str(r.get("team_a", "")).split("+") if p.strip()]
            team_b = [p.strip() for p in str(r.get("team_b", "")).split("+") if p.strip()]
            if not team_a or not team_b:
                continue

            for p in team_a + team_b:
                ratings.setdefault(p, initial_for(p))

            ra = sum(ratings[p] for p in team_a) / len(team_a)
            rb = sum(ratings[p] for p in team_b) / len(team_b)
            ea = 1.0 / (1.0 + 10 ** ((rb - ra) / SCALE))
            sa = 1.0 if winner == "A" else 0.0

            k = K_SINGLES if rtype == "singles" else K_DOUBLES
            delta_team_a = k * (sa - ea)
            delta_a_each = delta_team_a / len(team_a)
            delta_b_each = (-delta_team_a) / len(team_b)

            for p in team_a:
                ratings[p] += delta_a_each
                last_delta[p] = delta_a_each
                last_date[p] = str(r.get("date", "")).strip()
                played_elo_match.add(p)

            for p in team_b:
                ratings[p] += delta_b_each
                last_delta[p] = delta_b_each
                last_date[p] = str(r.get("date", "")).strip()
                played_elo_match.add(p)

    # total delta proti startu (baseline)
    total_delta = {p: (float(ratings[p]) - float(base_rating.get(p, initial_for(p))))
                   for p in ratings.keys()}

    return ratings, last_date, total_delta, last_delta, played_elo_match

def get_all_players():
    ratings, *_ = compute_elo_with_meta()
    return sorted(list(ratings.keys()))

# --- UI STREAMLIT ---
st.set_page_config(page_title="Tennis ELO Žebříček", page_icon="🎾", layout="wide")
st.title("🎾 Tennis ELO — Zápisy a Žebříček")

# Záložky pro přepínání obsahu
tab1, tab2, tab3 = st.tabs(["🏆 Žebříček", "✍️ Zadat zápas", "📜 Historie"])



# --- TAB 1: ŽEBŘÍČEK ---
with tab1:
    st.header("Aktuální žebříček ELO")

    ratings, last_date, total_delta, last_delta, played_elo_match = compute_elo_with_meta()

    # DataFrame pro všechny
    rows = []
    for p, elo in ratings.items():
        rows.append({
            "Hráč": p,
            "ELO": round(float(elo), 2),
            "Poslední zápas": last_date.get(p, ""),
            "ELO změna celkem (poslední zápas)": f'{total_delta.get(p, 0):+.0f} ({last_delta.get(p, 0):+.0f})'
        })

    rank_df = pd.DataFrame(rows).sort_values("ELO", ascending=False).reset_index(drop=True)

    # rozdělení na aktivní/neaktivní (30 dní podle data posledního zápasu)
    def parse_cz_date(s):
        s = str(s).strip()
        if not s:
            return None
        for fmt in ("%d.%m.%Y", "%d.%m.%y"):
            try:
                return datetime.strptime(s, fmt).date()
            except:
                pass
        return None

    today = date.today()
    cutoff = today - timedelta(days=30)

    active_rows = []
    inactive_rows = []
    for i, r in rank_df.iterrows():
        d = parse_cz_date(r["Poslední zápas"])
        is_active = (d is not None and d >= cutoff and r["Hráč"] in played_elo_match)
        if is_active:
            active_rows.append(r)
        else:
            inactive_rows.append(r)

    active_df = pd.DataFrame(active_rows) if active_rows else pd.DataFrame(columns=rank_df.columns)
    inactive_df = pd.DataFrame(inactive_rows) if inactive_rows else pd.DataFrame(columns=rank_df.columns)

    # doplnění pořadí + korunka
    if not active_df.empty:
        active_df.insert(0, "#", range(1, len(active_df) + 1))
        active_df.loc[0, "Hráč"] = f"👑 {active_df.loc[0, 'Hráč']}"
    else:
        active_df.insert(0, "#", [])

    sty = active_df.style.set_properties(**{"text-align": "center"}).set_table_styles(
    [{"selector": "th", "props": [("text-align", "center")]}]
)

    try:
        st.dataframe(sty, use_container_width=True, hide_index=True)
    except TypeError:
        st.dataframe(sty.hide(axis="index"), use_container_width=True)

    st.subheader("Hráči bez zápasu za posledních 30 dní")
    if inactive_df.empty:
        st.write("Nikdo.")
    else:
        inactive_df.insert(0, "#", ["unranked"] * len(inactive_df))
        sty = inactive_df.style.set_properties(**{"text-align": "center"}).set_table_styles(
    [{"selector": "th", "props": [("text-align", "center")]}]
)

    try:
        st.dataframe(sty, use_container_width=True, hide_index=True)
    except TypeError:
        st.dataframe(sty.hide(axis="index"), use_container_width=True)


# --- TAB 2: ZADÁNÍ ZÁPASU ---
with tab2:
    all_players = get_all_players()
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("Nový zápas")
        m_type = st.radio("Typ zápasu", ["Singles", "Doubles", "Přátelák (Singles)", "Přátelák (Doubles)"])
        date = st.date_input("Datum", datetime.now())
        
        # Výběr hráčů podle typu
        if "Singles" in m_type:
            p1 = st.selectbox("Hráč A", all_players, key="s1")
            p2 = st.selectbox("Hráč B", all_players, key="s2")
            team_a, team_b = p1, p2
        else:
            c_a1, c_a2 = st.columns(2)
            with c_a1: p1a = st.selectbox("Tým A - Hráč 1", all_players)
            with c_a2: p1b = st.selectbox("Tým A - Hráč 2", all_players)
            
            c_b1, c_b2 = st.columns(2)
            with c_b1: p2a = st.selectbox("Tým B - Hráč 1", all_players)
            with c_b2: p2b = st.selectbox("Tým B - Hráč 2", all_players)
            team_a, team_b = f"{p1a}+{p1b}", f"{p2a}+{p2b}"
            
    with col2:
        st.write("") # Odsazení
        st.write("")
        winner = st.selectbox("Vítěz", ["A", "B"], format_func=lambda x: "Tým/Hráč A" if x=="A" else "Tým/Hráč B")
        score = st.text_input("Skóre (např. 2:1)", "")
        sets = st.text_input("Gemy setů (např. 6,4,6)", "")
        
        if st.button("💾 Uložit zápas", use_container_width=True):
            if ("Singles" in m_type and p1 == p2) or ("Doubles" in m_type and len(set([p1a, p1b, p2a, p2b])) != 4):
                st.error("Hráči se nesmí opakovat!")
            else:
                # Interní typy pro CSV
                if m_type == "Singles": db_type = "singles"
                elif m_type == "Doubles": db_type = "doubles"
                elif m_type == "Přátelák (Singles)": db_type = "friendly_singles"
                else: db_type = "friendly_doubles"
                
                save_match({
                    "date": date.strftime("%d.%m.%Y"),
                    "type": db_type,
                    "team_a": team_a,
                    "team_b": team_b,
                    "winner": winner,
                    "score": score,
                    "sets": sets,
                    "reason": ""
                })
                st.success("Zápas byl úspěšně uložen!")
                st.rerun()

    st.divider()
    
    # Úpravy ELO a přidání hráče
    st.subheader("Manuální úpravy a noví hráči")
    adj_col1, adj_col2 = st.columns(2)
    
    with adj_col1:
        st.write("**Upravit existující ELO**")
        adj_player = st.selectbox("Hráč", all_players, key="adj_p")
        adj_delta = st.number_input("Změna (např. 5 nebo -3)", step=1, value=0)
        adj_reason = st.text_input("Důvod úpravy")
        if st.button("Upravit ELO"):
            save_match({"date": datetime.now().strftime("%d.%m.%Y"), "type": "adjust", "team_a": adj_player, "team_b": adj_delta, "reason": adj_reason})
            st.rerun()
            
    with adj_col2:
        st.write("**Přidat nového hráče**")
        new_name = st.text_input("Jméno nového hráče")
        new_elo = st.number_input("Startovní ELO", value=1000, step=10)
        if st.button("Přidat hráče"):
            if new_name and new_name not in all_players:
                delta = new_elo - 1000
                save_match({"date": datetime.now().strftime("%d.%m.%Y"), "type": "adjust", "team_a": new_name, "team_b": delta, "reason": f"Přidání hráče({new_elo} ELO)"})
                st.success(f"Hráč {new_name} přidán!")
                st.rerun()
            elif new_name in all_players:
                st.error("Tento hráč už existuje.")

# --- TAB 3: HISTORIE ---
with tab3:
    st.header("Kompletní historie zápasů")

    df_hist = load_data()

    if df_hist.empty:
        st.info("Historie je prázdná.")
    else:
        # přidej číslo řádku v Google Sheetu (1 = hlavička, data začínají na řádku 2)
        df_hist = df_hist.copy()
        df_hist["_sheet_row"] = range(2, len(df_hist) + 2)

        # view od nejnovějšího
        view = df_hist.iloc[::-1].reset_index(drop=True)

        # výběr zápasu ke smazání
        def _label(r):
            ta = str(r["team_a"])
            tb = str(r["team_b"])
            dt = str(r["date"])
            tp = str(r["type"])
            wn = str(r.get("winner", ""))
            sc = str(r.get("score", ""))
            return f"{dt} | {tp} | {ta} vs {tb} | W:{wn} | {sc}"

        options = list(view.index)
        sel = st.selectbox(
            "Vyber zápas k odstranění",
            options=options,
            format_func=lambda i: _label(view.loc[i]),
        )

        colA, colB = st.columns([1, 3])
        with colA:
            confirm = st.checkbox("Potvrzuji smazání", value=False)

        with colB:
            if st.button("🗑️ Smazat vybraný zápas a přepočítat ELO", use_container_width=True, disabled=not confirm):
                sheet_row = int(view.loc[sel, "_sheet_row"])
                delete_sheet_row(sheet_row)
                st.success("Smazáno. ELO se přepočítalo z historie.")
                st.rerun()

        # tabulka historie bez levého indexu + bez pomocného sloupce
        show = view.drop(columns=["_sheet_row"])

        sty = show.style.set_properties(**{"text-align": "center"}).set_table_styles(
            [{"selector": "th", "props": [("text-align", "center")]}]
        )

        try:
            st.dataframe(sty, use_container_width=True, hide_index=True)
        except TypeError:
            st.dataframe(sty.hide(axis="index"), use_container_width=True)
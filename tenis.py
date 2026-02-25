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

    # Streamlit Cloud (Secrets)
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


def compute_elo_with_meta():
    ratings = INITIAL_RATINGS.copy()
    df = load_data()

    # meta
    last_date = {p: None for p in ratings}
    total_delta = {p: 0.0 for p in ratings}
    last_delta = {p: 0.0 for p in ratings}
    played_elo_match = {p: False for p in ratings}

    def parse_team(s: str):
        return [x.strip() for x in str(s).split("+") if x.strip()]

    def parse_date(s: str):
        try:
            return datetime.strptime(str(s).strip(), "%d.%m.%Y").date()
        except:
            return None

    for _, r in df.iterrows():
        rtype = str(r.get("type", "")).strip()
        d = parse_date(r.get("date", ""))

        # adjust
        if rtype == "adjust":
            p = str(r.get("team_a", "")).strip()
            try:
                delta = float(r.get("team_b", 0))
            except:
                delta = 0.0

            ratings.setdefault(p, 1000.0)
            last_date.setdefault(p, None)
            total_delta.setdefault(p, 0.0)
            last_delta.setdefault(p, 0.0)
            played_elo_match.setdefault(p, False)

            ratings[p] += delta
            total_delta[p] += delta
            last_delta[p] = delta
            if d:
                last_date[p] = d
            continue

        # matches (ranked)
        if rtype in ["singles", "doubles"]:
            team_a = parse_team(r.get("team_a", ""))
            team_b = parse_team(r.get("team_b", ""))
            winner = str(r.get("winner", "")).strip()

            for p in team_a + team_b:
                ratings.setdefault(p, 1000.0)
                last_date.setdefault(p, None)
                total_delta.setdefault(p, 0.0)
                last_delta.setdefault(p, 0.0)
                played_elo_match.setdefault(p, False)

            ra = sum(ratings[p] for p in team_a) / len(team_a)
            rb = sum(ratings[p] for p in team_b) / len(team_b)
            ea = 1.0 / (1.0 + 10 ** ((rb - ra) / SCALE))
            sa = 1.0 if winner == "A" else 0.0

            k = K_SINGLES if rtype == "singles" else K_DOUBLES
            delta = k * (sa - ea)

            # před update
            before = {p: ratings[p] for p in team_a + team_b}

            for p in team_a:
                ratings[p] += delta / len(team_a)
            for p in team_b:
                ratings[p] -= delta / len(team_b)

            # after + meta
            for p in team_a + team_b:
                ch = ratings[p] - before[p]
                total_delta[p] += ch
                last_delta[p] = ch
                played_elo_match[p] = True
                if d:
                    last_date[p] = d
            continue

        # friendly -> jen aktualizace posledního zápasu (bez ELO změn)
        if rtype in ["friendly_singles", "friendly_doubles"]:
            team_a = parse_team(r.get("team_a", ""))
            team_b = parse_team(r.get("team_b", ""))
            for p in team_a + team_b:
                ratings.setdefault(p, 1000.0)
                last_date.setdefault(p, None)
                total_delta.setdefault(p, 0.0)
                last_delta.setdefault(p, 0.0)
                played_elo_match.setdefault(p, False)
                if d:
                    last_date[p] = d

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
# --- TAB 1: ŽEBŘÍČEK ---
with tab1:
    st.header("Aktuální žebříček ELO")

    ratings, last_date, total_delta, last_delta, played_elo_match = compute_elo_with_meta()

    rows = []
    for p, elo in ratings.items():
        ld = last_date.get(p)
        ld_str = ld.strftime("%d.%m.%Y") if ld else ""
        td = total_delta.get(p, 0.0)
        ldel = last_delta.get(p, 0.0)

        rows.append({
            "Hráč": p,
            "ELO": int(round(float(elo))),  # celé číslo
            "Poslední zápas": ld_str,
            "ELO změna celkem (poslední zápas)": f"{td:+.0f} ({ldel:+.0f})",
        })

    rank_df = pd.DataFrame(rows)

    # ranked hráči = aspoň jeden ranked match (singles/doubles)
    rank_df["__ranked"] = rank_df["Hráč"].apply(lambda x: bool(played_elo_match.get(x, False)))

    ranked_df = rank_df[rank_df["__ranked"]].copy()
    unranked_df = rank_df[~rank_df["__ranked"]].copy()

    ranked_df = ranked_df.sort_values("ELO", ascending=False).reset_index(drop=True)
    ranked_df.insert(0, "#", range(1, len(ranked_df) + 1))

    if not ranked_df.empty:
        ranked_df.iloc[0, ranked_df.columns.get_loc("Hráč")] = f"👑 {ranked_df.iloc[0]['Hráč']}"

    # 30 dní bez zápasu (poslední datum < dnes-30 nebo prázdné)
    today = datetime.now().date()
    cutoff = today.toordinal() - 30

    def _date_ord(s):
        try:
            return datetime.strptime(s, "%d.%m.%Y").date().toordinal()
        except:
            return -10**9

    ranked_df["__ld_ord"] = ranked_df["Poslední zápas"].apply(_date_ord)

    active_df = ranked_df[ranked_df["__ld_ord"] >= cutoff].drop(columns=["__ranked", "__ld_ord"])
    inactive_ranked_df = ranked_df[ranked_df["__ld_ord"] < cutoff].drop(columns=["__ranked", "__ld_ord"])

    # unranked sekce (bez #)
    unranked_df = unranked_df.sort_values("ELO", ascending=False).reset_index(drop=True)
    if not unranked_df.empty:
        unranked_df.insert(0, "#", ["unranked"] * len(unranked_df))
    unranked_df = unranked_df.drop(columns=["__ranked"])

    st.subheader("Aktuální žebříček ELO")
    st.dataframe(active_df, use_container_width=True, hide_index=True)

    st.subheader("Hráči bez zápasu za posledních 30 dní")
    inactive_df = pd.concat([inactive_ranked_df, unranked_df], ignore_index=True)
    st.dataframe(inactive_df, use_container_width=True, hide_index=True)

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
    # Zobrazení od nejnovějšího
    st.dataframe(df_hist.iloc[::-1], use_container_width=True)
import pandas as pd
import os
import math
import gspread
import streamlit as st
from google.oauth2.service_account import Credentials
from datetime import datetime, timedelta

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
    return gc.open_by_url("https://docs.google.com/spreadsheets/d/18By2jSoHEXI1WLCBYh8YXnMaCtfPNM1GsruV-pfdsXI/edit").sheet1
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

    # startovní ELO pro výpočet total_delta
    base = {p: float(v) for p, v in ratings.items()}

    last_date = {}          # poslední zápas (singles/doubles/friendly)
    total_delta = {}        # finální - start
    last_delta = {}         # poslední změna (ranked/adjust; friendly=0)
    played_elo_match = {}   # měl někdy ranked match (singles/doubles)

    def parse_team(s: str):
        return [x.strip() for x in str(s).split("+") if x.strip()]

    def parse_date(s: str):
        try:
            return datetime.strptime(str(s).strip(), "%d.%m.%Y").date()
        except:
            return None

    def ensure_player(p: str):
        ratings.setdefault(p, 1000.0)
        base.setdefault(p, 1000.0)
        last_date.setdefault(p, None)
        last_delta.setdefault(p, 0.0)
        played_elo_match.setdefault(p, False)

    for _, r in df.iterrows():
        rtype = str(r.get("type", "")).strip()
        d = parse_date(r.get("date", ""))

        # --- adjust ---
        if rtype == "adjust":
            p = str(r.get("team_a", "")).strip()
            try:
                delta = float(r.get("team_b", 0))
            except:
                delta = 0.0

            ensure_player(p)
            ratings[p] += delta
            last_delta[p] = delta
            if d:
                last_date[p] = d
            continue

        # --- friendly ---
        if rtype in ["friendly_singles", "friendly_doubles"]:
            team_a = parse_team(r.get("team_a", ""))
            team_b = parse_team(r.get("team_b", ""))

            for p in team_a + team_b:
                ensure_player(p)
                last_delta[p] = 0.0
                if d:
                    last_date[p] = d
            continue

        # --- ranked matches ---
        if rtype in ["singles", "doubles"]:
            team_a = parse_team(r.get("team_a", ""))
            team_b = parse_team(r.get("team_b", ""))
            winner = str(r.get("winner", "")).strip()

            for p in team_a + team_b:
                ensure_player(p)

            ra = sum(ratings[p] for p in team_a) / max(1, len(team_a))
            rb = sum(ratings[p] for p in team_b) / max(1, len(team_b))
            ea = 1.0 / (1.0 + 10 ** ((rb - ra) / SCALE))
            sa = 1.0 if winner == "A" else 0.0

            k = K_SINGLES if rtype == "singles" else K_DOUBLES
            delta = k * (sa - ea)

            da = delta / max(1, len(team_a))
            db = -delta / max(1, len(team_b))

            for p in team_a:
                ratings[p] += da
                last_delta[p] = da
                played_elo_match[p] = True
                if d:
                    last_date[p] = d

            for p in team_b:
                ratings[p] += db
                last_delta[p] = db
                played_elo_match[p] = True
                if d:
                    last_date[p] = d

    # total delta = finální - start (base)
    for p in ratings.keys():
        ensure_player(p)
        total_delta[p] = ratings[p] - base.get(p, 1000.0)

    return ratings, last_date, total_delta, last_delta, played_elo_match

def parse_ddmmyyyy(s: str):
    s = str(s or "").strip()
    try:
        return datetime.strptime(s, "%d.%m.%Y").date()
    except:
        return None

MATCH_TYPES = {"singles", "doubles", "friendly_singles", "friendly_doubles"}


def get_all_players():
    ratings, *_ = compute_elo_with_meta()
    return sorted(list(ratings.keys()))

def build_player_history(df, target):
    ratings = INITIAL_RATINGS.copy()
    ratings.setdefault(target, 1000.0)
    
    hist = []
    
    for _, r in df.iterrows():
        rtype = str(r.get("type", "")).strip()
        rawd = str(r.get("date", "")).strip()
        winner = str(r.get("winner", "")).strip()
        score = str(r.get("score", "")).strip()
        sets_raw = str(r.get("sets", "")).strip()
        reason = str(r.get("reason", "")).strip()
        
        # 1. Manuální úpravy
        if rtype == "adjust":
            p = str(r.get("team_a", "")).strip()
            try:
                delta = float(r.get("team_b", 0))
            except:
                continue
            
            ratings[p] = ratings.get(p, 1000.0) + delta
            
            if p == target:
                is_add_player = reason.startswith("Přidání hráče")
                if is_add_player:
                    hist.append({
                        "Datum": rawd, "Typ": "Přidání hráče", "Zápas": f"Nastaveno na {int(round(ratings[target]))}",
                        "Výsledek": "", "Skóre": "", "Sety": "", "Rozdíl ELO": "", "ELO po": round(ratings[target], 2)
                    })
                else:
                    hist.append({
                        "Datum": rawd, "Typ": "Úprava ELO", "Zápas": f"Manuální úprava — {reason}".strip(' —'),
                        "Výsledek": "", "Skóre": "", "Sety": "", "Rozdíl ELO": f"{'+' if delta >= 0 else ''}{int(delta)}", "ELO po": round(ratings[target], 2)
                    })
            continue
            
        # 2. Zápasy
        if rtype in ["singles", "doubles", "friendly_singles", "friendly_doubles"]:
            team_a = [p.strip() for p in str(r.get("team_a", "")).split("+") if p.strip()]
            team_b = [p.strip() for p in str(r.get("team_b", "")).split("+") if p.strip()]
            if not team_a or not team_b: continue
            
            for p in team_a + team_b:
                ratings.setdefault(p, 1000.0)
            
            is_friendly = "friendly" in rtype
            base_type = "Singles" if "singles" in rtype else "Doubles"
            
            ra = sum(ratings[p] for p in team_a) / len(team_a)
            rb = sum(ratings[p] for p in team_b) / len(team_b)
            ea = 1.0 / (1.0 + 10 ** ((rb - ra) / SCALE))
            sa = 1.0 if winner == "A" else 0.0
            
            k = 0 if is_friendly else (K_SINGLES if "singles" in rtype else K_DOUBLES)
            delta_a = k * (sa - ea)
            delta_b = -delta_a
            
            per_player_delta = {}
            for p in team_a: per_player_delta[p] = delta_a / len(team_a)
            for p in team_b: per_player_delta[p] = delta_b / len(team_b)
                
            for p, d in per_player_delta.items():
                ratings[p] = ratings.get(p, 1000.0) + d
                
            if target in per_player_delta:
                d_pl = per_player_delta[target]
                res = "Výhra" if ((winner == "A" and target in team_a) or (winner == "B" and target in team_b)) else "Prohra"
                match_txt = f"{' + '.join(team_a)} 🆚 {' + '.join(team_b)}"
                
                hist.append({
                    "Datum": rawd,
                    "Typ": "Přátelák" if is_friendly else base_type,
                    "Zápas": match_txt,
                    "Výsledek": res,
                    "Skóre": score,
                    "Sety": sets_raw,
                    "Rozdíl ELO": "" if is_friendly else f"{'+' if round(d_pl) >= 0 else ''}{int(round(d_pl))}",
                    "ELO po": round(ratings[target], 2)
                })
                
    if not hist:
        return pd.DataFrame(columns=["Datum", "Typ", "Zápas", "Výsledek", "Skóre", "Sety", "Rozdíl ELO", "ELO po"])
        
    return pd.DataFrame(hist).iloc[::-1]

# --- UI STREAMLIT ---
st.set_page_config(page_title="Tennis ELO Žebříček", page_icon="🎾", layout="wide")
st.title("🎾 Tennis ELO — Zápisy a Žebříček")

# Záložky pro přepínání obsahu
tab1, tab2, tab3 = st.tabs(["🏆 Žebříček", "✍️ Zadat zápas", "📜 Historie"])

# --- TAB 1: ŽEBŘÍČEK ---
with tab1:
    st.markdown("""
    <style>
    /* udělá z horizontal radio řádek se scrollováním */
    div[role="radiogroup"]{
    flex-wrap: nowrap !important;
    overflow-x: auto !important;
    padding-bottom: 6px;
    }
    div[role="radiogroup"] label{
    white-space: nowrap !important;
    }
    </style>
    """, unsafe_allow_html=True)
    st.header("Aktuální žebříček ELO")

    ratings, last_date, total_delta, last_delta, played_elo_match = compute_elo_with_meta()

    rows = []
    for p, elo in ratings.items():
        ld = last_date.get(p)
        ld_str = ld.strftime("%d.%m.%Y") if ld else ""
        td = total_delta.get(p, 0.0)
        ldel = last_delta.get(p, 0.0)

        is_ranked = bool(played_elo_match.get(p, False))  # ranked = někdy odehrál singles/doubles

        rows.append({
            "Hráč": p,
            "__ranked": is_ranked,
            "__elo_num": int(round(float(elo))),  # pro řazení ranked
            "ELO": int(round(float(elo))),        # dočasně, u unranked přepíšeme
            "Poslední zápas": ld_str,
            "ELO změna celkem (poslední zápas)": f"{td:+.0f} ({ldel:+.0f})",
        })

    rank_df = pd.DataFrame(rows)

    # rozdělení ranked / unranked
    ranked_df = rank_df[rank_df["__ranked"]].copy()
    unranked_df = rank_df[~rank_df["__ranked"]].copy()

    # --- 30 dní bez zápasu (jen pro ranked hráče, protože unranked jdou vždy dolů) ---
    today = datetime.now().date()
    cutoff_date = today - timedelta(days=30)

    def _parse_date(s):
        try:
            return datetime.strptime(s, "%d.%m.%Y").date()
        except:
            return None

    ranked_df["__ld"] = ranked_df["Poslední zápas"].apply(_parse_date)

    active_ranked_df = ranked_df[(ranked_df["__ld"].notna()) & (ranked_df["__ld"] >= cutoff_date)].copy()
    inactive_ranked_df = ranked_df[(ranked_df["__ld"].isna()) | (ranked_df["__ld"] < cutoff_date)].copy()

    # --- HORNÍ TABULKA = jen active ranked ---
    active_ranked_df = active_ranked_df.sort_values("__elo_num", ascending=False).reset_index(drop=True)
    active_ranked_df.insert(0, "#", range(1, len(active_ranked_df) + 1))

    if not active_ranked_df.empty:
        active_ranked_df.iloc[0, active_ranked_df.columns.get_loc("Hráč")] = f"👑 {active_ranked_df.iloc[0]['Hráč']}"

    active_out = active_ranked_df.drop(columns=["__ranked", "__elo_num", "__ld"])

    st.subheader("Aktuální žebříček ELO")
    st.dataframe(active_out.style.set_properties(**{'text-align': 'center'}).set_table_styles([{'selector': 'th', 'props': [('text-align', 'center')]}]), use_container_width=False, hide_index=True)

    # --- SPODNÍ TABULKA = inactive ranked + unranked ---
    inactive_ranked_df = inactive_ranked_df.sort_values("__elo_num", ascending=False).reset_index(drop=True)
    inactive_ranked_df.insert(0, "#", ["inactive"] * len(inactive_ranked_df))

    inactive_ranked_out = inactive_ranked_df.drop(columns=["__ranked", "__elo_num", "__ld"])

    # unranked: rank = "unranked", ELO = "0(0)" (jen zobrazení)
    unranked_df = unranked_df.sort_values("__elo_num", ascending=False).reset_index(drop=True)
    unranked_df.insert(0, "#", ["unranked"] * len(unranked_df))
    unranked_df["ELO"] = "0(0)"
    unranked_out = unranked_df.drop(columns=["__ranked", "__elo_num"])

    st.subheader("Hráči bez zápasu za posledních 30 dní")
    inactive_out = pd.concat([inactive_ranked_out, unranked_out], ignore_index=True)
    st.dataframe(inactive_out.style.set_properties(**{'text-align': 'center'}).set_table_styles([{'selector': 'th', 'props': [('text-align', 'center')]}]), use_container_width=False, hide_index=True)

    df_all = load_data()
    all_players = sorted(list(set(list(ratings.keys()))))

    col_sel, _ = st.columns([3, 7])
    with col_sel:
        picked = st.selectbox(
            "Vyber hráče pro zobrazení historie:",
            options=all_players,
            index=all_players.index(st.session_state.get("selected_player")) if st.session_state.get("selected_player") in all_players else 0,
        )

    st.session_state["selected_player"] = picked
    st.subheader(f"Historie hráče: {picked}")

    hist_df = build_player_history(df_all, picked)

    if hist_df.empty:
        st.info("Bez zápasů.")
    else:
        st.dataframe(hist_df.style.set_properties(**{'text-align': 'center'}).set_table_styles([{'selector': 'th', 'props': [('text-align', 'center')]}]), use_container_width=False, hide_index=True)



# --- TAB 2: ZADÁNÍ ZÁPASU ---
with tab2:
    all_players = get_all_players()
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("Nový zápas")
        m_type = st.radio("Typ zápasu", ["Singles", "Doubles"])
        is_friendly = st.checkbox("Přátelák (nezapočítává se do ELO)")
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
        # V selectboxu se zobrazí konkrétní jména, ale do kódu se uloží jen "A" nebo "B"
        winner = st.selectbox("Vítěz", ["A", "B"], format_func=lambda x: team_a if x == "A" else team_b)
        score = st.text_input("Skóre (např. 2:1)", "")
        sets = st.text_input("Gemy setů (např. 6,4,6)", "")
        
        if st.button("💾 Uložit zápas", use_container_width=True):
            if ("Singles" in m_type and p1 == p2) or ("Doubles" in m_type and len(set([p1a, p1b, p2a, p2b])) != 4):
                st.error("Hráči se nesmí opakovat!")
            else:
                # Interní typy pro CSV
                if m_type == "Singles":
                    db_type = "friendly_singles" if is_friendly else "singles"
                else:
                    db_type = "friendly_doubles" if is_friendly else "doubles"
                
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
    st.dataframe(df_hist.iloc[::-1].style.set_properties(**{'text-align': 'center'}).set_table_styles([{'selector': 'th', 'props': [('text-align', 'center')]}]), use_container_width=False)
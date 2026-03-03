import pandas as pd
import os
import math
import gspread
import streamlit as st
import streamlit_authenticator as stauth
import base64
import calendar
import plotly.express as px
import streamlit.components.v1 as components
from google.oauth2.service_account import Credentials
from datetime import datetime, timedelta

# --- GLOBÁLNÍ POMOCNÉ FUNKCE (Musí být nahoře) ---
def get_players(team_str):
    """Rozdělí řetězec týmu (např. 'Tobi+Kuba') na seznam jmen."""
    return [p.strip() for p in str(team_str).split("+") if p.strip()]

def parse_ddmmyyyy(s: str):
    """Bezpečně převede text na datum."""
    s = str(s or "").strip()
    try:
        return datetime.strptime(s, "%d.%m.%Y").date()
    except:
        return None

# --- KONFIGURACE ---
SHEET_NAME = "tennis_elo_template"
WORKSHEET = "tennis_elo_template"
KEYFILE = "teniselo-98a88e562ec1.json"
K_SINGLES = 24
K_DOUBLES = 36
SCALE = 400

INITIAL_RATINGS = {
    "Tobi": 1200, "Kuba": 1100, "Jirka": 1040, 
    "Kávič": 1040, "Ríša": 1030, "Novas": 1030
}

COLUMNS = ["date", "type", "team_a", "team_b", "winner", "score", "sets", "reason", "author"]

@st.cache_resource
def get_ws():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = None
    try:
        if "gcp_service_account" in st.secrets:
            creds = Credentials.from_service_account_info(
                st.secrets["gcp_service_account"],
                scopes=scopes
            )
    except Exception:
        creds = None
    if creds is None:
        creds = Credentials.from_service_account_file(KEYFILE, scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open_by_url("https://docs.google.com/spreadsheets/d/18By2jSoHEXI1WLCBYh8YXnMaCtfPNM1GsruV-pfdsXI/edit")
    return sh.sheet1
    
@st.cache_data(ttl=10)
def load_data():
    ws = get_ws()
    values = ws.get_all_values()

    if not values:
        ws.append_row(COLUMNS)
        return pd.DataFrame(columns=COLUMNS)

    header = values[0]
    rows = values[1:]
    df = pd.DataFrame(rows, columns=header).fillna("")

    for c in COLUMNS:
        if c not in df.columns:
            df[c] = ""
    return df[COLUMNS]

def save_match(row):
    ws = get_ws()

    full = {c: "" for c in COLUMNS}
    full.update(row)

    ws.append_row([full[c] for c in COLUMNS], value_input_option="USER_ENTERED")

    st.cache_data.clear() # Vymaže veškerou paměť aplikace (data i výpočty)

def delete_match_by_row(row_index):
    if row_index is None or str(row_index) == 'nan' or row_index == "":
        st.error("Chyba: Nepodařilo se identifikovat řádek v databázi.")
        return
    
    try:
        ws = get_ws()
        # Převod na int a smazání
        idx = int(float(row_index)) 
        ws.delete_rows(idx)
        st.cache_data.clear()
    except Exception as e:
        st.error(f"Chyba při mazání v Google Sheets: {e}")

@st.cache_data(ttl=600)
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
        author = str(r.get("author", "")).strip()
        
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





@st.cache_data(ttl=600)
def build_full_history(df: pd.DataFrame) -> pd.DataFrame:
    ratings = INITIAL_RATINGS.copy()
    
    # 1. Příprava dat a indexů řádků
    tmp = df.copy()
    tmp["sheet_row"] = tmp.index + 2 
    
    def parse_team(s: str):
        return [x.strip() for x in str(s).split("+") if x.strip()]

    def parse_date(s: str):
        try:
            return datetime.strptime(str(s).strip(), "%d.%m.%Y").date()
        except:
            return None

    # Pomocná funkce definovaná přímo zde, aby ji build_full_history viděla
    def ensure_player(p: str):
        ratings.setdefault(p, 1000.0)

    # Řazení
    tmp["__dt"] = tmp["date"].apply(parse_date)
    tmp = tmp.sort_values("__dt", ascending=True).drop(columns=["__dt"])

    out = []

    for _, r in tmp.iterrows():
        rtype = str(r.get("type", "")).strip()
        rawd = str(r.get("date", "")).strip()
        winner = str(r.get("winner", "")).strip()
        score = str(r.get("score", "")).strip()
        reason = str(r.get("reason", "")).strip()
        author = str(r.get("author", "")).strip()

        # --- ADJUST ---
        if rtype == "adjust":
            p = str(r.get("team_a", "")).strip()
            if not p: continue

            try:
                delta = float(r.get("team_b", 0))
            except:
                delta = 0.0

            ensure_player(p)
            ratings[p] = ratings.get(p, 1000.0) + delta

            is_add_player = reason.startswith("Přidání hráče")
            if is_add_player:
                typ, zapas, duvod = "Přidání hráče", f"{p} — Nastaveno na {int(round(ratings[p]))}", reason
            else:
                typ, zapas, duvod = "Úprava ELO", f"{p} (Změna: {'+' if delta >= 0 else ''}{int(delta)})", reason

            out.append({
                "Datum": rawd, "Typ": typ, "Zápas": zapas, "Důvod": duvod,
                "Výsledek": "", "Skóre": "", "Zapsal": author, "row_idx": r["sheet_row"]
            })
            continue

        # --- MATCH ---
        if rtype in ["singles", "doubles", "friendly_singles", "friendly_doubles"]:
            team_a = parse_team(r.get("team_a", ""))
            team_b = parse_team(r.get("team_b", ""))
            if not team_a or not team_b: continue

            for p in team_a + team_b: ensure_player(p)

            is_friendly = "friendly" in rtype
            typ = "Přátelák" if is_friendly else ("Singles" if "singles" in rtype else "Doubles")

            ra = sum(ratings[p] for p in team_a) / max(1, len(team_a))
            rb = sum(ratings[p] for p in team_b) / max(1, len(team_b))
            ea = 1.0 / (1.0 + 10 ** ((rb - ra) / SCALE))
            sa = 1.0 if winner == "A" else 0.0

            k = 0 if is_friendly else (K_SINGLES if "singles" in rtype else K_DOUBLES)
            delta = k * (sa - ea)
            da, db = delta / max(1, len(team_a)), -delta / max(1, len(team_b))

            for p in team_a: ratings[p] += da
            for p in team_b: ratings[p] += db

            vysledek = f"Vítěz: {' + '.join(team_a if winner == 'A' else team_b)}" if winner in ["A", "B"] else "Remíza"

            out.append({
                "Datum": rawd, "Typ": typ, "Zápas": f"{' + '.join(team_a)} 🆚 {' + '.join(team_b)}",
                "Důvod": "", "Výsledek": vysledek, "Skóre": score, "Zapsal": author,
                "row_idx": r["sheet_row"]  # <--- Tohle je klíčové pro smazání!
            })

    if not out:
        return pd.DataFrame(columns=["Datum", "Typ", "Zápas", "Důvod", "Výsledek", "Skóre", "Zapsal", "row_idx"])

    return pd.DataFrame(out).iloc[::-1].reset_index(drop=True)

def get_last_matches(df: pd.DataFrame, n: int = 5) -> pd.DataFrame:
    def _dt(s):
        try:
            return datetime.strptime(str(s).strip(), "%d.%m.%Y")
        except:
            return datetime.min

    m = df[df["type"].isin(["singles", "doubles", "friendly_singles", "friendly_doubles"])].copy()
    if m.empty:
        return pd.DataFrame(columns=["Datum", "Typ", "Zápas", "Vítěz", "Skóre"])

    m["__dt"] = m["date"].apply(_dt)
    m = m.sort_values("__dt", ascending=False).head(n)

    def _pretty_type(t):
        if t == "singles": return "Singles"
        if t == "doubles": return "Doubles"
        if t == "friendly_singles": return "Přátelák S"
        if t == "friendly_doubles": return "Přátelák D"
        return t

    def _pretty_match(a, b):
        a = str(a)
        b = str(b)
        if len(a) > 14: a = a[:14] + "…"
        if len(b) > 14: b = b[:14] + "…"
        return f"{a} vs {b}"

    def _pretty_winner(row):
        return row["team_a"] if row["winner"] == "A" else row["team_b"]

    out = pd.DataFrame({
        "Datum": m["date"],
        "Typ": m["type"].apply(_pretty_type),
        "Zápas": [_pretty_match(a, b) for a, b in zip(m["team_a"], m["team_b"])],
        "Vítěz": m.apply(_pretty_winner, axis=1),
        "Skóre": m["score"],
    })

    return out

@st.cache_data(ttl=600)
def compute_player_stats_cached(df: pd.DataFrame, current_user: str):
    """
    Vrátí hotové tabulky + pomocné struktury pro Tab 'Statistika hráče'.
    """
    def get_players(team_str):
        return [p.strip() for p in str(team_str).split("+") if p.strip()]

    MATCH_TYPES = {"singles", "doubles", "friendly_singles", "friendly_doubles"}

    singles_opponents = {}
    doubles_partners = {}
    doubles_opponents = {}

    for _, r in df.iterrows():
        if r["type"] not in MATCH_TYPES:
            continue

        ta = get_players(r["team_a"])
        tb = get_players(r["team_b"])
        win = r["winner"]

        if current_user not in ta and current_user not in tb:
            continue

        my_team = ta if current_user in ta else tb
        opp_team = tb if current_user in ta else ta

        is_win = (current_user in ta and win == "A") or (current_user in tb and win == "B")
        is_loss = (current_user in ta and win == "B") or (current_user in tb and win == "A")

        # Singles
        if "singles" in r["type"] and len(my_team) == 1 and len(opp_team) == 1:
            opp = opp_team[0]
            if opp not in singles_opponents:
                singles_opponents[opp] = {"w": 0, "l": 0}
            if is_win:
                singles_opponents[opp]["w"] += 1
            elif is_loss:
                singles_opponents[opp]["l"] += 1

        # Doubles
        if "doubles" in r["type"] and len(my_team) == 2 and len(opp_team) == 2:
            partner = my_team[0] if my_team[1] == current_user else my_team[1]
            if partner not in doubles_partners:
                doubles_partners[partner] = {"w": 0, "l": 0}
            if is_win:
                doubles_partners[partner]["w"] += 1
            elif is_loss:
                doubles_partners[partner]["l"] += 1

            opp_key = f"{opp_team[0]} + {opp_team[1]}"
            if opp_key not in doubles_opponents:
                doubles_opponents[opp_key] = {"w": 0, "l": 0}
            if is_win:
                doubles_opponents[opp_key]["w"] += 1
            elif is_loss:
                doubles_opponents[opp_key]["l"] += 1

    def build_stat_df(stat_dict, col_name, sort_by="games"):
        rows = []
        for k, v in stat_dict.items():
            g = v["w"] + v["l"]
            pct = (v["w"] / g * 100) if g > 0 else 0
            rows.append({
                col_name: k,
                "Zápasů": g,
                "Výhry": v["w"],
                "Prohry": v["l"],
                "__pct": pct,  # Skrytý sloupec pro matematické řazení
                "Úspěšnost": f"{pct:.1f} %".replace('.', ',')
            })
            
        if not rows:
            return pd.DataFrame(columns=[col_name, "Zápasů", "Výhry", "Prohry", "Úspěšnost"])
            
        df = pd.DataFrame(rows)
        
        if sort_by == "pct":
            # Dvouhra: Primárně % úspěšnosti, sekundárně počet zápasů
            df = df.sort_values(["__pct", "Zápasů"], ascending=[False, False])
        else:
            # Čtyřhra: Primárně počet zápasů, sekundárně % úspěšnosti
            df = df.sort_values(["Zápasů", "__pct"], ascending=[False, False])
            
        # Odstraníme skrytý sloupec před vykreslením
        return df.drop(columns=["__pct"]).reset_index(drop=True)

    # Tady je definované to nové řazení
    df_singles = build_stat_df(singles_opponents, "Soupeř (Singles)", sort_by="pct")
    df_d_partners = build_stat_df(doubles_partners, "Parťák (Doubles)", sort_by="games")
    df_d_opponents = build_stat_df(doubles_opponents, "Soupeři (Doubles)", sort_by="games")

    return (
        df_singles,
        df_d_partners,
        df_d_opponents,
        singles_opponents,
        doubles_partners,
        doubles_opponents
    )

import streamlit.components.v1 as components

def render_player_calendar(match_details, year, month):
    # match_details je slovník {datetime.date: "popis zápasů"}
    cal = calendar.Calendar(firstweekday=0)
    try:
        month_days = cal.monthdatescalendar(year, month)
    except:
        return "<div style='color:red;'>Chyba kalendáře</div>"
        
    month_names_cz = ["Leden","Únor","Březen","Duben","Květen","Červen","Červenec","Srpen","Září","Říjen","Listopad","Prosinec"]
    month_name = month_names_cz[month-1]
    today = datetime.now().date()

    html = []
    html.append("""
    <style>
    .cal-grid { display:grid; grid-template-columns:repeat(7, 1fr); gap:5px; max-width:280px; margin:auto; font-family:sans-serif; }
    .day-cell { 
        aspect-ratio:1/1; display:flex; align-items:center; justify-content:center; 
        font-size:12px; position: relative; cursor: default;
    }
    .tooltip {
        visibility: hidden; width: 160px; background-color: rgba(0,0,0,0.95); color: #fff;
        text-align: center; border-radius: 8px; padding: 8px; position: absolute;
        z-index: 100; bottom: 125%; left: 50%; margin-left: -80px; opacity: 0;
        transition: opacity 0.2s; border: 1px solid #2ecc71; font-size: 11px; line-height: 1.4;
        pointer-events: none; box-shadow: 0 4px 15px rgba(0,0,0,0.5);
    }
    .day-cell:hover .tooltip { visibility: visible; opacity: 1; }
    </style>
    """)

    html.append(f"<div style='text-align:center; margin-bottom:10px; font-weight:bold; color:#2ecc71; font-size:18px; font-family:sans-serif;'>{month_name} {year}</div>")
    html.append("<div class='cal-grid'>")

    for day_name in ["Po","Út","St","Čt","Pá","So","Ne"]:
        html.append(f"<div style='font-size:10px; color:gray; text-align:center;'>{day_name}</div>")

    for week in month_days:
        for day in week:
            match_info = match_details.get(day)
            is_match = match_info is not None
            is_today = (day == today)
            is_current_month = (day.month == month)

            bg = "rgba(46, 204, 113, 0.5)" if is_match else "rgba(255,255,255,0.05)"
            border = "1px solid #2ecc71" if is_match else "1px solid rgba(255,255,255,0.1)"
            opacity = "1" if is_current_month else "0.2"
            color = "white" if is_current_month else "gray"
            radius = "50%" if is_match else "4px"
            shadow = "box-shadow: 0 0 10px rgba(255,255,255,0.5);" if is_today else ""

            tooltip_html = f"<span class='tooltip'>{match_info}</span>" if is_match else ""

            html.append(
                f"<div class='day-cell' style='background:{bg}; border:{border}; border-radius:{radius}; "
                f"color:{color}; opacity:{opacity}; {shadow}'>"
                f"{day.day}{tooltip_html}</div>"
            )
    html.append("</div>")
    return "".join(html)


# --- UI STREAMLIT ---
st.set_page_config(page_title="Tennis ELO Žebříček", page_icon="🎾", layout="wide")
# --- NOVÝ OPRAVENÝ BLOK NADPISU ---
def get_base64_image(image_filename):
    # Najde cestu ke složce, kde běží skript
    dir_path = os.path.dirname(os.path.realpath(__file__))
    img_path = os.path.join(dir_path, image_filename)
    
    if os.path.exists(img_path):
        with open(img_path, "rb") as img_file:
            return base64.b64encode(img_file.read()).decode()
    return None

# Pokus o načtení loga (správný název souboru)
img_data = get_base64_image("logo_tenis.png")

if img_data:
    # Změněno na image/png
    img_html = f'<img src="data:image/png;base64,{img_data}" style="height: 70px; margin-right: 20px; border-radius: 12px; box-shadow: 0 4px 12px rgba(0,0,0,0.5);">'
else:
    # Záloha pokud se obrázek nenajde
    img_html = "🎾 "

# Vykreslení nadpisu v moderním obdélníku
st.markdown(f"""
    <div style="
        background: linear-gradient(135deg, rgba(255, 255, 255, 0.08) 0%, rgba(255, 255, 255, 0.02) 100%);
        border: 1px solid rgba(255, 255, 255, 0.12);
        border-radius: 20px;
        padding: 25px;
        display: flex;
        align-items: center;
        justify-content: center;
        margin-bottom: 30px;
        box-shadow: 0 10px 30px rgba(0,0,0,0.4);
    ">
        {img_html}
        <h1 style="
            margin: 0;
            padding: 0;
            color: #ffffff;
            font-family: 'Segoe UI', sans-serif;
            letter-spacing: 1.5px;
            text-transform: uppercase;
            font-size: 34px;
            font-weight: 900;
            text-shadow: 2px 2px 4px rgba(0,0,0,0.3);
        ">
            TENIS ELO — Zápisy a žebříčky
        </h1>
    </div>
""", unsafe_allow_html=True)

# --- PŘIHLAŠOVÁNÍ (Levý panel) ---
credentials = st.secrets["credentials"].to_dict()

# Inicializace přihlášení s fixní hodnotou 30 dní
# Tímto odpadají veškeré chyby s mizející cookie při stisku F5
authenticator = stauth.Authenticate(
    credentials,
    st.secrets["cookie"]["name"],
    st.secrets["cookie"]["key"],
    30  # Natvrdo nastaveno 30 dní platnosti (přežije F5 i zavření prohlížeče)
)

# Vykreslení přihlašovacího formuláře (Jméno, Heslo, tlačítko Login)
authenticator.login(location="sidebar")

# Zpracování stavu
if st.session_state.get("authentication_status"):
    authenticator.logout("Odhlásit se", location="sidebar")
    st.sidebar.success(f'Přihlášen jako: **{st.session_state["name"]}**')
elif st.session_state.get("authentication_status") is False:
    st.sidebar.error('Špatné uživatelské jméno nebo heslo')
elif st.session_state.get("authentication_status") is None:
    st.sidebar.warning('Pro zápis výsledků se přihlas')

def bar(text: str):
    st.markdown(f'<div class="section-bar">{text}</div>', unsafe_allow_html=True)

# Záložky pro přepínání obsahu
# Zjištění jména pro dynamický název záložky
if st.session_state.get("authentication_status"):
    stat_tab_name = f"📊 Statistika hráče: {st.session_state.get('name')}"
else:
    stat_tab_name = "📊 Statistika hráče"

# Záložky pro přepínání obsahu
tab1, tab_sd, tab_stats, tab2, tab3 = st.tabs(["🏆 Žebříček", "🎾 Singles & Doubles", stat_tab_name, "✍️ Zadat zápas nebo přidat hráče", "📜 Kompletní historie"])

# načti sheet JEDNOU pro celý run
DF_ALL = load_data()
# --- TAB 1: ŽEBŘÍČEK ---
with tab1:
    st.markdown("""
    <style>
    .section-bar{
      background: rgba(255,255,255,0.07);
      border: 1px solid rgba(255,255,255,0.10);
      padding: 10px 14px;
      border-radius: 12px;
      text-align: center;
      font-weight: 800;
      font-size: 22px;
      margin: 8px 0 10px 0;
    }

    /* Společný styl pro všechny vlastní HTML tabulky (vzhled z Tab 3) */
    .hist-wrap {
      width: 100%;
      overflow-x: auto;
      border: 1px solid rgba(255,255,255,0.08);
      border-radius: 12px;
      background: rgba(0,0,0,0.10);
      margin-bottom: 20px;
    }
    .hist-wrap table {
      border-collapse: collapse;
      table-layout: auto;
      width: max-content;
      min-width: 100%;
      color: rgba(255,255,255,0.90);
      margin: 0;
    }
    .hist-wrap thead th {
      position: sticky;
      top: 0;
      background: rgba(255,255,255,0.06) !important;
      border-bottom: 1px solid rgba(255,255,255,0.10) !important;
      font-weight: 800 !important;
      text-align: center !important;
    }
    .hist-wrap th, .hist-wrap td {
      padding: 10px 12px;
      border-right: 1px solid rgba(255,255,255,0.06);
      border-bottom: 1px solid rgba(255,255,255,0.06);
      white-space: nowrap;
      text-align: center !important;
      font-size: 12.5px !important;
    }
    .hist-wrap th:last-child, .hist-wrap td:last-child {
      border-right: none;
    }
    .hist-wrap tr:last-child td {
      border-bottom: none;
    }
    /* Skrytí prázdného th z pandas styleru (index sloupec) */
    .hist-wrap .blank { display: none; }
    .hist-wrap .row_heading { display: none; }
    </style>
    """, unsafe_allow_html=True)

    ratings, last_date, total_delta, last_delta, played_elo_match = compute_elo_with_meta()

    rows = []
    for p, elo in ratings.items():
        ld = last_date.get(p)
        ld_str = ld.strftime("%d.%m.%Y") if ld else "—"
        td = total_delta.get(p, 0.0)
        ldel = last_delta.get(p, 0.0)

        is_ranked = bool(played_elo_match.get(p, False))  # ranked = někdy odehrál singles/doubles

        rows.append({
            "Hráč": p,
            "__ranked": is_ranked,
            "__elo_num": int(round(float(elo))),  # pro řazení ranked
            "ELO": round(float(elo), 2),
            "Poslední zápas": ld_str,
            "Δ ELO (posl.)": f"{td:+.0f} ({ldel:+.0f})",
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

    df_all = DF_ALL
    # pravá tabulka má mít stejný počet řádků jako levá

    # --- SPODNÍ TABULKA = inactive ranked + unranked (dáme do jedné tabulky s hlavní) ---
    inactive_ranked_df = inactive_ranked_df.sort_values("__elo_num", ascending=False).reset_index(drop=True)
    inactive_ranked_df.insert(0, "#", ["unranked"] * len(inactive_ranked_df))
    inactive_ranked_out = inactive_ranked_df.drop(columns=["__ranked", "__elo_num", "__ld"])
    
    inactive_ranked_out["ELO"] = "0"
    inactive_ranked_out["Δ ELO (posl.)"] = "0 (0)"

    unranked_df = unranked_df.sort_values("__elo_num", ascending=False).reset_index(drop=True)
    unranked_df.insert(0, "#", ["unranked"] * len(unranked_df))
    unranked_out = unranked_df.drop(columns=["__ranked", "__elo_num"])
    unranked_out["ELO"] = "0"
    unranked_out["Δ ELO (posl.)"] = "0 (0)"

    # separator řádek (vizuálně "sloučený" – ostatní buňky zneviditelníme stylem)
    sep = {c: " " for c in active_out.columns}
    sep["#"] = " "
    sep["Hráč"] = "Hráči bez zápasu za posledních 30 dní"
    sep["ELO"] = " "
    sep["Poslední zápas"] = " "
    sep["Δ ELO (posl.)"] = " "
    sep_row = pd.DataFrame([sep])

    players_out = pd.concat([active_out, sep_row, inactive_ranked_out, unranked_out], ignore_index=True)

    DELTA_COL = "Δ ELO (posl.)"

    def _delta_color(v):
        s = str(v).strip()
        if not s:
            return ""
        try:
            main = s.split("(", 1)[0].strip()
            n = float(main.replace("+", "").replace("−", "-"))
        except:
            return ""
        if n > 0:
            return "color: #2ecc71; font-weight: 700;"
        if n < 0:
            return "color: #e74c3c; font-weight: 700;"
        return ""

    def _row_style(row):
        # separator řádek (uvnitř tabulky)
        if str(row.get("Hráč", "")).strip() == "Hráči bez zápasu za posledních 30 dní":
            return [
                "background-color: rgba(255,255,255,0.09);"
                "color: rgba(255,255,255,0.55);"
                "font-weight: 800;"
            ] * len(row)

        # inactive + unranked řádky (zašedlé)
        if str(row.get("#", "")).strip() in ["inactive", "unranked"]:
            return [
                "color: rgba(255,255,255,0.55);"
                "background-color: rgba(255,255,255,0.03);"
            ] * len(row)

        return [""] * len(row)

    # sloučený efekt separatoru: všechny buňky kromě "Hráč" v tom řádku zneviditelníme
    def _sep_hide_cells(row):
        if str(row.get("Hráč", "")).strip() == "Hráči bez zápasu za posledních 30 dní":
            out = []
            for col in players_out.columns:
                if col == "Hráč":
                    out.append("text-align: center;")
                else:
                    out.append("color: rgba(255,255,255,0.0);")
            return out
        return [""] * len(row)

    # --- místo pandas styleru vykresli HTML, a separator udělej colspan přes všechny sloupce ---
    cols = list(players_out.columns)
    ncols = len(cols)

    def _esc(x):
        return str(x).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    parts = []
    parts.append('<div class="hist-wrap"><table class="hist-table">')

    # header
    parts.append("<thead><tr>")
    for c in cols:
        parts.append(f"<th>{_esc(c)}</th>")
    parts.append("</tr></thead>")

    # body
    parts.append("<tbody>")
    for _, row in players_out.iterrows():
        is_sep = str(row.get("Hráč", "")).strip() == "Hráči bez zápasu za posledních 30 dní"
        is_unranked = str(row.get("#", "")).strip() == "unranked"

        if is_sep:
            parts.append(
                f'<tr>'
                f'<td colspan="{ncols}" style="background-color: rgba(255,255,255,0.09); color: rgba(255,255,255,0.55); font-weight: 800; text-align: center;">'
                f'Hráči bez zápasu za posledních 30 dní'
                f'</td>'
                f'</tr>'
            )
            continue

        parts.append("<tr>")
        for c in cols:
            v = row.get(c, "")
            row_style = 'color: rgba(255,255,255,0.55); background-color: rgba(255,255,255,0.03);' if is_unranked else ""

            # 1. Barvení sloupce "Poslední zápas" (zelená, žlutá, červená)
            if c == "Poslední zápas":
                d_obj = _parse_date(str(v))
                d_color = ""
                if d_obj:
                    days_ago = (today - d_obj).days
                    if days_ago <= 10: d_color = "color: #2ecc71; font-weight: 700;"    # Zelená
                    elif days_ago <= 20: d_color = "color: #f1c40f; font-weight: 700;"  # Žlutá
                    elif days_ago <= 30: d_color = "color: #e74c3c; font-weight: 700;"  # Červená
                
                parts.append(f'<td style="{row_style}{d_color}">{_esc(v)}</td>')
                continue

            # 2. Barvení Δ sloupce (původní logika)
            if c == DELTA_COL:
                s = str(v).strip()
                style = ""
                try:
                    main = s.split("(", 1)[0].strip()
                    n = float(main.replace("+", "").replace("−", "-"))
                    if n > 0: style = "color: #2ecc71; font-weight: 700;"
                    elif n < 0: style = "color: #e74c3c; font-weight: 700;"
                except: style = ""
                parts.append(f'<td style="{row_style}{style}">{_esc(v)}</td>')
                continue

            # Ostatní sloupce
            parts.append(f'<td style="{row_style}">{_esc(v)}</td>')

        parts.append("</tr>")
    parts.append("</tbody></table></div>")

    html_left = "".join(parts)

    left, right = st.columns([3, 2], gap="large")

    with left:
        st.markdown('<div class="section-bar">Aktuální žebříček ELO</div>', unsafe_allow_html=True)
        st.markdown(html_left, unsafe_allow_html=True)

    with right:
        right_n = len(players_out)

        match_types = ["singles", "doubles", "friendly_singles", "friendly_doubles"]
        available_matches = int(df_all["type"].isin(match_types).sum())
        shown_n = min(right_n, available_matches)

        lastN_df = get_last_matches(df_all, n=right_n)

        if len(lastN_df) < right_n:
            pad = pd.DataFrame([{"Datum": "", "Typ": "", "Zápas": "", "Vítěz": "", "Skóre": ""}] * (right_n - len(lastN_df)))
            lastN_df = pd.concat([lastN_df, pad], ignore_index=True)

        st.markdown(f'<div class="section-bar">Posledních {shown_n} zápasů</div>', unsafe_allow_html=True)
        html_right = lastN_df.to_html(index=False, border=0, escape=True)
        st.markdown(f'<div class="hist-wrap">{html_right}</div>', unsafe_allow_html=True)

    df_all = DF_ALL
    all_players = sorted(list(set(list(ratings.keys()))))

    col_sel, _ = st.columns([3, 7])
    with col_sel:
        picked = st.selectbox(
            "Vyber hráče pro zobrazení historie:",
            options=all_players,
            index=None,  # Tímhle říkáme "nevybírej nikoho na začátku"
            placeholder="— nevybráno —",
            key="history_player_sel"
        )

    # Historii vykreslíme POUZE pokud je vybrán nějaký hráč
    if picked:
        st.subheader(f"Historie hráče: {picked}")

        hist_df = build_player_history(df_all, picked)

        if hist_df.empty:
            st.info("Bez zápasů.")
        else:
            def _res_color(v):
                s = str(v).strip().lower()
                if s == "výhra":
                    return "color: #2ecc71; font-weight: 800;"
                if s == "prohra":
                    return "color: #e74c3c; font-weight: 800;"
                return ""

            hist_styler = (
                hist_df.style
                    .hide(axis="index")
                    .format({"ELO po": "{:.2f}"})
                    .applymap(_res_color, subset=["Výsledek"])
            )

            html_hist = hist_styler.to_html()
            st.markdown(f'<div class="hist-wrap">{html_hist}</div>', unsafe_allow_html=True)
    else:
        st.info("Vyber hráče ze seznamu nahoře pro zobrazení jeho osobní historie.")

# --- TAB 1.5: SINGLES A DOUBLES ---
with tab_sd:
    df_sd = DF_ALL
    ratings_sd, *_ = compute_elo_with_meta()
    
    # Session state pro přepínání tlačítek
    if "sd_view" not in st.session_state:
        st.session_state["sd_view"] = "Singles"

    # Stylovaná obdélníková tlačítka vedle sebe
    col_btn1, col_btn2, _ = st.columns([1, 1, 4])
    with col_btn1:
        if st.button("🎾 Singles", use_container_width=True, type="primary" if st.session_state["sd_view"] == "Singles" else "secondary"):
            st.session_state["sd_view"] = "Singles"
            st.rerun()
    with col_btn2:
        if st.button("👥 Doubles", use_container_width=True, type="primary" if st.session_state["sd_view"] == "Doubles" else "secondary"):
            st.session_state["sd_view"] = "Doubles"
            st.rerun()

    st.markdown("<br>", unsafe_allow_html=True)

    # --- SINGLES ---
    if st.session_state["sd_view"] == "Singles":
        bar("Žebříček Singles")
        s_matches = df_sd[df_sd["type"] == "singles"]
        s_stats = {}
        
        for _, r in s_matches.iterrows():
            p1, p2 = r["team_a"].strip(), r["team_b"].strip()
            win = r["winner"].strip()
            if p1 not in s_stats: s_stats[p1] = {"w": 0, "l": 0}
            if p2 not in s_stats: s_stats[p2] = {"w": 0, "l": 0}
            if win == "A":
                s_stats[p1]["w"] += 1; s_stats[p2]["l"] += 1
            elif win == "B":
                s_stats[p2]["w"] += 1; s_stats[p1]["l"] += 1
                
        s_rows = []
        max_s_games = max([st_s["w"] + st_s["l"] for st_s in s_stats.values()]) if s_stats else 0
        s_threshold = max_s_games / 3.0
        
        for p, st_s in s_stats.items():
            w, l = st_s["w"], st_s["l"]
            g = w + l
            pct = (w / g * 100) if g > 0 else 0
            elo_val = ratings_sd.get(p, 1000)
            s_rows.append({
                "Hráč": p,
                "__games": g,
                "__pct": pct,
                "__wins": w,
                "ELO": int(round(elo_val)),
                "Skóre": f"{w}:{l}",
                "Úspěšnost": f"{pct:.1f}".replace('.', ',') + " %"
            })
            
        s_df = pd.DataFrame(s_rows)
        if not s_df.empty:
            # Řazení: 1. úspěšnost, 2. počet výher
            s_active = s_df[s_df["__games"] >= s_threshold].sort_values(["__pct", "__wins"], ascending=[False, False]).reset_index(drop=True)
            s_active.insert(0, "#", range(1, len(s_active) + 1))
            s_active = s_active.drop(columns=["__games", "__pct", "__wins"])
            
            s_inactive = s_df[s_df["__games"] < s_threshold].sort_values(["__pct", "__wins"], ascending=[False, False]).reset_index(drop=True)
            s_inactive.insert(0, "#", range(1, len(s_inactive) + 1))
            s_inactive = s_inactive.drop(columns=["__games", "__pct", "__wins"])
            
            sep_s = {c: " " for c in s_active.columns}
            sep_s["#"] = " "
            s_limit_text = f"Hráči s méně než {int(math.ceil(s_threshold))} zápasy"
            sep_s["Hráč"] = s_limit_text
            sep_s_row = pd.DataFrame([sep_s])
            
            if s_inactive.empty:
                s_out = s_active
            elif s_active.empty:
                s_out = s_inactive
            else:
                s_out = pd.concat([s_active, sep_s_row, s_inactive], ignore_index=True)
                
            def _s_row_style(row):
                if str(row.get("Hráč", "")).strip() == s_limit_text:
                    return ["background-color: rgba(255,255,255,0.09); color: rgba(255,255,255,0.55); font-weight: 800;"] * len(row)
                if str(row.get("#", "")).strip() != " " and str(row.get("Hráč", "")).strip() in s_inactive["Hráč"].values:
                    return ["color: rgba(255,255,255,0.55); background-color: rgba(255,255,255,0.03);"] * len(row)
                return [""] * len(row)
                
            def _s_hide_cells(row):
                if str(row.get("Hráč", "")).strip() == s_limit_text:
                    return ["text-align: center;" if c == "Hráč" else "color: rgba(255,255,255,0.0);" for c in s_out.columns]
                return [""] * len(row)
                
            html_s = s_out.style.hide(axis="index").apply(_s_row_style, axis=1).apply(_s_hide_cells, axis=1).to_html()
            st.markdown(f'<div class="hist-wrap">{html_s}</div>', unsafe_allow_html=True)
        else:
            st.info("Zatím žádné zápasy.")

    # --- DOUBLES ---
    if st.session_state["sd_view"] == "Doubles":
        bar("Žebříček Doubles")
        d_matches = df_sd[df_sd["type"] == "doubles"]
        d_stats = {}

        for _, r in d_matches.iterrows():
            ta = [x.strip() for x in r["team_a"].split("+") if x.strip()]
            tb = [x.strip() for x in r["team_b"].split("+") if x.strip()]
            if len(ta) != 2 or len(tb) != 2:
                continue

            ta_key = " + ".join(sorted(ta))
            tb_key = " + ".join(sorted(tb))
            win = r["winner"].strip()

            if ta_key not in d_stats:
                d_stats[ta_key] = {"w": 0, "l": 0, "p1": ta[0], "p2": ta[1]}
            if tb_key not in d_stats:
                d_stats[tb_key] = {"w": 0, "l": 0, "p1": tb[0], "p2": tb[1]}

            if win == "A":
                d_stats[ta_key]["w"] += 1
                d_stats[tb_key]["l"] += 1
            elif win == "B":
                d_stats[tb_key]["w"] += 1
                d_stats[ta_key]["l"] += 1

        d_rows = []
        max_d_games = max([st_d["w"] + st_d["l"] for st_d in d_stats.values()]) if d_stats else 0
        d_threshold = max_d_games / 3.0

        for d_k, st_d in d_stats.items():
            w, l = st_d["w"], st_d["l"]
            g = w + l
            pct = (w / g * 100) if g > 0 else 0
            avg_elo = (ratings_sd.get(st_d["p1"], 1000) + ratings_sd.get(st_d["p2"], 1000)) / 2.0
            d_rows.append({
                "Dvojice": d_k,
                "__games": g,
                "__pct": pct,
                "__wins": w,
                "Průměrné ELO": int(round(avg_elo)),
                "Skóre": f"{w}:{l}",
                "Úspěšnost": f"{pct:.1f}".replace('.', ',') + " %"
            })

        d_df = pd.DataFrame(d_rows)

        if d_df.empty:
            st.info("Zatím žádné zápasy.")
        else:
            # Řazení: 1. úspěšnost, 2. počet výher
            d_active = d_df[d_df["__games"] >= d_threshold].sort_values(["__pct", "__wins"], ascending=[False, False]).reset_index(drop=True)
            d_active.insert(0, "#", range(1, len(d_active) + 1))
            d_active = d_active.drop(columns=["__games", "__pct", "__wins"])

            d_inactive = d_df[d_df["__games"] < d_threshold].sort_values(["__pct", "__wins"], ascending=[False, False]).reset_index(drop=True)
            d_inactive.insert(0, "#", range(1, len(d_inactive) + 1))
            d_inactive = d_inactive.drop(columns=["__games", "__pct", "__wins"])

            d_limit_text = f"Dvojice s méně než {int(math.ceil(d_threshold))} zápasy"

            # poskládej data tak, aby separator byl samostatný marker řádek
            if d_inactive.empty:
                d_out = d_active.copy()
            elif d_active.empty:
                d_out = d_inactive.copy()
            else:
                sep_row = pd.DataFrame([{"#": "__SEP__", "Dvojice": d_limit_text, "Průměrné ELO": "", "Skóre": "", "Úspěšnost": ""}])
                d_out = pd.concat([d_active, sep_row, d_inactive], ignore_index=True)

            # vygeneruj HTML tabulku a separatoru nastav colspan přes všechny sloupce
            cols = list(d_out.columns)
            ncols = len(cols)

            parts = []
            parts.append('<div class="hist-wrap"><table class="hist-table">')

            # header
            parts.append("<thead><tr>")
            for c in cols:
                parts.append(f"<th>{str(c)}</th>")
            parts.append("</tr></thead>")

            # body
            parts.append("<tbody>")
            for _, row in d_out.iterrows():
                is_sep = str(row.get("#", "")).strip() == "__SEP__"
                if is_sep:
                    parts.append(
                        f'<tr>'
                        f'<td colspan="{ncols}" style="background-color: rgba(255,255,255,0.09); color: rgba(255,255,255,0.55); font-weight: 800; text-align: center;">'
                        f'{d_limit_text}'
                        f'</td>'
                        f'</tr>'
                    )
                    continue

                # běžné řádky
                parts.append("<tr>")
                for c in cols:
                    v = row.get(c, "")
                    parts.append(f"<td>{str(v)}</td>")
                parts.append("</tr>")
            parts.append("</tbody></table></div>")

            st.markdown("".join(parts), unsafe_allow_html=True)

# --- TAB STATISTIKY PŘIHLÁŠENÉHO HRÁČE ---
with tab_stats:
    if not st.session_state.get("authentication_status"):
        st.warning("⚠️ Pro zobrazení osobních statistik se musíš přihlásit v levém panelu.")
    else:
        current_user = st.session_state.get("name")
        bar(f"Statistiky hráče: {current_user}")

        # --- 1. POMOCNÉ FUNKCE (Hned na začátku, aby se předešlo NameError) ---
        def get_players(team_str):
            return [p.strip() for p in str(team_str).split("+") if p.strip()]

        def get_player_season_stats(player_name, data):
            w, l = 0, 0
            for _, r in data.iterrows():
                if r["type"] not in ["singles", "doubles", "friendly_singles", "friendly_doubles"]: continue
                ta, tb = get_players(r["team_a"]), get_players(r["team_b"])
                if player_name in ta:
                    if r["winner"] == "A": w += 1
                    elif r["winner"] == "B": l += 1
                elif player_name in tb:
                    if r["winner"] == "B": w += 1
                    elif r["winner"] == "A": l += 1
            return w, l

        # --- 2. INICIALIZACE A NAVIGACE KALENDÁŘE ---
        if "cal_month" not in st.session_state:
            st.session_state.cal_month = datetime.now().month
            st.session_state.cal_year = datetime.now().year

        # --- 3. VÝPOČET DAT PRO KALENDÁŘ (S TOOLTIPY) ---
        match_details = {}
        all_match_dates = []
        for _, r in DF_ALL.iterrows():
            if r["type"] not in ["singles", "doubles", "friendly_singles", "friendly_doubles"]: continue
            ta_list, tb_list = get_players(r["team_a"]), get_players(r["team_b"])
            if current_user in ta_list or current_user in tb_list:
                d_obj = parse_ddmmyyyy(r["date"])
                if d_obj:
                    all_match_dates.append(d_obj)
                    # Sestavení popisku pro mini okenko
                    txt = f"<b>{r['team_a']} vs {r['team_b']}</b><br>Skóre: {r['score']}"
                    if d_obj in match_details:
                        match_details[d_obj] += f"<hr style='margin:5px 0; border:0; border-top:1px solid rgba(255,255,255,0.2)'>{txt}"
                    else:
                        match_details[d_obj] = txt

        # --- 4. VYKRESLENÍ KALENDÁŘE A ELO GRAFU ---
        col_cal, col_info = st.columns([1.2, 2])
        
        with col_cal:
            # Tlačítka pro změnu měsíce (elegantnější)
            st.markdown("""
                <style>
                /* zúží a zjemní jen tyhle dvě šipky (nejde 100% cílit jen klíčem, tak to držíme lokálně velikostí) */
                .cal-nav-wrap { display:flex; justify-content:space-between; align-items:center; margin: 2px 0 10px 0; }
                </style>
            """, unsafe_allow_html=True)

            c_nav1, c_nav2, c_nav3 = st.columns([0.9, 4.2, 0.9], vertical_alignment="center")

            with c_nav1:
                if st.button("‹", key="btn_prev_m", use_container_width=True, type="secondary"):
                    st.session_state.cal_month -= 1
                    if st.session_state.cal_month < 1:
                        st.session_state.cal_month = 12
                        st.session_state.cal_year -= 1
                    st.rerun()

            with c_nav2:
                # jen vycentrovaná mezera (nadpis měsíce je přímo v kalendáři)
                st.write("")

            with c_nav3:
                if st.button("›", key="btn_next_m", use_container_width=True, type="secondary"):
                    st.session_state.cal_month += 1
                    if st.session_state.cal_month > 12:
                        st.session_state.cal_month = 1
                        st.session_state.cal_year += 1
                    st.rerun()

            cal_html = render_player_calendar(match_details, st.session_state.cal_year, st.session_state.cal_month)
            components.html(cal_html, height=320)
            
        with col_info:
            count = len([d for d in all_match_dates if d.month == st.session_state.cal_month and d.year == st.session_state.cal_year])

            # názvy měsíců ve tvaru "v měsíci <...>"
            month_loc_cz = ["lednu","únoru","březnu","dubnu","květnu","červnu","červenci","srpnu","září","říjnu","listopadu","prosinci"]
            month_loc = month_loc_cz[st.session_state.cal_month - 1]

            # Česká gramatika
            word = "zápas" if count == 1 else ("zápasy" if 1 < count < 5 else "zápasů")
            
            st.markdown(f"""
                <div style="padding: 15px; color: rgba(255,255,255,0.8); font-size: 14px; background: rgba(255,255,255,0.03); border-radius: 12px; border-left: 4px solid #2ecc71;">
                    V měsíci {month_loc} {st.session_state.cal_year} jsi odehrál <b>{count}</b> {word}.<br>
                    <span style="font-size: 12px; opacity: 0.7;">Najeď myší na zelený den pro detail zápasu.</span>
                </div>
                <div style="height: 30px;"></div>
            """, unsafe_allow_html=True)
            
            # Interaktivní ELO Graf (Plotly) s fixní osou
            hist_df_graph = build_player_history(DF_ALL, current_user)
            if not hist_df_graph.empty:
                graph_data = hist_df_graph.iloc[::-1].copy()
                min_elo, max_elo = graph_data["ELO po"].min(), graph_data["ELO po"].max()
                
                fig = px.line(graph_data, x="Datum", y="ELO po", markers=True, color_discrete_sequence=["#2ecc71"])
                fig.update_layout(
                    height=230, margin=dict(l=0, r=0, t=10, b=0),
                    paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
                    yaxis_title=None, xaxis_title=None,
                    yaxis_range=[min_elo - 10, max_elo + 10]
                )
                fig.update_xaxes(showgrid=False, color="gray", tickfont=dict(size=10))
                fig.update_yaxes(showgrid=True, gridcolor="rgba(255,255,255,0.05)", color="gray", tickfont=dict(size=10))
                st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})

        st.write("")
        # Načtení cache tabulek pro H2H
        (df_singles, df_d_partners, df_d_opponents, singles_opponents, 
         doubles_partners, doubles_opponents) = compute_player_stats_cached(DF_ALL, current_user)

        # Horní přehledové tabulky
        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown("**🆚 Dvouhra (Proti)**")
            st.dataframe(df_singles, use_container_width=True, hide_index=True)
        with c2:
            st.markdown("**🤝 Čtyřhra (Parťák)**")
            st.dataframe(df_d_partners, use_container_width=True, hide_index=True)
        with c3:
            st.markdown("**⚔️ Čtyřhra (Proti)**")
            st.dataframe(df_d_opponents, use_container_width=True, hide_index=True)

        st.divider()
        st.subheader("🔍 Detailní rozbory (H2H)")
        
        if "sel_opp" not in st.session_state: st.session_state.sel_opp = None
        if "sel_partner" not in st.session_state: st.session_state.sel_partner = None
        
        col_sel_s, col_sel_d = st.columns(2)
        with col_sel_s:
            st.selectbox("🎯 Detail soupeře (Dvouhra):", options=sorted(list(singles_opponents.keys())), 
                         index=None, placeholder="— vyber soupeře —", key="sel_opp")
        with col_sel_d:
            st.selectbox("🤝 Detail parťáka (Čtyřhra):", options=sorted(list(doubles_partners.keys())), 
                         index=None, placeholder="— vyber parťáka —", key="sel_partner")
        
        # --- LOGIKA VZÁJEMNÝCH ZÁPASŮ (DVOUHRA) ---
        if st.session_state.sel_opp:
            selected_opp = st.session_state.sel_opp
            p1_w, p1_l = get_player_season_stats(current_user, DF_ALL)
            p2_w, p2_l = get_player_season_stats(selected_opp, DF_ALL)
            h2h_w = singles_opponents[selected_opp]["w"]
            h2h_l = singles_opponents[selected_opp]["l"]
            h2h_g = h2h_w + h2h_l
            
            st.markdown(f"""
            <div style="background: rgba(255,255,255,0.05); padding: 20px; border-radius: 12px; border: 1px solid rgba(255,255,255,0.1); margin-top: 10px;">
                <h3 style="text-align: center; margin-top: 0;">Vzájemné zápasy: {current_user} vs {selected_opp}</h3>
                <div style="display: flex; justify-content: space-between; text-align: center; margin-top: 20px;">
                    <div style="width: 30%;"><p><b>{h2h_g}</b></p><p style="color: #2ecc71;">{h2h_w}</p><p style="color: #e74c3c;">{h2h_l}</p></div>
                    <div style="width: 30%; color: gray;"><p>Zápasů</p><p>Výhry</p><p>Prohry</p></div>
                    <div style="width: 30%;"><p><b>{h2h_g}</b></p><p style="color: #2ecc71;">{h2h_l}</p><p style="color: #e74c3c;">{h2h_w}</p></div>
                </div>
            </div>
            """, unsafe_allow_html=True)

            h2h_matches = []
            for _, r in DF_ALL.iterrows():
                if "singles" not in r["type"]: continue
                ta, tb = get_players(r["team_a"]), get_players(r["team_b"])
                if (current_user in ta and selected_opp in tb) or (current_user in tb and selected_opp in ta):
                    winner_name = ta[0] if r["winner"] == "A" else tb[0]
                    h2h_matches.append({
                        "Datum": r["date"], "Zápas": f"{ta[0]} vs {tb[0]}", "Vítěz": winner_name, 
                        "Skóre": r["score"], "Sety": str(r["sets"]).replace("'", "")
                    })
            if h2h_matches:
                df_h2h = pd.DataFrame(h2h_matches).iloc[::-1]
                st.dataframe(df_h2h.style.map(lambda x: 'color: #2ecc71; font-weight: bold;' if x == current_user else ('color: #e74c3c; font-weight: bold;' if x == selected_opp else ''), subset=['Vítěz']), use_container_width=True, hide_index=True)

        # --- LOGIKA VZÁJEMNÝCH ZÁPASŮ (ČTYŘHRA) ---
        if st.session_state.sel_partner:
            selected_partner = st.session_state.sel_partner
            pw, pl = doubles_partners[selected_partner]["w"], doubles_partners[selected_partner]["l"]
            
            st.markdown(f"""
            <div style="background: rgba(255,255,255,0.05); padding: 20px; border-radius: 12px; border: 1px solid rgba(255,255,255,0.1); margin-top: 10px;">
                <h3 style="text-align: center; margin-top: 0; color: #f1c40f;">Společná bilance: {current_user} & {selected_partner}</h3>
                <div style="display: flex; justify-content: space-around; text-align: center; margin-top: 20px;">
                    <div><p style="margin:5px 0; color: gray;">Zápasů</p><p style="margin:5px 0; font-size: 20px;"><b>{pw+pl}</b></p></div>
                    <div><p style="margin:5px 0; color: gray;">Výhry</p><p style="margin:5px 0; color: #2ecc71; font-size: 20px;"><b>{pw}</b></p></div>
                    <div><p style="margin:5px 0; color: gray;">Prohry</p><p style="margin:5px 0; color: #e74c3c; font-size: 20px;"><b>{pl}</b></p></div>
                </div>
            </div>
            """, unsafe_allow_html=True)
            
            partner_matches = []
            opponents_set = set()
            for _, r in DF_ALL.iterrows():
                if "doubles" not in r["type"]: continue
                ta, tb = get_players(r["team_a"]), get_players(r["team_b"])
                we_ta, we_tb = (current_user in ta and selected_partner in ta), (current_user in tb and selected_partner in tb)
                if we_ta or we_tb:
                    opps_str = " + ".join(sorted(tb if we_ta else ta))
                    opponents_set.add(opps_str)
                    is_win = (we_ta and r["winner"] == "A") or (we_tb and r["winner"] == "B")
                    partner_matches.append({
                        "Datum": r["date"], "Soupeři": opps_str, "Výsledek": "Výhra" if is_win else "Prohra", 
                        "Skóre": r["score"], "Sety": str(r["sets"]).replace("'", "")
                    })
            
            st.markdown("---")
            selected_d_opp = st.selectbox("⚔️ Head-to-Head proti dvojici:", options=sorted(list(opponents_set)), index=None, placeholder="— všichni soupeři —", key="h2h_d_opp")
            display_m = [m for m in partner_matches if m["Soupeři"] == selected_d_opp] if selected_d_opp else partner_matches
            if display_m:
                st.dataframe(pd.DataFrame(display_m).iloc[::-1].style.map(lambda x: 'color: #2ecc71; font-weight: bold;' if x == 'Výhra' else ('color: #e74c3c; font-weight: bold;' if x == 'Prohra' else ''), subset=['Výsledek']), use_container_width=True, hide_index=True)

# --- TAB 2: ZADÁNÍ ZÁPASU ---
with tab2:
    if st.session_state.get("authentication_status"):
        # VŠECHNO pod tímto řádkem je nyní odsazené, takže se zobrazí jen přihlášeným
        
        # 1. Zobrazení vyskakovacích mizejících zpráv (Toasty)
        if st.session_state.get("_match_saved"):
            st.toast("Zápas byl úspěšně uložen!", icon="✅")
            st.session_state["_match_saved"] = False
            
        if st.session_state.get("_elo_adjusted"):
            st.toast("ELO bylo úspěšně upraveno!", icon="✅")
            st.session_state["_elo_adjusted"] = False
            
        if st.session_state.get("_player_added"):
            st.toast("Nový hráč byl úspěšně přidán!", icon="✅")
            st.session_state["_player_added"] = False

        # 2. Skutečný a bezpečný reset formulářů
        if st.session_state.get("_clear_form"):
            st.session_state["m_type"] = "Singles"
            st.session_state["is_friendly"] = False
            st.session_state["match_date"] = datetime.now().date()
            st.session_state["s1"] = None
            st.session_state["s2"] = None
            st.session_state["d_a1"] = None
            st.session_state["d_a2"] = None
            st.session_state["d_b1"] = None
            st.session_state["d_b2"] = None
            st.session_state["winner_sel"] = "A"
            st.session_state["score_in"] = ""
            st.session_state["sets_in"] = ""
            st.session_state["_clear_form"] = False

        if st.session_state.get("_clear_adj"):
            st.session_state["adj_p"] = None
            st.session_state["adj_delta"] = 0
            st.session_state["adj_reason"] = ""
            st.session_state["_clear_adj"] = False

        if st.session_state.get("_clear_add"):
            st.session_state["new_name"] = ""
            st.session_state["new_elo"] = 1000
            st.session_state["_clear_add"] = False

        all_players = sorted(compute_elo_with_meta()[0].keys())
        
        bar("Přidat nový zápas")
        
        col1, col2 = st.columns(2)
        
        with col1:
            if "match_date" not in st.session_state:
                st.session_state["m_date_init"] = datetime.now().date()

            m_type = st.radio("Typ zápasu", ["Singles", "Doubles"], key="m_type")
            is_friendly = st.checkbox("Přátelák (nezapočítává se do ELO)", key="is_friendly")
            date = st.date_input("Datum", key="match_date")
            
            if "Singles" in m_type:
                p1 = st.selectbox("Hráč A", all_players, index=None, placeholder="— nevybráno —", key="s1")
                p2 = st.selectbox("Hráč B", all_players, index=None, placeholder="— nevybráno —", key="s2")
                team_a = p1 if p1 is not None else ""
                team_b = p2 if p2 is not None else ""
            else:
                c_a1, c_a2 = st.columns(2)
                with c_a1: p1a = st.selectbox("Tým A - Hráč 1", all_players, index=None, placeholder="— nevybráno —", key="d_a1")
                with c_a2: p1b = st.selectbox("Tým A - Hráč 2", all_players, index=None, placeholder="— nevybráno —", key="d_a2")
                
                c_b1, c_b2 = st.columns(2)
                with c_b1: p2a = st.selectbox("Tým B - Hráč 1", all_players, index=None, placeholder="— nevybráno —", key="d_b1")
                with c_b2: p2b = st.selectbox("Tým B - Hráč 2", all_players, index=None, placeholder="— nevybráno —", key="d_b2")
                team_a = f"{p1a}+{p1b}" if (p1a and p1b) else ""
                team_b = f"{p2a}+{p2b}" if (p2a and p2b) else ""
                
        with col2:
            st.write("") 
            st.write("")
            winner = st.selectbox("Vítěz", ["A", "B"], format_func=lambda x: team_a if x == "A" else team_b, key="winner_sel")
            score = st.text_input("Skóre (např. 2:1)", key="score_in")
            sets = st.text_input("Gemy setů (např. 6,4,6)", key="sets_in")
            
            if st.button("💾 Uložit zápas", use_container_width=True):
                if m_type == "Singles":
                    if (p1 is None) or (p2 is None):
                        st.error("Vyber oba hráče.")
                        st.stop()
                    if p1 == p2:
                        st.error("Hráči se nesmí opakovat!")
                        st.stop()
                else:
                    if (p1a is None) or (p1b is None) or (p2a is None) or (p2b is None):
                        st.error("Vyber všechny 4 hráče.")
                        st.stop()
                    if len(set([p1a, p1b, p2a, p2b])) != 4:
                        st.error("Hráči se nesmí opakovat!")
                        st.stop()

                db_type = "friendly_singles" if is_friendly and m_type == "Singles" else \
                          "friendly_doubles" if is_friendly and m_type == "Doubles" else \
                          "singles" if m_type == "Singles" else "doubles"

                save_match({
                    "date": date.strftime("%d.%m.%Y"),
                    "type": db_type,
                    "team_a": team_a,
                    "team_b": team_b,
                    "winner": winner,
                    "score": score,
                    "sets": f"'{sets}" if sets else "",
                    "reason": "",
                    "author": st.session_state.get("name", "Neznámý")  # PŘIDAT TENTO ŘÁDEK
                })

                st.session_state["_match_saved"] = True
                st.session_state["_clear_form"] = True
                st.rerun()

        st.divider()
        
        adj_col1, adj_col2 = st.columns(2)

        with adj_col1:
            bar("Upravit existující ELO")
            adj_player = st.selectbox("Hráč", all_players, index=None, placeholder="— nevybráno —", key="adj_p")
            adj_delta = st.number_input("Změna (např. 5 nebo -3)", step=1, key="adj_delta")
            adj_reason = st.text_input("Důvod úpravy", key="adj_reason")

            if st.button("Upravit ELO"):
                if adj_player is None:
                    st.error("Vyber hráče.")
                else:
                    save_match({
                        "date": datetime.now().strftime("%d.%m.%Y"),
                        "type": "adjust",
                        "team_a": adj_player,
                        "team_b": adj_delta,
                        "reason": adj_reason,
                        "author": st.session_state.get("name", "Neznámý")
                    })
                    st.session_state["_elo_adjusted"] = True
                    st.session_state["_clear_adj"] = True
                    st.rerun()

        with adj_col2:
            bar("Přidat nového hráče")
            new_name = st.text_input("Jméno nového hráče", key="new_name")
            new_elo = st.number_input("Startovní ELO", step=10, key="new_elo")

            if st.button("Přidat hráče"):
                if new_name and new_name not in all_players:
                    delta = new_elo - 1000
                    save_match({
                        "date": datetime.now().strftime("%d.%m.%Y"),
                        "type": "adjust",
                        "team_a": new_name,
                        "team_b": delta,
                        "reason": f"Přidání hráče({new_elo} ELO)",
                        "author": st.session_state.get("name", "Neznámý")
                    })
                    st.session_state["_player_added"] = True
                    st.session_state["_clear_add"] = True
                    st.rerun()
                elif new_name in all_players:
                    st.error("Tento hráč už existuje.")
                    
    else:
        # TOTO se zobrazí, pokud uživatel není přihlášen
        st.warning("⚠️ Pro zadávání nových zápasů, přidávání hráčů a úpravu ELO se musíš přihlásit v levém panelu.")
        st.info("Bez přihlášení je možné pouze prohlížet žebříčky a historii.")


# --- TAB 3: HISTORIE ---
with tab3:
    bar("Kompletní historie zápasů")

    # Tady se načtou data (pokud máš u build_full_history @st.cache_data, bude to hned)
    df_hist = build_full_history(DF_ALL)

    # --- 1. ADMIN SEKCE (FRAGMENT PRO RYCHLOST) ---
    if st.session_state.get("authentication_status") and st.session_state.get("name") == "Tobi":
        
        @st.fragment # <--- Tato magie zajistí, že výběr v adminu nebrzdí tabulku
        def admin_panel(df):
            with st.expander("🛠️ Admin správa zápasů (Klikni pro otevření)", expanded=False):
                st.subheader("Odstranění zápasu")
                
                if not df.empty:
                    # Vytvoříme seznam pro selectbox
                    match_options = df.apply(lambda x: f"{x['Datum']} | {x['Typ']} | {x['Zápas']}", axis=1).tolist()
                    selected = st.selectbox("Vyber zápas ke smazání:", options=match_options, index=None, key="admin_del_select")

                    # Dialog definujeme uvnitř, aby vyskočil správně
                    @st.dialog("⚠️ Potvrdit smazání")
                    def confirm_delete(row_idx, info):
                        st.warning("Opravdu smazat?")
                        st.code(info)
                        if st.button("🔥 Ano, smazat", type="primary", use_container_width=True):
                            delete_match_by_row(row_idx)
                            st.cache_data.clear() # Smaže cache, aby se změna projevila
                            st.rerun()

                    if selected:
                        if st.button("🗑️ Odstranit vybraný zápas", type="secondary", use_container_width=True):
                            idx = match_options.index(selected)
                            target_row = df.iloc[idx]["row_idx"]
                            confirm_delete(target_row, selected)
                else:
                    st.info("Historie je prázdná.")
        
        admin_panel(df_hist)
        st.write("---") 

    # --- 2. VYKRESLENÍ TABULKY HISTORIE ---
    display_df = df_hist.drop(columns=["row_idx"]) if "row_idx" in df_hist.columns else df_hist
    st.markdown("""
    <style>
      .hist-wrap{
        width: 100%;
        overflow-x: auto;
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 12px;
        background: rgba(0,0,0,0.10);
      }
      table.hist-table{
        border-collapse: collapse;
        table-layout: auto;
        width: max-content;
        min-width: 100%;
      }
      table.hist-table thead th{
        position: sticky;
        top: 0;
        background: rgba(255,255,255,0.06);
        border-bottom: 1px solid rgba(255,255,255,0.10);
        font-weight: 800;
        text-align: center;
      }
      table.hist-table th, table.hist-table td{
        padding: 10px 12px;
        border-right: 1px solid rgba(255,255,255,0.06);
        border-bottom: 1px solid rgba(255,255,255,0.06);
        white-space: nowrap;
        text-align: center;
        font-size: 12.5px;
        color: rgba(255,255,255,0.90);
      }
    </style>
    """, unsafe_allow_html=True)

    if not display_df.empty:
        html_table = display_df.to_html(index=False, classes="hist-table", border=0, escape=True)
        st.markdown(f'<div class="hist-wrap">{html_table}</div>', unsafe_allow_html=True)
    else:
        st.info("Zatím nejsou k dispozici žádné záznamy.")
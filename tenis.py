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

    load_data.clear()


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

def build_full_history(df: pd.DataFrame) -> pd.DataFrame:
    ratings = INITIAL_RATINGS.copy()

    def parse_team(s: str):
        return [x.strip() for x in str(s).split("+") if x.strip()]

    def parse_date(s: str):
        try:
            return datetime.strptime(str(s).strip(), "%d.%m.%Y").date()
        except:
            return None

    def ensure_player(p: str):
        ratings.setdefault(p, 1000.0)

    # seřadit chronologicky, aby ELO po dávalo smysl
    tmp = df.copy()
    tmp["__dt"] = tmp["date"].apply(parse_date)
    tmp = tmp.sort_values("__dt", ascending=True).drop(columns=["__dt"])

    out = []

    for _, r in tmp.iterrows():
        rtype = str(r.get("type", "")).strip()
        rawd = str(r.get("date", "")).strip()
        winner = str(r.get("winner", "")).strip()
        score = str(r.get("score", "")).strip()
        sets_raw = str(r.get("sets", "")).strip()
        reason = str(r.get("reason", "")).strip()

        # 1) Manuální úpravy
        if rtype == "adjust":
            p = str(r.get("team_a", "")).strip()
            try:
                delta = float(r.get("team_b", 0))
            except:
                delta = 0.0

            if not p:
                continue

            ensure_player(p)
            ratings[p] = ratings.get(p, 1000.0) + delta

            is_add_player = reason.startswith("Přidání hráče")
            if is_add_player:
                match_txt = f"{p} — Nastaveno na {int(round(ratings[p]))}"
                elo_diff = ""
                typ = "Přidání hráče"
            else:
                match_txt = f"{p} — Manuální úprava — {reason}".strip(" —")
                elo_diff = f"{'+' if delta >= 0 else ''}{int(delta)}"
                typ = "Úprava ELO"

            out.append({
                "Datum": rawd,
                "Typ": typ,
                "Zápas": match_txt,
                "Výsledek": "",
                "Skóre": "",
                "Sety": "",
                "Rozdíl ELO": elo_diff,
                "ELO po": round(ratings[p], 2),
            })
            continue

        # 2) Zápasy (ranked + friendly)
        if rtype in ["singles", "doubles", "friendly_singles", "friendly_doubles"]:
            team_a = parse_team(r.get("team_a", ""))
            team_b = parse_team(r.get("team_b", ""))
            if not team_a or not team_b:
                continue

            for p in team_a + team_b:
                ensure_player(p)

            is_friendly = "friendly" in rtype
            base_type = "Singles" if "singles" in rtype else "Doubles"

            ra = sum(ratings[p] for p in team_a) / max(1, len(team_a))
            rb = sum(ratings[p] for p in team_b) / max(1, len(team_b))
            ea = 1.0 / (1.0 + 10 ** ((rb - ra) / SCALE))
            sa = 1.0 if winner == "A" else 0.0

            k = 0 if is_friendly else (K_SINGLES if "singles" in rtype else K_DOUBLES)
            delta_a = k * (sa - ea)
            delta_b = -delta_a

            per_player_delta = {}
            for p in team_a:
                per_player_delta[p] = delta_a / max(1, len(team_a))
            for p in team_b:
                per_player_delta[p] = delta_b / max(1, len(team_b))

            # upd ELO
            for p, d in per_player_delta.items():
                ratings[p] = ratings.get(p, 1000.0) + d

            match_txt_base = f"{' + '.join(team_a)} 🆚 {' + '.join(team_b)}"

            # výstupní řádky pro všechny hráče v zápase
            for p in team_a + team_b:
                d_pl = per_player_delta.get(p, 0.0)

                res = "Výhra" if (
                    (winner == "A" and p in team_a) or (winner == "B" and p in team_b)
                ) else "Prohra"

                out.append({
                    "Datum": rawd,
                    "Typ": "Přátelák" if is_friendly else base_type,
                    "Zápas": f"{p} — {match_txt_base}",
                    "Výsledek": res,
                    "Skóre": score,
                    "Sety": sets_raw,
                    "Rozdíl ELO": "" if is_friendly else f"{'+' if round(d_pl) >= 0 else ''}{int(round(d_pl))}",
                    "ELO po": round(ratings[p], 2),
                })

    if not out:
        return pd.DataFrame(columns=["Datum", "Typ", "Zápas", "Výsledek", "Skóre", "Sety", "Rozdíl ELO", "ELO po"])

    # zobrazit od nejnovějšího
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
# --- UI STREAMLIT ---
st.set_page_config(page_title="Tennis ELO Žebříček", page_icon="🎾", layout="wide")
st.title("🎾 Tennis ELO — Zápisy a Žebříček")
def bar(text: str):
    st.markdown(f'<div class="section-bar">{text}</div>', unsafe_allow_html=True)

# Záložky pro přepínání obsahu
tab1, tab_sd, tab2, tab3 = st.tabs(["🏆 Žebříček", "🎾 Singles & Doubles", "✍️ Zadat zápas nebo přidat hráče", "📜 Kompletní historie"])

# načti sheet JEDNOU pro celý run (tabs se i tak vykonají všechny)
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

            # barvení Δ sloupce
            if c == DELTA_COL:
                s = str(v).strip()
                style = ""
                try:
                    main = s.split("(", 1)[0].strip()
                    n = float(main.replace("+", "").replace("−", "-"))
                    if n > 0:
                        style = "color: #2ecc71; font-weight: 700;"
                    elif n < 0:
                        style = "color: #e74c3c; font-weight: 700;"
                except:
                    style = ""

                # unranked = šedé pozadí
                row_style = 'color: rgba(255,255,255,0.55); background-color: rgba(255,255,255,0.03);' if is_unranked else ""
                parts.append(f'<td style="{row_style}{style}">{_esc(v)}</td>')
                continue

            # unranked = šedé pozadí (všechny sloupce)
            if is_unranked:
                parts.append(f'<td style="color: rgba(255,255,255,0.55); background-color: rgba(255,255,255,0.03);">{_esc(v)}</td>')
            else:
                parts.append(f"<td>{_esc(v)}</td>")

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
            index=all_players.index(st.session_state.get("selected_player")) if st.session_state.get("selected_player") in all_players else 0,
        )

    st.session_state["selected_player"] = picked
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
                .applymap(_res_color, subset=["Výsledek"])
        )

        html_hist = hist_styler.to_html()
        st.markdown(f'<div class="hist-wrap">{html_hist}</div>', unsafe_allow_html=True)

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
# --- TAB 2: ZADÁNÍ ZÁPASU ---
with tab2:
    all_players = sorted(compute_elo_with_meta()[0].keys())
    col1, col2 = st.columns(2)
    
    with col1:
        bar("Nový zápas")
        m_type = st.radio("Typ zápasu", ["Singles", "Doubles"])
        is_friendly = st.checkbox("Přátelák (nezapočítává se do ELO)")
        date = st.date_input("Datum", datetime.now())
        
        # Výběr hráčů podle typu
        if "Singles" in m_type:
            p1 = st.selectbox("Hráč A", all_players, index=None, placeholder="— nevybráno —", key="s1")
            p2 = st.selectbox("Hráč B", all_players, index=None, placeholder="— nevybráno —", key="s2")
            team_a = p1 if p1 is not None else ""
            team_b = p2 if p2 is not None else ""
        else:
            c_a1, c_a2 = st.columns(2)
            with c_a1: p1a = st.selectbox("Tým A - Hráč 1", players_pick, index=0, key="d_a1")
            with c_a2: p1b = st.selectbox("Tým A - Hráč 2", players_pick, index=0, key="d_a2")
            
            c_b1, c_b2 = st.columns(2)
            with c_b1: p2a = st.selectbox("Tým B - Hráč 1", players_pick, index=0, key="d_b1")
            with c_b2: p2b = st.selectbox("Tým B - Hráč 2", players_pick, index=0, key="d_b2")
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
    bar("Upravit existující ELO")
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
        bar("Přidat nového hráče")
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
    bar("Kompletní historie zápasů")

    df_hist = build_full_history(DF_ALL)

# pokud nechceš Sety ve kompletní historii, smaž tenhle řádek:
# df_hist = df_hist.drop(columns=["Sety"])

    st.markdown("""
    <style>
      /* TAB 3 = vlastní HTML tabulka, šířky podle obsahu, žádný filler sloupec */
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
        width: max-content;      /* šířka podle nejdelších hodnot */
        min-width: 100%;         /* když je krátká, vyplní wrapper */
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
        white-space: nowrap;     /* neláme text, šířky podle obsahu */
        text-align: center;
        font-size: 12.5px;
        color: rgba(255,255,255,0.90);
      }
      table.hist-table th:last-child, table.hist-table td:last-child{
        border-right: none;
      }
      table.hist-table tr:last-child td{
        border-bottom: none;
      }
    </style>
    """, unsafe_allow_html=True)

    html_table = df_hist.to_html(index=False, classes="hist-table", border=0, escape=True)

    st.markdown(f'<div class="hist-wrap">{html_table}</div>', unsafe_allow_html=True)
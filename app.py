import io
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import urllib.request
import streamlit as st
import pandas as pd
from jobspy import scrape_jobs
from duckduckgo_search import DDGS

st.set_page_config(
    page_title="Vacature Scanner",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# 1. Functietitels waarmee gezocht wordt
DEFAULT_TITELS = (
    "Brand Manager, Brand Management, Product Marketing Manager, "
    "Trade Marketeer, Brand Activation Manager, Category Manager, Marketing Manager"
)

# 2. Verplichte kernwoorden in de gevonden titel (houdt Sales/Verkoop/etc. buiten de deur)
DEFAULT_KERNWOORDEN_TITEL = (
    "Brand, Merk, Marketing, Marketeer, Category, Categorie, Trade, Product, Activation, Campagne, Campaign"
)

# 3. Uitgebreide sectortrefwoorden + grote werkgevers in deze 5 sectoren (regio Amsterdam)
DEFAULT_SECTOREN = (
    "Horeca, Hospitality, Hotel, Hotels, Restaurant, Restaurants, Cafe, Catering, Bar, Foodservice, "
    "Travel, Tourism, Toerisme, Reizen, Reis, Airline, Luchtvaart, Aviation, Vakantie, Leisure, Mobility, "
    "Retail, Winkel, Winkels, E-commerce, Ecommerce, Webshop, Omnichannel, Marketplace, "
    "FMCG, Consumer Goods, Consumentengoederen, Food, Voeding, Beverage, Drinks, Dranken, Bier, Koffie, "
    "Fashion, Mode, Apparel, Kleding, Schoenen, Footwear, Sportswear, Denim, Luxury, Cosmetics, Cosmetica, Beauty, Lifestyle, "
    "Warenhuis, Department Store, DIY, Tuincentrum, Elektronica, "
    "Publisher, Publishing, Uitgever, Uitgeverij, Media, Tijdschrift, Magazine, Krant, Boeken, Nieuws, Broadcasting, Entertainment, "
    "Heineken, Booking, KLM, Transavia, SkyTeam, Corendon, TUI, Accor, Marriott, Hilton, CitizenM, Soho House, "
    "Rituals, Bijenkorf, HEMA, Etos, Gall, Ahold, Nike, Adidas, PVH, Tommy Hilfiger, Calvin Klein, Karl Lagerfeld, "
    "Suitsupply, G-Star, Scotch, Bestseller, Inditex, Zara, H&M, Action, IKEA, Dyson, Philips, L'Oreal, Unilever, "
    "Nestle, Danone, Kraft Heinz, Mondelez, PepsiCo, Coca-Cola, Red Bull, Jacobs Douwe Egberts, JDE, Tony's, "
    "DPG, Mediahuis, Hearst, FD Mediagroep, RTL, Talpa, Fremantle, Elsevier, Wolters Kluwer, Veen Media, ANWB"
)

# 4. Uitsluitingen in de functietitel
DEFAULT_UITSLUITINGEN = (
    "Stage, Intern, Internship, Stagiair, Trainee, Junior, Store Manager, Shop Manager, Assistant Store, "
    "Brand Ambassador, Ambassadeur, Winkelmedewerker, Verkoopspecialist, Verkoper, Sales, Accountmanager, "
    "Account Manager, Business Development, Recruiter, Recruitment, HR, Klantenservice, Customer Service, "
    "Monteur, Chauffeur, Beveiliger, Oncology, Pharma, Medical, Clinical, Finance, Controller, Accountant, "
    "Software, Developer, Engineer, Data Scientist, IT"
)

DEFAULT_EXTRA_SITES = "nationalevacaturebank.nl, hotelprofessionals.nl, werkenbij.nl, fashionunited.nl, marketingtribune.nl"


def parse_list(csv_string):
    if not csv_string:
        return []
    return [item.strip() for item in csv_string.split(",") if item.strip()]


def detect_language(text):
    if not text:
        return "Onbekend"
    text_lower = f" {text.lower()} "
    nl_words = [" de ", " het ", " een ", " wij ", " voor ", " met ", " zijn ", " jouw ", " functie "]
    en_words = [" the ", " and ", " you ", " our ", " with ", " for ", " role ", " experience ", " team "]
    nl_score = sum(1 for w in nl_words if w in text_lower)
    en_score = sum(1 for w in en_words if w in text_lower)
    if nl_score > en_score:
        return "NL"
    elif en_score > nl_score:
        return "EN"
    return "Onbekend"


def fetch_single_linkedin_desc(url):
    """Haalt snel de tekst op van één relevante LinkedIn-vacature als de samenvatting leeg is."""
    if not url or "linkedin.com" not in url:
        return ""
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        )
        with urllib.request.urlopen(req, timeout=4) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
        # Zoek de beschrijvingstekst in de HTML
        match = re.search(r'<div class="[^"]*show-more-less-html__markup[^"]*"[^>]*>(.*?)</div>', html, re.DOTALL)
        if match:
            clean = re.sub(r"<[^>]+>", " ", match.group(1))
            return re.sub(r"\s+", " ", clean).strip()
    except Exception:
        pass
    return ""


def enrich_missing_descriptions(df_candidates):
    """Vult ontbrekende beschrijvingen aan voor maximaal 35 kandidaten tegelijk (duurt ~3 seconden)."""
    if df_candidates.empty:
        return df_candidates

    urls_to_fetch = {}
    for idx, row in df_candidates.iterrows():
        desc = str(row.get("description", "") or "").strip()
        url = str(row.get("job_url", "") or "").strip()
        if not desc and "linkedin.com" in url:
            urls_to_fetch[idx] = url

    if not urls_to_fetch:
        return df_candidates

    with ThreadPoolExecutor(max_workers=8) as executor:
        future_to_idx = {executor.submit(fetch_single_linkedin_desc, url): idx for idx, url in list(urls_to_fetch.items())[:35]}
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                text = future.result()
                if text:
                    df_candidates.at[idx, "description"] = text
            except Exception:
                pass

    return df_candidates


def scrape_single_title(titel, vaste_bronnen, locatie, distance_miles, hours_old):
    """Voert een zoekopdracht uit voor één functietitel."""
    try:
        jobs = scrape_jobs(
            site_name=vaste_bronnen,
            search_term=titel,  # Geen aanhalingstekens meer zodat variaties ook gevonden worden
            location=locatie,
            distance=distance_miles,
            hours_old=hours_old,
            results_wanted=40,
            country_indeed="Netherlands",
            linkedin_fetch_description=False
        )
        return jobs if jobs is not None else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


def scrape_extra_sites(extra_sites, functietitels, locatie, dagen_terug):
    if not extra_sites or not functietitels:
        return pd.DataFrame()

    if dagen_terug <= 1:
        timelimit = "d"
    elif dagen_terug <= 7:
        timelimit = "w"
    else:
        timelimit = "m"

    stad = locatie.split(",")[0].strip()
    results = []
    titels_query = " OR ".join([f'"{t}"' for t in functietitels[:4]])

    with DDGS() as ddgs:
        for site in extra_sites:
            clean_site = site.replace("https://", "").replace("http://", "").split("/")[0].strip()
            if not clean_site:
                continue
            query = f"site:{clean_site} ({titels_query}) {stad}"
            try:
                hits = ddgs.text(query, region="nl-nl", timelimit=timelimit, max_results=20)
                for h in hits:
                    results.append({
                        "title": h.get("title", ""),
                        "company": clean_site,
                        "location": stad,
                        "description": h.get("body", ""),
                        "site": clean_site,
                        "date_posted": f"Afgelopen {dagen_terug} dgn",
                        "job_type": "",
                        "job_url": h.get("href", "")
                    })
            except Exception:
                continue

    return pd.DataFrame(results)


def filter_jobs(df_raw, functietitels, kernwoorden_titel, sectoren, uitsluitingen, taal_voorkeur, strengheid):
    if df_raw is None or df_raw.empty:
        return pd.DataFrame()

    uitsluitingen_lower = [w.lower() for w in uitsluitingen]
    functietitels_lower = [t.lower() for t in functietitels]
    kernwoorden_lower = [k.lower() for k in kernwoorden_titel]

    seen_urls = set()
    seen_keys = set()
    pre_filtered_rows = []

    # STAP 1: Filter eerst op Functietitel en Uitsluitingen (en ontdubbel)
    for _, row in df_raw.iterrows():
        url = str(row.get("job_url", "")).strip()
        title = str(row.get("title", "")).strip()
        company = str(row.get("company", "")).strip()

        if not title:
            continue

        dedup_key = f"{company}_{title}".lower()
        if (url and url in seen_urls) or dedup_key in seen_keys:
            continue

        title_lower = title.lower()

        # Uitsluitingen controleren
        if any(excl in title_lower for excl in uitsluitingen_lower):
            continue

        # Verplichte kernwoorden in titel controleren
        if kernwoorden_lower:
            heeft_kernwoord = any(k in title_lower for k in kernwoorden_lower)
            heeft_exacte_titel = any(t in title_lower for t in functietitels_lower)
            if not (heeft_kernwoord or heeft_exacte_titel):
                continue

        if url:
            seen_urls.add(url)
        seen_keys.add(dedup_key)
        pre_filtered_rows.append(row)

    if not pre_filtered_rows:
        return pd.DataFrame()

    df_candidates = pd.DataFrame(pre_filtered_rows).reset_index(drop=True)

    # STAP 2: Haal voor de overgebleven LinkedIn-kandidaten razendsnel de vacaturetekst op
    df_candidates = enrich_missing_descriptions(df_candidates)

    # STAP 3: Filter op Taal en Sector
    accepted = []
    for _, row in df_candidates.iterrows():
        url = str(row.get("job_url", "")).strip()
        title = str(row.get("title", "")).strip()
        company = str(row.get("company", "")).strip()
        location = str(row.get("location", "")).strip()
        description = str(row.get("description", "") or "").strip()
        site = str(row.get("site", "")).strip()
        date_posted = str(row.get("date_posted", "")).strip()
        job_type = str(row.get("job_type", "") or "").strip()

        lang = detect_language(f"{title} {description}")
        if taal_voorkeur == "Alleen Nederlands" and lang == "EN":
            continue
        if taal_voorkeur == "Alleen Engels" and lang == "NL":
            continue

        combined_text = f"{title} {company} {description}"
        matched_sectors = []
        for s in sectoren:
            pattern = r"\b" + re.escape(s) + r"\b"
            if re.search(pattern, combined_text, flags=re.IGNORECASE):
                matched_sectors.append(s)

        sector_label = ", ".join(dict.fromkeys(matched_sectors)) if matched_sectors else "Geen directe match"

        if strengheid.startswith("Streng") and not matched_sectors:
            continue

        clean_desc = re.sub(r"\s+", " ", description)
        short_desc = clean_desc[:260] + "..." if len(clean_desc) > 260 else clean_desc

        accepted.append({
            "Geplaatst": date_posted,
            "Functietitel": title,
            "Bedrijf": company,
            "Locatie": location,
            "Sector Match": sector_label,
            "Taal": lang,
            "Bron": site,
            "Type": job_type,
            "Url": url,
            "Samenvatting": short_desc
        })

    return pd.DataFrame(accepted)


# --- HOOFDSCHERM ---
st.subheader("Vacature Scanner: Brand Management")

col_top1, col_top2 = st.columns([2, 1])
with col_top1:
    dagen_terug = st.slider(
        "Aantal dagen terug zoeken",
        min_value=1,
        max_value=30,
        value=7
    )
with col_top2:
    straal_km = st.number_input("Straal rondom locatie (km)", min_value=5, max_value=100, value=20)

with st.expander("Zoekfilters, Zoektermen & Websites aanpassen"):
    col1, col2 = st.columns(2)
    with col1:
        locatie = st.text_input("Locatie", value="Amsterdam, Netherlands")
        vaste_bronnen = st.multiselect(
            "Vaste vacaturesites",
            options=["linkedin", "indeed", "glassdoor", "google"],
            default=["linkedin", "indeed", "google"]
        )
        extra_sites_input = st.text_area(
            "Andere websites / bedrijfssites (komma-gescheiden)",
            value=DEFAULT_EXTRA_SITES,
            height=75
        )
        taal_voorkeur = st.selectbox(
            "Taal van de vacature",
            options=["Beide (NL & EN)", "Alleen Nederlands", "Alleen Engels"]
        )

    with col2:
        functietitels_input = st.text_area(
            "Functietitels (komma-gescheiden)",
            value=DEFAULT_TITELS,
            height=75
        )
        kernwoorden_titel_input = st.text_input(
            "Verplichte kernwoorden in functietitel (minimaal 1 vereist)",
            value=DEFAULT_KERNWOORDEN_TITEL
        )
        sectoren_input = st.text_area(
            "Sectortrefwoorden & Bedrijfsnamen (komma-gescheiden)",
            value=DEFAULT_SECTOREN,
            height=110
        )
        sector_strengheid = st.selectbox(
            "Sector-filter modus",
            options=[
                "Soepel (toon alles met juiste functietitel en label de sector)",
                "Streng (toon alleen bij directe match met sectortrefwoord)"
            ]
        )
        uitsluitingen_input = st.text_area(
            "Uitsluitingen in titel (komma-gescheiden)",
            value=DEFAULT_UITSLUITINGEN,
            height=85
        )

if "resultaten_df" not in st.session_state:
    st.session_state["resultaten_df"] = pd.DataFrame()

if st.button("Start Live Vacaturescan", type="primary", use_container_width=True):
    functietitels = parse_list(functietitels_input)
    kernwoorden_titel = parse_list(kernwoorden_titel_input)
    sectoren = parse_list(sectoren_input)
    uitsluitingen = parse_list(uitsluitingen_input)
    extra_sites = parse_list(extra_sites_input)
    hours_old = dagen_terug * 24
    distance_miles = max(5, round(straal_km * 0.621371))

    all_dfs = []

    with st.spinner(f"Vacatures van de afgelopen {dagen_terug} dagen worden parallel opgehaald en verrijkt..."):
        # Parallel ophalen van alle functietitels tegelijk (veel sneller)
        if vaste_bronnen and functietitels:
            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = [
                    executor.submit(scrape_single_title, titel, vaste_bronnen, locatie, distance_miles, hours_old)
                    for titel in functietitels
                ]
                for future in as_completed(futures):
                    res_df = future.result()
                    if not res_df.empty:
                        all_dfs.append(res_df)

        if extra_sites and functietitels:
            df_extra = scrape_extra_sites(extra_sites, functietitels, locatie, dagen_terug)
            if not df_extra.empty:
                all_dfs.append(df_extra)

        if all_dfs:
            df_combined = pd.concat(all_dfs, ignore_index=True)
            st.session_state["resultaten_df"] = filter_jobs(
                df_combined, functietitels, kernwoorden_titel, sectoren, uitsluitingen, taal_voorkeur, sector_strengheid
            )
        else:
            st.session_state["resultaten_df"] = pd.DataFrame()

# --- RESULTATEN WEERGEVEN & EXPORTEREN ---
df_res = st.session_state["resultaten_df"]

if not df_res.empty:
    st.divider()

    zoekwoord = st.text_input("Zoek in gevonden resultaten (op bedrijf, titel of trefwoord)", "")
    col_f1, col_f2 = st.columns(2)
    with col_f1:
        gevonden_bronnen = sorted(df_res["Bron"].unique().tolist())
        filter_bron = st.multiselect("Filter op bron", options=gevonden_bronnen, default=gevonden_bronnen)
    with col_f2:
        alleen_sector = st.checkbox("Alleen met directe sectormatch", value=False)

    mask = df_res["Bron"].isin(filter_bron)
    if alleen_sector:
        mask &= df_res["Sector Match"] != "Geen directe match"
    if zoekwoord:
        q = zoekwoord.lower()
        mask &= (
            df_res["Functietitel"].str.lower().str.contains(q, na=False) |
            df_res["Bedrijf"].str.lower().str.contains(q, na=False) |
            df_res["Samenvatting"].str.lower().str.contains(q, na=False)
        )

    df_view = df_

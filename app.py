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

# 2. Verplichte kernwoorden in de gevonden titel
DEFAULT_KERNWOORDEN_TITEL = (
    "Brand, Merk, Marketing, Marketeer, Category, Categorie, Trade, Product, Activation, Campagne, Campaign"
)

# 3. Sectortrefwoorden & Bedrijfsnamen (zonder Tuincentrum / DIY / Supermarkt)
DEFAULT_SECTOREN = (
    "Horeca, Hospitality, Hotel, Hotels, Restaurant, Restaurants, Cafe, Catering, Bar, Foodservice, "
    "Travel, Tourism, Toerisme, Reizen, Reis, Airline, Luchtvaart, Aviation, Vakantie, Leisure, Mobility, "
    "Retail, Winkel, Winkels, E-commerce, Ecommerce, Webshop, Omnichannel, Marketplace, "
    "FMCG, Consumer Goods, Consumentengoederen, Food, Voeding, Beverage, Drinks, Dranken, Bier, Koffie, "
    "Fashion, Mode, Apparel, Kleding, Schoenen, Footwear, Sportswear, Denim, Luxury, Cosmetics, Cosmetica, Beauty, Lifestyle, "
    "Warenhuis, Department Store, Elektronica, "
    "Publisher, Publishing, Uitgever, Uitgeverij, Media, Tijdschrift, Magazine, Krant, Boeken, Nieuws, Broadcasting, Entertainment, "
    "Heineken, Booking, KLM, Transavia, SkyTeam, Corendon, TUI, Accor, Marriott, Hilton, CitizenM, Soho House, "
    "Rituals, Bijenkorf, HEMA, Etos, Gall, Nike, Adidas, PVH, Tommy Hilfiger, Calvin Klein, Karl Lagerfeld, "
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
    "Software, Developer, Engineer, Data Scientist, IT, Tuincentrum, Supermarkt"
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
    if not url or "linkedin.com" not in url:
        return ""
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        )
        with urllib.request.urlopen(req, timeout=4) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
        match = re.search(r'<div class="[^"]*show-more-less-html__markup[^"]*"[^>]*>(.*?)</div>', html, re.DOTALL)
        if match:
            clean = re.sub(r"<[^>]+>", " ", match.group(1))
            return re.sub(r"\s+", " ", clean).strip()
    except Exception:
        pass
    return ""


def enrich_missing_descriptions(df_candidates):
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
        future_to_idx = {
            executor.submit(fetch_single_linkedin_desc, url): idx
            for idx, url in list(urls_to_fetch.items())[:35]
        }
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
    try:
        jobs = scrape_jobs(
            site_name=vaste_bronnen,
            search_term=titel,
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

        if any(excl in title_lower for excl in uitsluitingen_lower):
            continue

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
    df_candidates = enrich_missing_descriptions(df_candidates)

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
            "Ta

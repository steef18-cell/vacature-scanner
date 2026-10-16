import io
import re
from datetime import datetime
import streamlit as st
import pandas as pd
from jobspy import scrape_jobs
from duckduckgo_search import DDGS

st.set_page_config(
    page_title="Vacature Scanner",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# Standaardinstellingen ('Supermarkt' verwijderd, Sales/Verkoop toegevoegd aan uitsluitingen)
DEFAULT_TITELS = "Brand Manager, Brand Management, Product Marketing Manager, Trade Marketeer, Brand Activation Manager, Category Manager"
DEFAULT_SECTOREN = "Horeca, Hospitality, Travel, Tourism, Hotel, Restaurant, Retail, FMCG, E-commerce, Fashion, Publisher, Uitgeverij, Media"
DEFAULT_UITSLUITINGEN = "Stage, Intern, Internship, Stagiair, Trainee, Junior, Store Manager, Shop Manager, Brand Ambassador, Ambassadeur, Winkelmedewerker, Verkoopspecialist, Verkoper, Sales, Accountmanager, Account Manager, Recruiter, Klantenservice, Customer Service, Monteur, Chauffeur, Beveiliger, Oncology, Pharma"
DEFAULT_KERNWOORDEN_TITEL = "Brand, Marketing, Marketeer, Category, Trade, Product, Activation"
DEFAULT_EXTRA_SITES = "nationalevacaturebank.nl, hotelprofessionals.nl, werkenbij.nl"


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

    # Combineer functietitels in één zoekopdracht per site voor snelheid
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
    accepted = []

    for _, row in df_raw.iterrows():
        url = str(row.get("job_url", "")).strip()
        title = str(row.get("title", "")).strip()
        company = str(row.get("company", "")).strip()
        location = str(row.get("location", "")).strip()
        description = str(row.get("description", "") or "").strip()
        site = str(row.get("site", "")).strip()
        date_posted = str(row.get("date_posted", "")).strip()
        job_type = str(row.get("job_type", "") or "").strip()

        if not title:
            continue

        dedup_key = f"{company}_{title}".lower()
        if (url and url in seen_urls) or dedup_key in seen_keys:
            continue

        title_lower = title.lower()

        # 1. Uitsluitingen in de titel
        if any(excl in title_lower for excl in uitsluitingen_lower):
            continue

        # 2. Strenge controle op Functietitel (voorkomt irrelevante Sales/Verkoop rollen)
        if kernwoorden_lower:
            heeft_kernwoord = any(k in title_lower for k in kernwoorden_lower)
            heeft_exacte_titel = any(t in title_lower for t in functietitels_lower)
            if not (heeft_kernwoord or heeft_exacte_titel):
                continue

        # 3. Taalfilter
        lang = detect_language(f"{title} {description}")
        if taal_voorkeur == "Alleen Nederlands" and lang == "EN":
            continue
        if taal_voorkeur == "Alleen Engels" and lang == "NL":
            continue

        # 4. Sector-matching op hele woorden (woordgrenzen)
        combined_text = f"{title} {company} {description}"
        matched_sectors = []
        for s in sectoren:
            pattern = r"\b" + re.escape(s) + r"\b"
            if re.search(pattern, combined_text, flags=re.IGNORECASE):
                matched_sectors.append(s)

        sector_label = ", ".join(matched_sectors) if matched_sectors else "Geen directe match"

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

        if url:
            seen_urls.add(url)
        seen_keys.add(dedup_key)

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
    straal_km = st.number_input("Straal rondom locatie (km)", min_value=5, max_value=100, value=15)

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
            height=70
        )
        taal_voorkeur = st.selectbox(
            "Taal van de vacature",
            options=["Beide (NL & EN)", "Alleen Nederlands", "Alleen Engels"]
        )
        linkedin_diep = st.checkbox(
            "Volledige LinkedIn-vacatureteksten ophalen (Let op: maakt scan 3 tot 4 minuten trager)",
            value=False
        )

    with col2:
        functietitels_input = st.text_area(
            "Functietitels (komma-gescheiden)",
            value=DEFAULT_TITELS,
            height=75
        )
        kernwoorden_titel_input = st.text_input(
            "Verplichte kernwoorden in functietitel (minimaal 1 vereist)",
            value=DEFAULT_KERNWOORDEN_TITEL,
            help="Voorkomt dat irrelevante vacatures (zoals Verkoopspecialist of Sales) doorkomen."
        )
        sectoren_input = st.text_area(
            "Sectortrefwoorden (komma-gescheiden)",
            value=DEFAULT_SECTOREN,
            height=75
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
    progress_text = st.empty()
    progress_bar = st.progress(0)

    total_steps = (len(functietitels) if vaste_bronnen else 0) + (1 if extra_sites else 0)
    current_step = 0

    if vaste_bronnen and functietitels:
        for titel in functietitels:
            current_step += 1
            progress_text.text(f"Bezig met zoeken naar '{titel}' ({current_step}/{total_steps})...")
            progress_bar.progress(current_step / max(1, total_steps))
            try:
                jobs = scrape_jobs(
                    site_name=vaste_bronnen,
                    search_term=f'"{titel}"',
                    location=locatie,
                    distance=distance_miles,
                    hours_old=hours_old,
                    results_wanted=20,
                    country_indeed="Netherlands",
                    linkedin_fetch_description=linkedin_diep
                )
                if jobs is not None and not jobs.empty:
                    all_dfs.append(jobs)
            except Exception as e:
                st.warning(f"Fout bij '{titel}' op vaste bronnen: {e}")

    if extra_sites and functietitels:
        current_step += 1
        progress_text.text("Bezig met doorzoeken van extra websites...")
        progress_bar.progress(1.0)
        df_extra = scrape_extra_sites(extra_sites, functietitels, locatie, dagen_terug)
        if not df_extra.empty:
            all_dfs.append(df_extra)

    progress_text.empty()
    progress_bar.empty()

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

    df_view = df_res[mask].copy()
    st.markdown(f"**Gevonden vacatures:** {len(df_view)} (van {len(df_res)} totaal)")

    datum_str = datetime.now().strftime("%Y-%m-%d")
    col_dl1, col_dl2 = st.columns(2)

    with col_dl1:
        excel_buffer = io.BytesIO()
        with pd.ExcelWriter(excel_buffer, engine="openpyxl") as writer:
            df_view.to_excel(writer, index=False, sheet_name="Vacatures")
        st.download_button(
            label="Download Excel (.xlsx)",
            data=excel_buffer.getvalue(),
            file_name=f"vacatures_{datum_str}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True
        )

    with col_dl2:
        csv_data = df_view.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            label="Download CSV (.csv)",
            data=csv_data,
            file_name=f"vacatures_{datum_str}.csv",
            mime="text/csv",
            use_container_width=True
        )

    weergave = st.radio("Weergave:", ["Mobiele kaarten", "Tabel"], horizontal=True)

    if weergave == "Mobiele kaarten":
        for _, row in df_view.iterrows():
            with st.container(border=True):
                st.markdown(f"**{row['Functietitel']}** — *{row['Bedrijf']}*")
                st.caption(f"📍 {row['Locatie']} | 🏷️ {row['Sector Match']} | 🌐 {row['Bron']} | 📅 {row['Geplaatst']}")
                if row["Samenvatting"]:
                    st.write(row["Samenvatting"])
                if row["Url"]:
                    st.link_button("Open vacature", row["Url"], use_container_width=True)
    else:
        st.dataframe(
            df_view,
            column_config={
                "Url": st.column_config.LinkColumn("Link", display_text="Open vacature"),
                "Samenvatting": st.column_config.TextColumn("Samenvatting", width="large")
            },
            hide_index=True,
            use_container_width=True
        )
else:
    st.info("Pas indien gewenst de filters aan en tik op 'Start Live Vacaturescan'.")

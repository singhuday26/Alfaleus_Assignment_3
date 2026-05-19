"""
app.py — Alfaleus Streamlit Dashboard [Agent 3]
"""
import streamlit as st
import pandas as pd
import os
from pymongo import MongoClient
from datetime import datetime, timezone

# Configuration
@st.cache_resource
def get_database():
    try:
        uri = st.secrets.get("MONGO_URI")
    except Exception:
        uri = None
    uri = uri or os.getenv("MONGO_URI") or "mongodb://localhost:27017/alfaleus"
    client = MongoClient(uri)
    return client.get_database("alfaleus")

st.set_page_config(
    page_title="Alfaleus | Startup Opportunity Aggregator",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom CSS for "Wow" Factor
st.markdown("""
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
  html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
  .main { background: linear-gradient(135deg, #0a0e1a 0%, #0d1528 50%, #0a1020 100%); }
  /* KPI Cards */
  .kpi-card {
    background: linear-gradient(135deg, rgba(255,255,255,0.05) 0%, rgba(255,255,255,0.02) 100%);
    border: 1px solid rgba(99, 179, 237, 0.2);
    border-radius: 16px;
    padding: 24px 20px;
    text-align: center;
    backdrop-filter: blur(10px);
    transition: transform 0.3s ease, border-color 0.3s ease;
  }
  .kpi-card:hover { border-color: rgba(99, 179, 237, 0.5); transform: translateY(-2px); }
  .kpi-value { font-size: 2.4rem; font-weight: 700; color: #63b3ed; line-height: 1.1; }
  .kpi-label { font-size: 0.78rem; color: #a0aec0; text-transform: uppercase; letter-spacing: 0.08em; margin-top: 6px; }
  .section-header {
    font-size: 1.1rem; font-weight: 600; color: #e2e8f0;
    border-left: 3px solid #63b3ed; padding-left: 12px;
    margin: 24px 0 16px 0;
  }
</style>
""", unsafe_allow_html=True)

# ── Data Ingestion & Caching ──────────────────────────────────────────────────

@st.cache_data(ttl=300, show_spinner=False)
def fetch_opportunities() -> pd.DataFrame:
    try:
        db = get_database()
        page = 0
        page_size = 2000
        cursor = db.opportunities.find().sort("scraped_at", -1).skip(page * page_size).limit(page_size)
        all_data = []
        for d in cursor:
            d["_id"] = str(d["_id"])
            all_data.append(d)
    except Exception as e:
        st.error(f"Error fetching opportunities: {e}")
        all_data = []
            
    if not all_data:
        return pd.DataFrame()
        
    rows = []
    for d in all_data:
        ai = d.get("ai_tags") or {}
        rows.append({
            "Title": d.get("title", ""),
            "Description": d.get("description", ""),
            "URL": d.get("source_url", ""),
            "Source": d.get("source", ""),
            "Organization": d.get("organization", ""),
            "Startup Stage": ai.get("startup_stage", "Unknown"),
            "Work Model": "Remote" if ai.get("is_remote") else ("On-site" if ai.get("is_remote") is False else "Unknown"),
            "Deadline": d.get("deadline", ""),
            "Created At": d.get("created_at", "")
        })
    return pd.DataFrame(rows)

@st.cache_data(ttl=300, show_spinner=False)
def fetch_scraper_runs() -> list:
    try:
        db = get_database()
        cursor = db.scraper_runs.find().sort("start_time", -1).limit(5)
        runs = []
        for r in cursor:
            r["_id"] = str(r["_id"])
            r["id"] = r["_id"]  # Match expected UI key
            if "started_at" not in r and "start_time" in r:
                r["started_at"] = r["start_time"]
            runs.append(r)
        return runs
    except Exception as e:
        return []

@st.cache_data(ttl=300, show_spinner=False)
def fetch_stats() -> dict:
    try:
        db = get_database()
        total = db.opportunities.count_documents({})
        active_sources = len(db.opportunities.distinct("source"))
        ai_enriched = db.opportunities.count_documents({"ai_tags": {"$ne": None}})
        
        ai_pct = round((ai_enriched / total * 100)) if total > 0 else 0
        
        return {
            "total_opportunities": total,
            "active_sources": active_sources,
            "ai_tagged_count": ai_enriched,
            "ai_tagged_pct": ai_pct
        }
    except Exception as e:
        return {}

# ── Main UI ───────────────────────────────────────────────────────────────────

def main():
    st.markdown(
        """
        <div style="text-align:center;padding:32px 0 24px">
          <h1 style="font-size:2.8rem;font-weight:700;background:linear-gradient(90deg,#63b3ed,#9f7aea);
                     -webkit-background-clip:text;-webkit-text-fill-color:transparent;margin:0">
            🚀 Alfaleus Intelligence
          </h1>
          <p style="color:#718096;font-size:1.1rem;margin-top:8px">
            Startup Opportunity Pipeline · AI Enrichment Layer
          </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.spinner("Fetching system data..."):
        df = fetch_opportunities()
        runs = fetch_scraper_runs()
        stats = fetch_stats()

    # ── Sidebar Filters ───────────────────────────────────────────────────────
    with st.sidebar:
        st.markdown("### 🔍 Filters")
        
        search_query = st.text_input("🔎 Keyword Search", placeholder="Search titles & descriptions...")
        
        if not df.empty:
            sources = sorted([s for s in df["Source"].unique() if s])
            stages = sorted([s for s in df["Startup Stage"].unique() if s])
            work_models = sorted([s for s in df["Work Model"].unique() if s])
        else:
            sources, stages, work_models = [], [], []

        selected_sources = st.multiselect("Source", options=sources, default=[])
        selected_stages = st.multiselect("Startup Stage (AI Tag)", options=stages, default=[])
        selected_work_models = st.multiselect("Work Model (AI Tag)", options=work_models, default=[])
        
        deadline_filter = st.date_input("Deadline after", value=None)

        if st.button("🔄 Refresh Data", use_container_width=True):
            fetch_opportunities.clear()
            fetch_scraper_runs.clear()
            fetch_stats.clear()
            st.rerun()

    # Apply Filters
    filtered_df = df.copy()
    if not filtered_df.empty:
        if search_query:
            filtered_df = filtered_df[
                filtered_df["Title"].str.contains(search_query, case=False, na=False) |
                filtered_df["Description"].str.contains(search_query, case=False, na=False)
            ]
        if selected_sources:
            filtered_df = filtered_df[filtered_df["Source"].isin(selected_sources)]
        if selected_stages:
            filtered_df = filtered_df[filtered_df["Startup Stage"].isin(selected_stages)]
        if selected_work_models:
            filtered_df = filtered_df[filtered_df["Work Model"].isin(selected_work_models)]
        if deadline_filter:
            # Convert to pandas datetime for comparison, accounting for mixed types/NAs
            valid_deadlines = pd.to_datetime(filtered_df["Deadline"], errors='coerce')
            filtered_df = filtered_df[valid_deadlines.dt.date >= deadline_filter]

    # ── KPI Metrics ───────────────────────────────────────────────────────────
    st.markdown('<div class="section-header">⚡ Pipeline KPIs</div>', unsafe_allow_html=True)
    
    total_opps = stats.get("total_opportunities", len(df))
    active_sources = stats.get("active_sources", len(sources))
    ai_enriched = stats.get("ai_tagged_count", 0)
    ai_pct = stats.get("ai_tagged_pct", 0)
    
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.markdown(f'<div class="kpi-card"><div class="kpi-value">{total_opps}</div><div class="kpi-label">Total Opportunities</div></div>', unsafe_allow_html=True)
    with col2:
        st.markdown(f'<div class="kpi-card"><div class="kpi-value">{len(filtered_df)}</div><div class="kpi-label">Filtered Results</div></div>', unsafe_allow_html=True)
    with col3:
        st.markdown(f'<div class="kpi-card"><div class="kpi-value">{active_sources}</div><div class="kpi-label">Active Sources</div></div>', unsafe_allow_html=True)
    with col4:
        st.markdown(f'<div class="kpi-card"><div class="kpi-value">{ai_enriched}</div><div class="kpi-label">AI-Enriched ({ai_pct}%)</div></div>', unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Audit Log Expander ────────────────────────────────────────────────────
    with st.expander("⚙️ System Health & Scraper Audit Log"):
        if runs:
            audit_data = []
            for r in runs:
                errors = r.get("errors_encountered", [])
                audit_data.append({
                    "Run ID": r.get("id", ""),
                    "Status": "✅ Success" if r.get("status") == "success" else f"❌ {r.get('status', 'failed').title()}",
                    "Source": r.get("source", ""),
                    "Time": pd.to_datetime(r.get("started_at")).strftime("%Y-%m-%d %H:%M") if r.get("started_at") else "",
                    "Items Added": r.get("items_added", 0),
                    "Errors": len(errors)
                })
            st.dataframe(pd.DataFrame(audit_data), use_container_width=True, hide_index=True)
        else:
            st.info("No scraper runs recorded in the audit log yet.")

    # ── Main Data Table ───────────────────────────────────────────────────────
    st.markdown('<div class="section-header">📋 Opportunities Database</div>', unsafe_allow_html=True)

    if not filtered_df.empty:
        # Simplify display
        display_df = filtered_df[["Title", "Organization", "Source", "Startup Stage", "Work Model", "Deadline", "URL"]].copy()
        
        # Format Deadline neatly
        display_df["Deadline"] = pd.to_datetime(display_df["Deadline"], errors='coerce').dt.strftime('%b %d, %Y').fillna('—')
        
        st.dataframe(
            display_df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "URL": st.column_config.LinkColumn("Source Link", display_text="🔗 Open Link"),
                "Title": st.column_config.TextColumn("Opportunity Title", width="large")
            }
        )

        st.markdown("<br>", unsafe_allow_html=True)
        csv = filtered_df.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="⬇️ Export Filtered Data as CSV",
            data=csv,
            file_name=f"alfaleus_opportunities_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
            mime='text/csv',
        )
    else:
        st.warning("No opportunities match your current filters.")

if __name__ == "__main__":
    main()

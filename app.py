"""
app.py — Alfaleus Streamlit Analytical Control Room
A production-grade, glassmorphic analytics control room for opportunity aggregation.
"""
import streamlit as st
import pandas as pd
import os
import numpy as np
from pymongo import MongoClient
from datetime import datetime, timezone, timedelta
import plotly.express as px
import plotly.graph_objects as go

# Configuration & Connection
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
    page_title="Alfaleus | Ingestion Analytics & Control Room",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Premium Dark-Mode & Glassmorphic Custom Styling
st.markdown("""
<style>
  @import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&display=swap');
  html, body, [class*="css"] { font-family: 'Outfit', sans-serif; }
  
  /* Glassmorphic Container styling */
  .stTabs [data-baseweb="tab-list"] {
    gap: 12px;
    background-color: rgba(255, 255, 255, 0.02);
    padding: 10px;
    border-radius: 12px;
    border: 1px solid rgba(255, 255, 255, 0.05);
  }
  
  .stTabs [data-baseweb="tab"] {
    height: 45px;
    white-space: pre-wrap;
    background-color: transparent;
    border-radius: 8px;
    color: #a0aec0;
    font-weight: 500;
    transition: all 0.3s ease;
    border: none;
    padding: 0 20px;
  }
  
  .stTabs [data-baseweb="tab"]:hover {
    color: #63b3ed;
    background-color: rgba(99, 179, 237, 0.08);
  }
  
  .stTabs [aria-selected="true"] {
    background-color: rgba(99, 179, 237, 0.15) !important;
    color: #63b3ed !important;
    border-bottom: 2px solid #63b3ed !important;
  }

  /* Glassmorphic KPI Cards */
  .kpi-card {
    background: linear-gradient(135deg, rgba(255,255,255,0.03) 0%, rgba(255,255,255,0.01) 100%);
    border: 1px solid rgba(99, 179, 237, 0.15);
    border-radius: 16px;
    padding: 22px 18px;
    text-align: center;
    backdrop-filter: blur(12px);
    box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.2);
    transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  }
  .kpi-card:hover { 
    border-color: rgba(99, 179, 237, 0.45); 
    transform: translateY(-3px);
    box-shadow: 0 12px 40px 0 rgba(99, 179, 237, 0.1);
  }
  .kpi-value { 
    font-size: 2.6rem; 
    font-weight: 700; 
    background: linear-gradient(90deg, #63b3ed, #9f7aea);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    line-height: 1.1; 
  }
  .kpi-label { 
    font-size: 0.8rem; 
    color: #a0aec0; 
    text-transform: uppercase; 
    letter-spacing: 0.1em; 
    margin-top: 8px; 
    font-weight: 600;
  }

  .section-header {
    font-size: 1.25rem; font-weight: 600; color: #f7fafc;
    border-left: 4px solid #63b3ed; padding-left: 14px;
    margin: 28px 0 18px 0;
  }
</style>
""", unsafe_allow_html=True)

# ── Data Ingestion & Cache Mapping ────────────────────────────────────────────

@st.cache_data(ttl=120, show_spinner=False)
def fetch_opportunities() -> pd.DataFrame:
    """Fetch opportunities from database including calculated analytical dimensions."""
    try:
        db = get_database()
        cursor = db.opportunities.find().sort("scraped_at", -1)
        all_data = []
        for d in cursor:
            d["_id"] = str(d["_id"])
            all_data.append(d)
    except Exception as e:
        st.error(f"Database Ingestion Failure: {e}")
        all_data = []
            
    if not all_data:
        return pd.DataFrame()
        
    rows = []
    for d in all_data:
        ai = d.get("ai_tags") or {}
        analytics = d.get("analytics") or {}
        
        # Determine Work Model label
        work_model = "Unknown"
        if ai.get("is_remote") is True:
            work_model = "Remote"
        elif ai.get("is_remote") is False:
            work_model = "On-site"

        rows.append({
            "ID": d.get("_id", ""),
            "Title": d.get("title", ""),
            "Description": d.get("description", ""),
            "URL": d.get("source_url", ""),
            "Source": d.get("source", ""),
            "Organization": d.get("organization", ""),
            "Startup Stage": ai.get("startup_stage", "Unknown"),
            "Work Model": work_model,
            "Deadline": d.get("deadline", None),
            "Created At": d.get("created_at", None),
            
            # Diagnostic Analytics Dimensions
            "Character Count": analytics.get("character_count", len(d.get("description", ""))),
            "Word Count": analytics.get("word_count", len(d.get("description", "").split())),
            "HTML Element Count": analytics.get("html_element_count", 0),
            "Structural Density": analytics.get("text_ratio", 0.0),
            "List Item Count": analytics.get("list_item_count", 0),
            "Scraped At": analytics.get("scraped_at", d.get("created_at", None)),
            "Enriched At": analytics.get("enriched_at", None),
        })
    return pd.DataFrame(rows)

@st.cache_data(ttl=120, show_spinner=False)
def fetch_scraper_runs() -> pd.DataFrame:
    """Fetch completed scraper telemetry runs for velocity metrics."""
    try:
        db = get_database()
        cursor = db.scraper_runs.find().sort("started_at", -1).limit(50)
        runs = []
        for r in cursor:
            r["_id"] = str(r["_id"])
            analytics = r.get("analytics") or {}
            
            runs.append({
                "Run ID": r.get("_id", ""),
                "Source": r.get("source", "unknown"),
                "Status": r.get("status", "failed"),
                "Started At": r.get("started_at", None),
                "Completed At": r.get("completed_at", None),
                "Items Scraped": r.get("items_scraped", 0),
                "Validated Count": r.get("validated_count", 0),
                "Items Added": r.get("items_added", 0),
                "Items Duplicate": r.get("items_duplicate", 0),
                
                # Descriptive Telemetry metrics
                "Execution Duration (sec)": analytics.get("execution_duration_seconds", 0.0),
                "Throughput Velocity (items/sec)": analytics.get("throughput_velocity", 0.0),
                "Source Distribution": analytics.get("source_distribution", {}),
                "Errors": len(r.get("errors_encountered", []))
            })
        return pd.DataFrame(runs)
    except Exception as e:
        return pd.DataFrame()

@st.cache_data(ttl=120, show_spinner=False)
def fetch_stats() -> dict:
    """Fetch global pipeline stats."""
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
    except Exception:
        return {}

# ── Custom Predictive Priority Scoring ─────────────────────────────────────────

def calculate_priority_scores(df: pd.DataFrame, w_deadline: float, w_stage: float, w_complexity: float) -> pd.DataFrame:
    """Vectorized calculation of dynamic Opportunity Priority Scores (0-100%)."""
    if df.empty:
        return df
        
    scores_deadline = []
    now = datetime.now(timezone.utc)
    
    for dl in df["Deadline"]:
        if pd.isna(dl) or dl is None:
            scores_deadline.append(0.0)
        else:
            # Ensure timezone-awareness
            if isinstance(dl, str):
                try:
                    dl_dt = pd.to_datetime(dl).to_pydatetime()
                except Exception:
                    scores_deadline.append(0.0)
                    continue
            else:
                dl_dt = dl
                
            if dl_dt.tzinfo is None:
                dl_dt = dl_dt.replace(tzinfo=timezone.utc)
                
            time_diff = (dl_dt - now).days
            if time_diff < 0:
                scores_deadline.append(0.0)  # Past deadline
            else:
                # Closer deadlines score higher; capped at 30 days
                scores_deadline.append(max(0.0, 1.0 - (time_diff / 30.0)))
                
    # Stage Scores mapping
    stage_weights = {
        "Idea": 1.0, "Pre-seed": 1.0, "Seed": 1.0,
        "Early": 0.6, "Growth": 0.4, "All": 0.5, "Unknown": 0.2
    }
    scores_stage = df["Startup Stage"].map(stage_weights).fillna(0.2)
    
    # Complexity Score (normalized word count cap at 600 words)
    scores_complexity = (df["Word Count"] / 600.0).clip(0.0, 1.0)
    
    # Combine scores using custom user weights
    denom = (w_deadline + w_stage + w_complexity) or 1e-6
    scores_final = (
        (w_deadline * pd.Series(scores_deadline) +
         w_stage * scores_stage +
         w_complexity * scores_complexity) / denom * 100.0
    )
    
    df["Priority Score"] = scores_final.round(1)
    return df

# ── Main Dashboard Application ────────────────────────────────────────────────

def main():
    st.markdown(
        """
        <div style="text-align:center;padding:15px 0 10px">
          <h1 style="font-size:2.8rem;font-weight:700;background:linear-gradient(90deg,#63b3ed,#9f7aea);
                     -webkit-background-clip:text;-webkit-text-fill-color:transparent;margin:0">
            🚀 Alfaleus Control Room
          </h1>
          <p style="color:#718096;font-size:1.1rem;margin-top:6px">
            Core Data Analytics & Operational Telemetry Center
          </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Ingest baseline structures
    with st.spinner("Synchronizing pipeline telemetry..."):
        df_opps = fetch_opportunities()
        df_runs = fetch_scraper_runs()
        stats = fetch_stats()

    # Graceful Empty-State Check
    if df_opps.empty:
        st.markdown("<br><br>", unsafe_allow_html=True)
        st.warning("⚠️ **System Offline / Empty Database State Detected**")
        st.info("The MongoDB collection is currently empty or unreachable. Please launch your scrapers to ingest initial opportunities.")
        
        # Display small system diagnostic check
        col1, col2 = st.columns(2)
        with col1:
            st.metric("MongoDB Local Sockets", "Listening (27017)" if get_database() is not None else "Unreachable")
        with col2:
            if st.button("🔄 Force Re-check Sockets"):
                st.rerun()
        return

    # ── Sidebar Filter Control and Scoring Weights ─────────────────────────────
    with st.sidebar:
        st.markdown("### 🎛️ Analytical Controls")
        
        st.markdown("#### **Priority Score Weights**")
        w_deadline = st.slider("📅 Deadline Urgency", 0.0, 1.0, 0.6, 0.1)
        w_stage = st.slider("🌱 Startup Stage Priority", 0.0, 1.0, 0.5, 0.1)
        w_complexity = st.slider("✍️ Text Complexity", 0.0, 1.0, 0.3, 0.1)
        
        st.markdown("---")
        st.markdown("#### **Interactive Filters**")
        
        search_query = st.text_input("🔎 Search Opportunities", placeholder="Keyword matching...")
        
        sources = sorted([s for s in df_opps["Source"].unique() if s])
        stages = sorted([s for s in df_opps["Startup Stage"].unique() if s])
        work_models = sorted([s for s in df_opps["Work Model"].unique() if s])

        selected_sources = st.multiselect("Source", options=sources, default=[])
        selected_stages = st.multiselect("Startup Stage (AI)", options=stages, default=[])
        selected_work_models = st.multiselect("Work Model (AI)", options=work_models, default=[])
        
        deadline_filter = st.date_input("Deadline Limit (After)", value=None)

        if st.button("🔄 Clear & Refresh Telemetry", use_container_width=True):
            fetch_opportunities.clear()
            fetch_scraper_runs.clear()
            fetch_stats.clear()
            st.rerun()

    # Dynamic scoring evaluation
    df_opps = calculate_priority_scores(df_opps, w_deadline, w_stage, w_complexity)

    # Ingestion Filters Application
    filtered_df = df_opps.copy()
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
        valid_deadlines = pd.to_datetime(filtered_df["Deadline"], errors='coerce')
        filtered_df = filtered_df[valid_deadlines.dt.date >= deadline_filter]

    # ── Pipeline KPI Cards ────────────────────────────────────────────────────
    st.markdown('<div class="section-header">⚡ Pipeline Ingestion KPIs</div>', unsafe_allow_html=True)
    
    total_opps = stats.get("total_opportunities", len(df_opps))
    active_sources_cnt = stats.get("active_sources", len(sources))
    ai_enriched_cnt = stats.get("ai_tagged_count", 0)
    ai_pct = stats.get("ai_tagged_pct", 0)
    
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.markdown(f'<div class="kpi-card"><div class="kpi-value">{total_opps}</div><div class="kpi-label">Ingested Opportunities</div></div>', unsafe_allow_html=True)
    with col2:
        st.markdown(f'<div class="kpi-card"><div class="kpi-value">{len(filtered_df)}</div><div class="kpi-label">Filtered Results</div></div>', unsafe_allow_html=True)
    with col3:
        st.markdown(f'<div class="kpi-card"><div class="kpi-value">{active_sources_cnt}</div><div class="kpi-label">Active Core Channels</div></div>', unsafe_allow_html=True)
    with col4:
        st.markdown(f'<div class="kpi-card"><div class="kpi-value">{ai_pct}%</div><div class="kpi-label">AI Enrichment Coverage</div></div>', unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Core Analytics Control Tabs ───────────────────────────────────────────
    tab1, tab2, tab3 = st.tabs([
        "📋 Opportunities Control Room",
        "📊 Ingestion Analytics & Profiling",
        "📈 Ingestion Velocity & Telemetry"
    ])

    # ==========================================================================
    # Tab 1: Opportunities Control Room
    # ==========================================================================
    with tab1:
        st.markdown("### 📋 Filtered Ingest Priorities")
        
        if not filtered_df.empty:
            # Sort by priority score descending
            display_df = filtered_df.sort_values(by="Priority Score", ascending=False).copy()
            
            # Format display dataframe
            show_df = display_df[["Priority Score", "Title", "Organization", "Source", "Startup Stage", "Work Model", "Word Count", "URL"]].copy()
            
            st.dataframe(
                show_df,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Priority Score": st.column_config.ProgressColumn(
                        "Priority Score",
                        help="Calculated dynamically using customized weights",
                        format="%.1f%%",
                        min_value=0.0,
                        max_value=100.0,
                    ),
                    "URL": st.column_config.LinkColumn("Source Link", display_text="🔗 View Ingest"),
                    "Title": st.column_config.TextColumn("Opportunity Title", width="large")
                }
            )

            # Export capability
            csv = display_df.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="⬇️ Export Sorted Diagnostic Control File (CSV)",
                data=csv,
                file_name=f"control_priorities_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
                mime='text/csv',
            )
        else:
            st.warning("No opportunities match your selected filter matrix.")

    # ==========================================================================
    # Tab 2: Ingestion Analytics & Profiling
    # ==========================================================================
    with tab2:
        st.markdown("### 📊 Ingestion Profiling & Structural Insights")
        
        # Sub-stats diagnostics
        avg_word = round(df_opps["Word Count"].mean(), 1)
        avg_density = round(df_opps["Structural Density"].mean(), 4)
        avg_elements = round(df_opps["HTML Element Count"].mean(), 1)
        
        stat_col1, stat_col2, stat_col3 = st.columns(3)
        with stat_col1:
            st.metric("Average Ingest Word Count", f"{avg_word} words")
        with stat_col2:
            st.metric("Average Structural Density", f"{avg_density}", help="HTML Elements per character")
        with stat_col3:
            st.metric("Average Raw HTML Elements", f"{avg_elements} tags")
            
        st.markdown("---")
        
        chart_col1, chart_col2 = st.columns(2)
        
        with chart_col1:
            st.markdown("#### **Diagnostic: Structural Density vs. Word Count**")
            # Build Scatter Plot using Plotly Express
            fig_scatter = px.scatter(
                df_opps,
                x="Word Count",
                y="Structural Density",
                color="Startup Stage",
                hover_data=["Title", "Source", "HTML Element Count"],
                color_discrete_sequence=px.colors.qualitative.G10,
                template="plotly_dark"
            )
            fig_scatter.update_layout(
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                xaxis=dict(gridcolor="rgba(255,255,255,0.05)"),
                yaxis=dict(gridcolor="rgba(255,255,255,0.05)")
            )
            st.plotly_chart(fig_scatter, use_container_width=True)
            st.caption("Visualizes raw XML/HTML parsing complexity. Complex layouts cluster differently based on core tags.")

        with chart_col2:
            st.markdown("#### **Domain Clustering: Startup Stage vs. Work Model**")
            # Build Grouped Distribution Histogram using Plotly Express
            fig_hist = px.histogram(
                df_opps,
                x="Startup Stage",
                color="Work Model",
                barmode="group",
                color_discrete_sequence=["#63b3ed", "#9f7aea", "#a0aec0"],
                template="plotly_dark"
            )
            fig_hist.update_layout(
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                xaxis=dict(gridcolor="rgba(255,255,255,0.05)"),
                yaxis=dict(gridcolor="rgba(255,255,255,0.05)")
            )
            st.plotly_chart(fig_hist, use_container_width=True)
            st.caption("Distribution of opportunity metadata classified by the downstream AI Enrichment logic.")

    # ==========================================================================
    # Tab 3: Ingestion Velocity & Telemetry
    # ==========================================================================
    with tab3:
        st.markdown("### 📈 Pipeline Velocity Telemetry Trends")
        
        if not df_runs.empty:
            # Sort by date for proper time series
            df_runs_sorted = df_runs.sort_values(by="Started At").copy()
            
            # 1. Visualization: Ingestion Velocity Trends over time
            # Check if Throughput Velocity exists and is calculated
            if "Throughput Velocity (items/sec)" in df_runs_sorted.columns:
                st.markdown("#### **Throughput Velocity Trend (Items per Second)**")
                fig_line = px.area(
                    df_runs_sorted,
                    x="Started At",
                    y="Throughput Velocity (items/sec)",
                    color="Source",
                    labels={"Started At": "Execution Timestamp", "Throughput Velocity (items/sec)": "Throughput Velocity (items/sec)"},
                    color_discrete_sequence=["#63b3ed", "#9f7aea"],
                    template="plotly_dark"
                )
                fig_line.update_layout(
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    xaxis=dict(gridcolor="rgba(255,255,255,0.05)"),
                    yaxis=dict(gridcolor="rgba(255,255,255,0.05)")
                )
                st.plotly_chart(fig_line, use_container_width=True)
                st.caption("Ingestion velocity tracking. Showcase network efficiency, scraper parser metrics, and throttle limits.")
            
            st.markdown("---")
            st.markdown("#### **Chronological Batch Execution Telemetry**")
            
            # Format and show runs
            show_runs_df = df_runs[["Started At", "Source", "Status", "Items Scraped", "Items Added", "Items Duplicate", "Execution Duration (sec)", "Throughput Velocity (items/sec)"]].copy()
            show_runs_df["Started At"] = pd.to_datetime(show_runs_df["Started At"]).dt.strftime("%Y-%m-%d %H:%M:%S")
            
            st.dataframe(
                show_runs_df,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Execution Duration (sec)": st.column_config.NumberColumn("Duration", format="%.2fs"),
                    "Throughput Velocity (items/sec)": st.column_config.NumberColumn("Throughput", format="%.2f items/s")
                }
            )
        else:
            st.info("Operational logs are currently empty. Telemetry will sync during initial scraper batch operations.")

if __name__ == "__main__":
    main()

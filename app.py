import sqlite3
import pandas as pd
import streamlit as st

# Page Configuration
st.set_page_config(page_title="Traficom IFIC Natural Language Explorer", layout="wide")

st.title("📡 ITU BR IFIC — Natural Language Spectrum Explorer")
st.markdown(
    "Type your query in plain English or select a preset to query the Space IFIC #3074 database view."
)

# Database Connection
DB_PATH = "ific3074.sqlite"


@st.cache_resource
def get_connection():
    return sqlite3.connect(DB_PATH, check_same_thread=False)


conn = get_connection()


# --- Helper: Simple rule-based/semantic fallback translator (or plug in an LLM API here) ---
def natural_language_to_sql(prompt: str) -> str:
    prompt_lower = prompt.lower()

    # Preset or keyword mappings tailored to your hackathon view
    if "usa" in prompt_lower or "united states" in prompt_lower:
        return "SELECT ntc_id, submitting_country, beam_name, freq_min, freq_max, pwr_ds_max, gso_longitude FROM vw_space_3074_pfd_inputs WHERE submitting_country = 'USA' LIMIT 20;"
    elif (
        "ku band" in prompt_lower or "14 ghz" in prompt_lower or "14000" in prompt_lower
    ):
        return "SELECT ntc_id, submitting_country, beam_name, freq_min, freq_max, pwr_ds_max, gso_longitude FROM vw_space_3074_pfd_inputs WHERE freq_max >= 14000 AND freq_min <= 14500 LIMIT 20;"
    elif "finland" in prompt_lower or "fin" in prompt_lower:
        return "SELECT ntc_id, submitting_country, beam_name, freq_min, freq_max, affected_country FROM vw_space_3074_pfd_inputs WHERE affected_country = 'FIN' LIMIT 20;"
    else:
        # Default fallback view query
        return f"SELECT ntc_id, submitting_country, beam_name, freq_min, freq_max, pwr_ds_max, gso_longitude FROM vw_space_3074_pfd_inputs WHERE beam_name LIKE '%{prompt.strip().upper()}%' LIMIT 20;"


# --- UI Layout: Natural Language Input Bar ---
st.subheader("💬 Natural Language Search Bar")
user_prompt = st.text_input(
    "Ask a question about the spectrum data:",
    placeholder="e.g., Show USA satellite notices or find Ku-band assignments around 14 GHz...",
)

# Quick Preset Buttons for Hackathon Evaluation
col1, col2, col3 = st.columns(3)
with col1:
    if st.button("🇺🇸 Show USA Filings"):
        user_prompt = "Show USA satellite notices"
with col2:
    if st.button("📡 Ku-Band (14 GHz) Overlaps"):
        user_prompt = "Find Ku band 14 GHz frequencies"
with col3:
    if st.button("🇫🇮 Finland Affected Notices"):
        user_prompt = "Show notices affecting Finland"

# Execute translation and query
if user_prompt:
    generated_sql = natural_language_to_sql(user_prompt)

    with st.expander(
        "🔍 View Generated SQL Query (Auditability & Provenance)", expanded=True
    ):
        st.code(generated_sql, language="sql")

    try:
        df = pd.read_sql_query(generated_sql, conn)
        st.success(
            found_msg := f"Query executed successfully! Returned **{len(df)}** rows."
        )
        st.dataframe(df, use_container_width=True)

        # Quick CSV Export for Experts
        csv = df.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="📥 Download Results as CSV",
            data=csv,
            file_name="ific_query_results.csv",
            mime="text/csv",
        )
    except Exception as e:
        st.error(f"Database Query Error: {e}")

# Footer info
st.markdown("---")
st.caption(
    "Traficom Hackathon 2026 — Built with SQLite views and Streamlit for transparent, auditable spectrum analysis."
)

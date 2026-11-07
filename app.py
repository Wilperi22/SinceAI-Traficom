import sqlite3
import pandas as pd
import streamlit as st
import folium
from streamlit_folium import st_folium
from folium.plugins import MarkerCluster

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

# --- Load Finland Station Data (from challenge brief XLSX) ---
@st.cache_data
def load_finland_stations():
    xlsx_path = "Traficom materials/Traficom_SinceAI_Frequencies_of_interest_v05112026.xlsx"
    df = pd.read_excel(xlsx_path, header=10, skiprows=[11])
    # Clean column names
    df.columns = [
        "station_number",
        "class_of_licence",
        "longitude",
        "latitude",
        "ground_height_m",
        "mast_height_m",
        "mast_type",
        "channel_bandwidth",
        "tx_frequency",
        "rx_frequency",
        "ant_height_agl_m",
        "ant_height_asl_m",
        "ant_gain_db",
        "class_of_transmission",
        "transmission_bandwidth",
        "radiation_direction_deg",
        "polarisation",
        "radiation_power",
        "radiation_power_itu",
        "radiation_power_unit",
        "transmission_line_db",
        "directivity",
    ]
    # Drop rows with missing coordinates
    df = df.dropna(subset=["longitude", "latitude"])
    # Convert numeric columns
    for col in [
        "longitude",
        "latitude",
        "ground_height_m",
        "mast_height_m",
        "ant_height_agl_m",
        "ant_height_asl_m",
        "ant_gain_db",
        "radiation_power",
        "transmission_line_db",
    ]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


stations_df = load_finland_stations()

# --- Helper: Simple rule-based/semantic fallback translator (or plug in an LLM API here) ---
def natural_language_to_sql(prompt: str) -> str:
    prompt_lower = prompt.lower()

    if "usa" in prompt_lower or "united states" in prompt_lower:
        return "SELECT ntc_id, submitting_country, beam_name, freq_min, freq_max, pwr_ds_max, gso_longitude FROM vw_space_3074_pfd_inputs WHERE submitting_country = 'USA' LIMIT 20;"
    elif (
        "ku band" in prompt_lower or "14 ghz" in prompt_lower or "14000" in prompt_lower
    ):
        return "SELECT ntc_id, submitting_country, beam_name, freq_min, freq_max, pwr_ds_max, gso_longitude FROM vw_space_3074_pfd_inputs WHERE freq_max >= 14000 AND freq_min <= 14500 LIMIT 20;"
    elif "finland" in prompt_lower or "fin" in prompt_lower:
        return "SELECT ntc_id, submitting_country, beam_name, freq_min, freq_max, affected_country FROM vw_space_3074_pfd_inputs WHERE affected_country = 'FIN' LIMIT 20;"
    else:
        return f"SELECT ntc_id, submitting_country, beam_name, freq_min, freq_max, pwr_ds_max, gso_longitude FROM vw_space_3074_pfd_inputs WHERE beam_name LIKE '%{prompt.strip().upper()}%' LIMIT 20;"


# --- Tabs for NL Query and Interactive Map ---
tab1, tab2 = st.tabs(["💬 Natural Language Query", "🗺️ Interactive Finland Station Map"])

with tab1:
    # --- UI Layout: Natural Language Input Bar ---
    st.subheader("💬 Natural Language Search Bar")
    user_prompt = st.text_input(
        "Ask a question about the spectrum data:",
        placeholder="e.g., Show USA satellite notices or find Ku-band assignments around 14 GHz...",
        key="nl_prompt",
    )

    # Quick Preset Buttons for Hackathon Evaluation
    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("🇺🇸 Show USA Filings", key="btn_usa"):
            user_prompt = "Show USA satellite notices"
    with col2:
        if st.button("📡 Ku-Band (14 GHz) Overlaps", key="btn_kuband"):
            user_prompt = "Find Ku band 14 GHz frequencies"
    with col3:
        if st.button("🇫🇮 Finland Affected Notices", key="btn_fin"):
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

with tab2:
    st.subheader("🗺️ Interactive Map of Finnish Frequency Stations")
    st.markdown(
        "Synthetic dataset of frequencies of special interest in Finland (from challenge brief). "
        "874 stations with coordinates, technical parameters, and transmission details."
    )

    # Sidebar filters for the map
    with st.sidebar:
        st.header("🗺️ Map Filters")

        # Class of licence filter
        licence_options = ["All"] + sorted(stations_df["class_of_licence"].dropna().unique().tolist())
        selected_licence = st.selectbox("Class of Licence", licence_options, key="map_licence")

        # Polarisation filter
        pol_options = ["All"] + sorted(stations_df["polarisation"].dropna().unique().tolist())
        selected_pol = st.selectbox("Polarisation", pol_options, key="map_pol")

        # Directivity filter
        dir_options = ["All"] + sorted(stations_df["directivity"].dropna().unique().tolist())
        selected_dir = st.selectbox("Directivity", dir_options, key="map_dir")

        # Mast type filter (only KIINTEÄ in this dataset)
        mast_options = ["All"] + sorted(stations_df["mast_type"].dropna().unique().tolist())
        selected_mast = st.selectbox("Mast Type", mast_options, key="map_mast")

        # Frequency range filter
        freq_min, freq_max = st.slider(
            "TX Frequency Range (MHz)",
            min_value=0,
            max_value=10000,
            value=(0, 10000),
            step=100,
            key="map_freq_range",
        )

        # Power filter - use 99th percentile to avoid extreme outliers
        power_max_val = float(stations_df["radiation_power"].quantile(0.99) or 1000)
        power_min, power_max = st.slider(
            "Radiation Power (kW)",
            min_value=0.0,
            max_value=power_max_val,
            value=(0.0, power_max_val),
            step=1.0,
            key="map_power_range",
        )

    # Apply filters
    filtered_df = stations_df.copy()

    if selected_licence != "All":
        filtered_df = filtered_df[filtered_df["class_of_licence"] == selected_licence]
    else:
        # Exclude NaN licence classes by default unless explicitly selected
        filtered_df = filtered_df[filtered_df["class_of_licence"].notna()]

    if selected_pol != "All":
        filtered_df = filtered_df[filtered_df["polarisation"] == selected_pol]

    if selected_dir != "All":
        filtered_df = filtered_df[filtered_df["directivity"] == selected_dir]
    else:
        # Exclude NaN directivity by default
        filtered_df = filtered_df[filtered_df["directivity"].notna()]

    if selected_mast != "All":
        filtered_df = filtered_df[filtered_df["mast_type"] == selected_mast]

    # Extract numeric frequency in MHz for filtering
    def extract_freq_mhz(freq_str):
        if pd.isna(freq_str):
            return None
        freq_str = str(freq_str).upper()
        if "MHZ" in freq_str:
            return float(freq_str.replace("MHZ", "").replace(",", "").strip())
        elif "GHZ" in freq_str:
            return float(freq_str.replace("GHZ", "").replace(",", "").strip()) * 1000
        elif "KHZ" in freq_str:
            return float(freq_str.replace("KHZ", "").replace(",", "").strip()) / 1000
        try:
            return float(freq_str)
        except:
            return None

    filtered_df["tx_freq_mhz"] = filtered_df["tx_frequency"].apply(extract_freq_mhz)
    filtered_df = filtered_df[
        (filtered_df["tx_freq_mhz"] >= freq_min) & (filtered_df["tx_freq_mhz"] <= freq_max)
    ]

    filtered_df = filtered_df[
        (filtered_df["radiation_power"] >= power_min) & (filtered_df["radiation_power"] <= power_max)
    ]

    st.metric("Stations Displayed", len(filtered_df))

    # Map options
    col_map1, col_map2 = st.columns([3, 1])
    with col_map2:
        base_map = st.selectbox(
            "Base Map",
            ["OpenStreetMap", "CartoDB Positron", "CartoDB Dark_Matter", "Stamen Terrain"],
            key="map_base",
        )
        use_clustering = st.checkbox("Cluster Markers", value=True, key="map_cluster")
    with col_map1:
        st.write("")  # spacer

    # Create Folium map centered on Finland
    finland_center = [64.0, 26.0]
    m = folium.Map(location=finland_center, zoom_start=5, tiles=base_map)

    # Color mapping for polarisation
    pol_colors = {
        "H": "blue",
        "V": "red",
        "M": "green",
        "V+H": "purple",
        "D": "orange",
    }

    # Color mapping for directivity
    dir_colors = {
        "ND": "gray",
        "D": "darkred",
    }

    # Add station markers
    if use_clustering:
        marker_cluster = MarkerCluster().add_to(m)
        target = marker_cluster
    else:
        target = m

    for _, row in filtered_df.iterrows():
        # Determine color based on polarisation (default) or directivity
        color = pol_colors.get(row["polarisation"], "blue")
        if selected_pol == "All" and selected_dir != "All":
            color = dir_colors.get(row["directivity"], "gray")

        # Create popup content
        popup_html = f"""
        <div style="width: 250px;">
            <h4>Station {int(row['station_number'])}</h4>
            <table style="font-size: 12px;">
                <tr><td><b>Licence Class:</b></td><td>{row['class_of_licence']}</td></tr>
                <tr><td><b>Coordinates:</b></td><td>{row['latitude']:.4f}, {row['longitude']:.4f}</td></tr>
                <tr><td><b>Ground Height:</b></td><td>{row['ground_height_m']:.0f} m</td></tr>
                <tr><td><b>Mast Height:</b></td><td>{row['mast_height_m']:.0f} m</td></tr>
                <tr><td><b>Ant Height AGL:</b></td><td>{row['ant_height_agl_m']:.0f} m</td></tr>
                <tr><td><b>Ant Height ASL:</b></td><td>{row['ant_height_asl_m']:.0f} m</td></tr>
                <tr><td><b>TX Frequency:</b></td><td>{row['tx_frequency']}</td></tr>
                <tr><td><b>RX Frequency:</b></td><td>{row['rx_frequency']}</td></tr>
                <tr><td><b>Channel BW:</b></td><td>{row['channel_bandwidth']}</td></tr>
                <tr><td><b>Polarisation:</b></td><td>{row['polarisation']}</td></tr>
                <tr><td><b>Directivity:</b></td><td>{row['directivity']}</td></tr>
                <tr><td><b>Radiation Power:</b></td><td>{row['radiation_power']} {row['radiation_power_unit']}</td></tr>
                <tr><td><b>Ant Gain:</b></td><td>{row['ant_gain_db']} dB</td></tr>
                <tr><td><b>Tx Class:</b></td><td>{row['class_of_transmission']}</td></tr>
                <tr><td><b>Tx Bandwidth:</b></td><td>{row['transmission_bandwidth']}</td></tr>
                <tr><td><b>Radiation Dir:</b></td><td>{row['radiation_direction_deg']}°</td></tr>
                <tr><td><b>Trans Line Loss:</b></td><td>{row['transmission_line_db']} dB</td></tr>
            </table>
        </div>
        """

        folium.CircleMarker(
            location=[row["latitude"], row["longitude"]],
            radius=6,
            color=color,
            fill=True,
            fill_color=color,
            fill_opacity=0.7,
            popup=folium.Popup(popup_html, max_width=300),
            tooltip=f"Station {int(row['station_number'])} | {row['polarisation']} | {row['tx_frequency']}",
        ).add_to(target)

    # Add legend
    legend_html = """
    <div style="position: fixed; bottom: 50px; left: 50px; z-index: 1000; background-color: white;
                padding: 10px; border: 2px solid grey; border-radius: 5px; font-size: 12px;">
    <b>Polarisation</b><br>
    """
    for pol, col in pol_colors.items():
        legend_html += f'<i style="background:{col}; width: 12px; height: 12px; display: inline-block; border-radius: 50%; margin-right: 5px;"></i> {pol}<br>'
    legend_html += "<br><b>Directivity</b><br>"
    for d, col in dir_colors.items():
        legend_html += f'<i style="background:{col}; width: 12px; height: 12px; display: inline-block; border-radius: 50%; margin-right: 5px;"></i> {d}<br>'
    legend_html += "</div>"
    m.get_root().html.add_child(folium.Element(legend_html))

    # Render map
    st_folium(m, width=1200, height=600, returned_objects=[])

    # Show data table below map
    with st.expander("📋 Station Data Table", expanded=False):
        display_cols = [
            "station_number",
            "class_of_licence",
            "longitude",
            "latitude",
            "ground_height_m",
            "mast_height_m",
            "tx_frequency",
            "rx_frequency",
            "channel_bandwidth",
            "polarisation",
            "directivity",
            "radiation_power",
            "radiation_power_unit",
            "class_of_transmission",
        ]
        st.dataframe(filtered_df[display_cols], use_container_width=True, height=400)

        csv = filtered_df[display_cols].to_csv(index=False).encode("utf-8")
        st.download_button(
            label="📥 Download Filtered Stations as CSV",
            data=csv,
            file_name="finland_stations_filtered.csv",
            mime="text/csv",
        )

# Footer info
st.markdown("---")
st.caption(
    "Traficom Hackathon 2026 — Built with SQLite views and Streamlit for transparent, auditable spectrum analysis."
)
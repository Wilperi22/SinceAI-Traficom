import json
import requests
import folium
from folium.plugins import MarkerCluster

def parse_finnish_coordinate(coord_str):
    """Convert Finnish coordinate format (e.g., '62N5005') to decimal degrees"""
    # Extract degrees, direction, minutes, seconds
    # Pattern: DD[NS][MM][SS] or D[NS][MM][SS] for longitude
    direction = coord_str[-1]
    # Find where minutes start (first digit that's not part of degrees)
    # Degrees can be 1-2 digits for latitude, 1-4 for longitude
    if 'N' in coord_str or 'S' in coord_str:
        # Latitude: 2-4 digits total (DDD or DD)
        deg_end = coord_str.find('N') if 'N' in coord_str else coord_str.find('S')
        degrees = int(coord_str[:deg_end])
        minutes_str = coord_str[deg_end+1:deg_end+3]
        seconds_str = coord_str[deg_end+3:]
    else:
        # Longitude: 2-5 digits total (DDDD or DDD)
        deg_end = coord_str.find('E') if 'E' in coord_str else coord_str.find('W')
        degrees = int(coord_str[:deg_end])
        minutes_str = coord_str[deg_end+1:deg_end+3]
        seconds_str = coord_str[deg_end+3:]
    
    minutes = float(minutes_str)
    seconds = float(seconds_str)
    
    decimal = degrees + (minutes / 60) + (seconds / 3600)
    
    if direction in ['S', 'W']:
        decimal = -decimal
    
    return decimal

def get_stations_data():
    """Fetch radio station data from the API"""
    url = "https://opendata.traficom.fi/api/v13/Radioasematiedot"
    response = requests.get(url)
    if response.status_code != 200:
        raise Exception(f"Failed to fetch data: {response.status_code}")
    
    data = response.json()
    return data['value']

def create_map(stations):
    """Create a Folium map with station markers"""
    # Calculate initial center (roughly center of Finland)
    center_lat = 62.0
    center_lon = 15.0
    
    # Create map
    m = folium.Map(location=[center_lat, center_lon], zoom_start=6)

    # Add Esri World Street Map tile layer (works without API key / Referer header)
    folium.TileLayer(
        tiles='https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}',
        attr='Tiles \u00a9 Esri — Esri, DeLorme, NAVTEQ, TomTom, Intermap, iPC, USGS, FAO, NPS, NRCAN, GeoBase, Kadaster NL, Ordnance Survey, Esri Japan, METI, Esri China (Hong Kong), and the GIS User Community',
        name='Esri World Street Map',
        overlay=False,
        control=True
    ).add_to(m)

    # Add marker cluster for better performance with many stations
    marker_cluster = MarkerCluster().add_to(m)
    
    for station in stations:
        try:
            # Parse coordinates
            lat = parse_finnish_coordinate(station['Latitude'])
            lon = parse_finnish_coordinate(station['Longitude'])
            
            # Create popup content
            popup_html = f"""
            <b>{station['StationName']}</b><br>
            {station['Municipality']}<br>
            {station['Frequency'] / 1e6:.1f} MHz<br>
            {station['TransmissionPower']} W<br>
            License: {station['LicenseNumber']}<br>
            Owner: {station['LicenseOwner']}<br>
            Ending Date: {station['EndingDate']}<br>
            """
            
            folium.Marker(
                location=[lat, lon],
                popup=folium.Popup(popup_html, max_width=300),
                tooltip=station['StationName']
            ).add_to(marker_cluster)
            
        except Exception as e:
            print(f"Error processing station {station.get('StationName', 'Unknown')}: {e}")
    
    return m

def main():
    print("Fetching radio station data...")
    stations = get_stations_data()
    print(f"Found {len(stations)} stations")
    
    print("Creating map...")
    m = create_map(stations)
    
    # Save map to HTML file
    output_file = "finland_radio_stations_map.html"
    m.save(output_file)
    print(f"Map saved to {output_file}")
    print("Open this file in a web browser to view the interactive map.")

if __name__ == "__main__":
    main()
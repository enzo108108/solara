import solara
import ipyleaflet
import threading
from scapy.all import traceroute
import ipywidgets as widgets
from geopy.geocoders import Nominatim
from geopy.geocoders import Photon
from geopy.exc import GeocoderTimedOut, GeocoderServiceError
import time
import ipaddress
import requests

# ipyleaflet parameters
center_default = (51.5074, -0.1278)
zoom_default = 5
maps = {
    "OpenStreetMap.Mapnik": ipyleaflet.basemaps.OpenStreetMap.Mapnik,
}

# --- REACTIVE STATE VARIABLES ---
map_zoom = solara.reactive(zoom_default)
map_center = solara.reactive(center_default)
marker_location = solara.reactive(center_default)
map_name = solara.reactive(list(maps)[0])
origin_name = solara.reactive("My Location")
destination_name = solara.reactive("google.com")
status_message = solara.reactive("Ready. Enter a destination and click 'Trace Route'")
locations = solara.reactive([])
is_tracing = solara.reactive(False)
map_layers = solara.reactive([])

# Initialize geolocators with timeout settings
geolocators = [
    Nominatim(user_agent="atracerouteapp", timeout=10),
    Photon(user_agent="atracerouteapp", timeout=10),
]


def is_public_ip(ip):
    """
    Check if the IP address is a public IP (not private or loopback)
    """
    try:
        ip_obj = ipaddress.ip_address(ip)
        return not (ip_obj.is_private or ip_obj.is_loopback)
    except ValueError:
        return False


def get_ip_geolocation(ip):
    """
    Get geolocation data for an IP address using ip-api.com
    """
    try:
        response = requests.get(f"http://ip-api.com/json/{ip}", timeout=10)
        data = response.json()
        if data["status"] == "success":
            return {
                "lat": data["lat"],
                "lon": data["lon"],
                "city": data["city"],
                "country": data["country"],
                "ip": ip,
            }
    except Exception as e:
        print(f"Error getting geolocation for IP {ip}: {e}")
    return None


def get_location_with_retry(geolocator, query, max_retries=3, delay=1):
    """
    Try to get location with retry mechanism
    """
    for i in range(max_retries):
        try:
            return geolocator.geocode(query, exactly_one=True)
        except (GeocoderTimedOut, GeocoderServiceError) as e:
            if i == max_retries - 1:  # Last attempt
                raise
            time.sleep(delay * (i + 1))  # Exponential backoff
    return None


def get_my_location():
    """
    Tries to find the user's current location based on their IP address.
    Falls back to a default location (London, UK) if detection fails.
    """
    # First try to get location from IP geolocation service
    try:
        response = requests.get("http://ip-api.com/json/", timeout=10)
        data = response.json()
        if data["status"] == "success":
            return {
                "lat": data["lat"],
                "lon": data["lon"],
                "city": data["city"],
                "country": data["country"],
                "ip": data["query"],
            }
    except Exception as e:
        print(f"Error getting my location: {e}")

    # Fallback to geolocation services
    for geolocator in geolocators:
        try:
            location = get_location_with_retry(geolocator, "my location")

            if location:
                return {
                    "lat": location.latitude,
                    "lon": location.longitude,
                    "city": location.raw.get("address", {}).get("city", "Unknown"),
                    "country": location.raw.get("address", {}).get(
                        "country", "Unknown"
                    ),
                    "ip": "N/A",
                }
        except Exception as e:
            print(
                f"[!] Could not determine current location with {geolocator.__class__.__name__}: {e}"
            )
            continue

    # Final fallback location
    return {
        "lat": 51.5074,
        "lon": -0.1278,
        "city": "London",
        "country": "GB",
        "ip": "N/A",
    }


def perform_traceroute(target):
    """
    Performs a traceroute to the target using Scapy and updates the locations reactive variable.
    This function is designed to be run in a background thread.
    """
    is_tracing.value = True
    status_message.value = f"Performing traceroute to {target}..."

    try:
        # Perform traceroute using Scapy
        result, unanswered = traceroute(target, maxttl=30, verbose=False)

        hops = []
        for sent, received in result:
            for packet in received:
                if packet.src not in hops and is_public_ip(packet.src):
                    hops.append(packet.src)

    except Exception as e:
        status_message.value = f"An unexpected error occurred: {e}"
        is_tracing.value = False
        return

    if not hops:
        status_message.value = "Traceroute completed but no public hops were found."
        is_tracing.value = False
        return

    status_message.value = "Traceroute complete. Looking up locations..."

    temp_locations = [get_my_location()]

    for ip in hops:
        try:
            # First try to get location from IP geolocation service
            location_data = get_ip_geolocation(ip)
            if location_data:
                if location_data not in temp_locations:
                    temp_locations.append(location_data)
                    print(
                        f"IP: {ip}, City: {location_data['city']}, "
                        f"lat: {location_data['lat']}, lon: {location_data['lon']}, "
                        f"Country: {location_data['country']}"
                    )
                continue

            # If IP geolocation fails, try with geocoding services
            current_geolocator_index = 0
            geolocator = geolocators[current_geolocator_index]

            # Try to get location information for the IP
            location = get_location_with_retry(geolocator, ip)

            if location:
                info = {
                    "lat": location.latitude,
                    "lon": location.longitude,
                    "city": location.raw.get("address", {}).get("city", "Unknown"),
                    "country": location.raw.get("address", {}).get(
                        "country", "Unknown"
                    ),
                    "ip": ip,
                }
                if info not in temp_locations:
                    temp_locations.append(info)
                    print(
                        f"IP: {ip}, City: {location.raw.get('address', {}).get('city', 'Unknown')}, "
                        f"lat: {location.latitude}, lon: {location.longitude}, "
                        f"Country: {location.raw.get('address', {}).get('country', 'Unknown')}"
                    )
        except Exception as e:
            print(f"Error looking up IP {ip}: {e}")
            continue

    locations.value = temp_locations
    status_message.value = "Route traced successfully!"
    is_tracing.value = False


def start_trace():
    if not is_tracing.value:
        locations.value = []
        thread = threading.Thread(
            target=perform_traceroute, args=(destination_name.value,)
        )
        thread.start()


def update_map_layers():
    """
    This function runs whenever 'locations' changes.
    It creates ipyleaflet layers and updates map center/zoom.
    """
    locs = locations.value
    layers = []

    # Add the base map layer
    base_map = ipyleaflet.basemap_to_tiles(maps[map_name.value])
    layers.append(base_map)

    if locs:
        # Update map center and zoom
        map_center.value = [locs[len(locs) // 2]["lat"], locs[len(locs) // 2]["lon"]]
        map_zoom.value = 3

        # Create markers
        for i, loc in enumerate(locs):
            color = "green"
            if i == 0:
                color = "blue"
            elif loc["ip"] == destination_name.value or i == len(locs) - 1:
                color = "orange"

            icon = ipyleaflet.AwesomeIcon(
                name="info-circle", marker_color=color, icon_color="white"
            )
            marker = ipyleaflet.Marker(
                location=(loc["lat"], loc["lon"]), icon=icon, draggable=False
            )

            # Create a popup for the marker using ipywidgets HTML
            html = widgets.HTML(
                value=f"<b>City:</b> {loc['city']}<br><b>IP:</b> {loc['ip']}"
            )
            popup = ipyleaflet.Popup(
                location=(loc["lat"], loc["lon"]),
                child=html,
                close_button=True,
                auto_close=True,
                close_on_escape_key=True,
            )
            marker.popup = popup
            layers.append(marker)

        # Create polyline
        path_coordinates = [(loc["lat"], loc["lon"]) for loc in locs]
        polyline = ipyleaflet.Polyline(
            locations=path_coordinates, color="#3388ff", fill=False, weight=3
        )
        layers.append(polyline)
    else:
        # Reset to default view if no locations
        my_loc = get_my_location()
        map_center.value = [my_loc["lat"], my_loc["lon"]]
        map_zoom.value = 5

    map_layers.value = layers


@solara.component
def Page():
    # This effect hook runs when the locations list changes
    solara.use_effect(update_map_layers, [locations.value])

    # This main column will contain the app bar and the main content row
    # It's styled to take up the full viewport height
    with solara.Column(style={"height": "100vh"}):
        solara.AppBarTitle("Interactive Traceroute Visualizer (Scapy)")

        # Use a Row layout to have the sidebar and map next to each other
        # This row will grow to fill the available space in the main column
        with solara.Row(style={"flex-grow": "1"}):
            with solara.Sidebar():
                with solara.Card("Controls", margin=1):
                    solara.InputText(label="Origin", value=origin_name, disabled=True)
                    solara.InputText(label="Destination", value=destination_name)
                    solara.Button(
                        "Trace Route",
                        on_click=start_trace,
                        disabled=is_tracing.value,
                        style={"margin-top": "10px"},
                    )

                    with solara.Column(gap="10px", style={"margin-top": "20px"}):
                        solara.Text("Status:")
                        if is_tracing.value:
                            solara.ProgressLinear(True)
                        solara.Text(status_message.value)

            # This column will grow to fill the remaining space in the row
            with solara.Column(style={"flex-grow": "1"}):
                ipyleaflet.Map.element(
                    center=map_center.value,
                    zoom=map_zoom.value,
                    layers=map_layers.value,
                    scroll_wheel_zoom=True,
                    layout={"height": "100%"},
                )


# To run this application:
# 1. Make sure you have solara, ipyleaflet, and scapy installed (`pip install solara ipyleaflet scapy`).
# 2. Save the code as a Python file (e.g., `traceroute_map_scapy.py`).
# 3. Run from your terminal: `solara run traceroute_map_scapy.py`

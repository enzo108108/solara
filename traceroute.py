import solara
import ipyleaflet
import threading
from scapy.all import traceroute
import ipywidgets as widgets
from geopy.geocoders import Nominatim, Photon
from geopy.exc import GeocoderTimedOut, GeocoderServiceError
import time
import ipaddress
import requests
import math

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
destination_name = solara.reactive("dt.de")
status_message = solara.reactive("Ready. Enter a destination and click 'Trace Route'")
locations = solara.reactive([])
is_tracing = solara.reactive(False)
map_layers = solara.reactive([])
map_controls = solara.reactive([])

# Initialize geolocators with timeout settings and no Google APIs
geolocators = [
    Nominatim(user_agent="tracerouteapp", timeout=10),
    Photon(user_agent="tracerouteapp", timeout=10),
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
        if data["status"] == "success" and is_public_ip(ip):
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
    Tries to find the user's current location based on their public IP address.
    Falls back to a default location (London, UK) if detection fails.
    """
    # First try to get location from IP geolocation service using public IP
    try:
        # Get public IP address
        public_ip = requests.get("https://api.ipify.org", timeout=10).text
        if is_public_ip(public_ip):
            response = requests.get(f"http://ip-api.com/json/{public_ip}", timeout=20)
            data = response.json()
            if data["status"] == "success":
                return {
                    "lat": data["lat"],
                    "lon": data["lon"],
                    "city": data["city"],
                    "country": data["country"],
                    "ip": public_ip,
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


def calculate_map_bounds(locations):
    """
    Calculate the bounding box for all locations to determine optimal center and zoom
    """
    if not locations:
        return center_default, zoom_default

    # Extract lat/lon coordinates
    lats = [loc["lat"] for loc in locations]
    lons = [loc["lon"] for loc in locations]

    # Calculate bounds
    min_lat, max_lat = min(lats), max(lats)
    min_lon, max_lon = min(lons), max(lons)

    # Calculate center
    center_lat = (min_lat + max_lat) / 2
    center_lon = (min_lon + max_lon) / 2

    # Calculate zoom level based on the bounding box size
    zoom = zoom_default

    # Adjust zoom based on geographic spread
    if max_lat - min_lat > 50 or max_lon - min_lon > 50:
        zoom = 3
    elif max_lat - min_lat > 20 or max_lon - min_lon > 20:
        zoom = 4
    elif max_lat - min_lat > 5 or max_lon - min_lon > 5:
        zoom = 5
    elif max_lat - min_lat > 1 or max_lon - min_lon > 1:
        zoom = 7
    else:
        zoom = 9

    return (center_lat, center_lon), zoom


def perform_traceroute(target):
    """
    Performs a traceroute to the target using Scapy and updates the locations reactive variable.
    This function is designed to be run in a background thread.
    """
    is_tracing.value = True
    status_message.value = f"Performing traceroute to {target} using Scapy..."

    try:
        # Perform traceroute using Scapy
        result, _ = traceroute(target, maxttl=30, timeout=2, verbose=False)

        if not result:
            status_message.value = "Traceroute completed but no hops were found."
            is_tracing.value = False
            return

        hops = []
        for _, hop in result:
            if hop:
                ip = hop.src
                if is_public_ip(ip) and ip not in hops:
                    hops.append(ip)

        if not hops:
            status_message.value = "Traceroute completed but no valid hops were found."
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
                for geolocator in geolocators:
                    try:
                        location = get_location_with_retry(geolocator, ip)
                        if location:
                            info = {
                                "lat": location.latitude,
                                "lon": location.longitude,
                                "city": location.raw.get("address", {}).get(
                                    "city", "Unknown"
                                ),
                                "country": location.raw.get("address", {}).get(
                                    "country", "Unknown"
                                ),
                                "ip": ip,
                            }
                            if info not in temp_locations:
                                temp_locations.append(info)
                            break
                    except Exception as e:
                        print(
                            f"Error looking up IP {ip} with {geolocator.__class__.__name__}: {e}"
                        )
                        continue

            except Exception as e:
                print(f"Error processing IP {ip}: {e}")
                continue

        locations.value = temp_locations
        status_message.value = "Route traced successfully!"
        is_tracing.value = False

    except Exception as e:
        status_message.value = f"An unexpected error occurred: {e}"
        is_tracing.value = False
        return


def start_trace():
    if not is_tracing.value:
        locations.value = []
        thread = threading.Thread(
            target=perform_traceroute, args=(destination_name.value,)
        )
        thread.start()


def update_map_layers():
    """
    Runs whenever 'locations' changes. Builds ipyleaflet layers and the top-right 'Hops' list.
    """
    locs = locations.value
    layers = []
    controls = []

    # Base map
    base_map = ipyleaflet.basemap_to_tiles(maps[map_name.value])
    layers.append(base_map)

    if locs:
        center, zoom = calculate_map_bounds(locs)
        map_center.value = center
        map_zoom.value = zoom

        location_counts = {}
        for loc in locs:
            key = f"{loc['lat']}_{loc['lon']}"
            location_counts[key] = location_counts.get(key, 0) + 1

        location_offsets = {}
        marker_positions = []

        for i, loc in enumerate(locs):
            if i == 0:
                color_hex = "#1e3a8a"  # origin (blue)
            elif loc["ip"] == destination_name.value or i == len(locs) - 1:
                color_hex = "#c2410c"  # destination/last (orange)
            else:
                color_hex = "#15803d"  # intermediate (green)

            # Offset math (fan out overlapping points)
            key = f"{loc['lat']}_{loc['lon']}"
            if key not in location_offsets:
                location_offsets[key] = 0
            offset = location_offsets[key]
            location_offsets[key] += 1

            max_offset = location_counts[key] - 1
            angle = (offset / max_offset) * 2 * math.pi if max_offset > 0 else 0
            offset_distance = 0.005 * (1 + max_offset * 0.1)
            offset_lat = loc["lat"] + offset_distance * math.sin(angle)
            offset_lon = loc["lon"] + offset_distance * math.cos(angle)

            marker_positions.append((offset_lat, offset_lon))

            hop_no = i + 1
            icon_html = f"""
            <div style="
                background:none;
                border:none;
                padding:0;
                margin:0;
            ">
            <div style="
                background:{color_hex};
                width:28px;
                height:28px;
                line-height:28px;
                border-radius:50%;
                text-align:center;
                color:#fff;
                font-weight:700;
                font-family:system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif;
                border:2px solid #fff;
                box-shadow:0 1px 4px rgba(0,0,0,.4);
            ">
                {hop_no}
            </div>
            </div>
            """

            icon = ipyleaflet.DivIcon(
                html=icon_html,
                icon_size=[28, 28],
                icon_anchor=[14, 14],
                class_name="empty", 
            )

            marker = ipyleaflet.Marker(
                location=(offset_lat, offset_lon),
                icon=icon,
                draggable=False,
                title=f"Hop {hop_no}: {loc['city']}, {loc['country']} ({loc['ip']})",
            )

            # Popup with hop no
            popup_content = f"""
            <div style="font-family: Arial, sans-serif; padding: 10px; max-width: 250px;">
                <h3 style="margin-top: 0; color: #333;">Hop {hop_no} — Location Details</h3>
                <div style="margin-bottom: 8px;">
                    <strong style="color: #555;">City:</strong> {loc['city']}
                </div>
                <div style="margin-bottom: 8px;">
                    <strong style="color: #555;">Country:</strong> {loc['country']}
                </div>
                <div style="margin-bottom: 8px;">
                    <strong style="color: #555;">IP Address:</strong> {loc['ip']}
                </div>
                <div style="margin-bottom: 8px;">
                    <strong style="color: #555;">Coordinates:</strong> {loc['lat']}, {loc['lon']}
                </div>
            </div>
            """
            html = widgets.HTML(value=popup_content)
            popup = ipyleaflet.Popup(
                location=(offset_lat, offset_lon),
                child=html,
                close_button=True,
                auto_close=True,
                close_on_escape_key=True,
            )
            marker.popup = popup
            layers.append(marker)

        # Route line
        polyline = ipyleaflet.Polyline(
            locations=marker_positions, color="#3388ff", fill=False, weight=3
        )
        layers.append(polyline)

        # -------- Top-right "Hops" list --------
        hop_widgets = []
        title = widgets.HTML(
            value="""
            <div style="font-weight:600;font-family:system-ui,Arial;
                        padding:6px 8px;border-bottom:1px solid #e5e7eb;">
                Hops
            </div>
            """
        )
        hop_widgets.append(title)

        for i, loc in enumerate(locs):
            hop_no = i + 1
            label = f"{hop_no}. {loc['city']}, {loc['country']} ({loc['ip']})"
            coord_key = f"{loc['lat']}_{loc['lon']}"
            count_here = location_counts.get(coord_key, 1)

            if count_here >= 5:
                target_zoom = 14
            elif count_here >= 3:
                target_zoom = 13
            elif count_here == 2:
                target_zoom = 12
            else:
                target_zoom = 9

            lat, lon = marker_positions[i]

            btn = widgets.Button(
                description=label,
                layout=widgets.Layout(width="280px"),
                tooltip=f"Center on hop {hop_no}",
                button_style="",
                style={"text_align": "left"},
            )
            btn.layout.padding = "0 6px"

            def _make_on_click(lat_, lon_, z_):
                def _on_click(_):
                    map_center.value = (lat_, lon_)
                    map_zoom.value = max(z_, map_zoom.value)
                return _on_click

            btn.on_click(_make_on_click(lat, lon, target_zoom))
            hop_widgets.append(btn)

        list_box = widgets.VBox(
            children=hop_widgets,
            layout=widgets.Layout(
                border="1px solid #e5e7eb",
                box_shadow="0 2px 8px rgba(0,0,0,0.08)",
                background_color="white",
                width="300px",
                max_height="320px",
                overflow_y="auto",
            ),
        )

        hops_control = ipyleaflet.WidgetControl(widget=list_box, position="topright")
        
        # --- CSS injection to remove the box behind markers
        css = widgets.HTML(
            value="""
            <style>
            /* kill the white square & border Leaflet adds to DivIcon */
            .leaflet-div-icon {
                background: transparent !important;
                border: none !important;
                box-shadow: none !important;
            }
            </style>
            """
        )
        
        controls.append(ipyleaflet.WidgetControl(widget=css, position="topleft"))
        controls.append(hops_control)

    else:
        my_loc = get_my_location()
        map_center.value = [my_loc["lat"], my_loc["lon"]]
        map_zoom.value = 5

    map_layers.value = layers
    map_controls.value = controls


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

            with solara.Column(style={"flex-grow": "1"}):
                ipyleaflet.Map.element(
                    center=map_center.value,
                    zoom=map_zoom.value,
                    layers=map_layers.value,
                    controls=map_controls.value,
                    scroll_wheel_zoom=True,
                    layout={"height": "100%"},
                )
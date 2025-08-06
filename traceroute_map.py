import solara
import ipyleaflet
import geoip2.database
import subprocess
import os
import threading
import geocoder
import ipywidgets as widgets

# --- CONFIGURATION ---
# IMPORTANT: You must download the GeoLite2 City database from MaxMind
# 1. Go to https://www.maxmind.com/en/geolite2/signup
# 2. Sign up and download the "GeoLite2 City" database in .mmdb format.
# 3. Place the .mmdb file in the same directory as this script.
GEOIP_DATABASE_PATH = "GeoLite2-City.mmdb"

# ipyleaflet parameters
center_default = (51.5074, -0.1278)
zoom_default = 5
maps = {
    "OpenStreetMap.Mapnik": ipyleaflet.basemaps.OpenStreetMap.Mapnik,
    # "OpenTopoMap": ipyleaflet.basemaps.OpenTopoMap,
    # "Esri.WorldTopoMap": ipyleaflet.basemaps.Esri.WorldTopoMap,
}

# --- REACTIVE STATE VARIABLES ---
# These variables hold the state of our application and will trigger UI updates when they change.
map_zoom = solara.reactive(zoom_default)
map_center = solara.reactive(center_default)
marker_location = solara.reactive(center_default)

map_name = solara.reactive(list(maps)[0])
origin_name = solara.reactive("My Location")
destination_name = solara.reactive("google.com")
status_message = solara.reactive("Ready. Enter a destination and click 'Trace Route'")
locations = solara.reactive([])
is_tracing = solara.reactive(False)
# We will now store ipyleaflet layers here, and create the map in the component
map_layers = solara.reactive([])


def get_my_location():
    """
    Tries to find the user's current location based on their IP address.
    Falls back to a default location (London, UK) if detection fails.
    """
    try:
        g = geocoder.ip("me")
        if g.ok:
            return {
                "lat": g.latlng[0],
                "lon": g.latlng[1],
                "city": g.city,
                "country": g.country,
                "ip": g.ip,
            }
    except Exception as e:
        print(f"[!] Could not determine current location: {e}")
    # Fallback location
    return {
        "lat": 51.5074,
        "lon": -0.1278,
        "city": "London",
        "country": "GB",
        "ip": "N/A",
    }


def perform_traceroute(target):
    """
    Performs a traceroute to the target and updates the locations reactive variable.
    This function is designed to be run in a background thread.
    """
    is_tracing.value = True
    status_message.value = f"Performing traceroute to {target}..."

    hops = []
    try:
        if os.name == "nt":
            # Use system 'tracert' on Windows for better reliability
            process = subprocess.Popen(
                ["tracert", target],
                stdout=subprocess.PIPE,
                universal_newlines=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            for line in process.stdout:
                if "Trace complete" in line:
                    break
                parts = line.strip().split()
                if len(parts) > 1 and parts[-1].count(".") == 3:
                    hops.append(parts[-1].strip("[]"))
        else:
            # Use system 'traceroute' on Linux/macOS
            status_message.value = (
                f"Performing traceroute to {target} (this may take a moment)..."
            )
            process = subprocess.Popen(
                ["traceroute", target],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
            )
            stdout, stderr = process.communicate(timeout=60)

            if process.returncode != 0:
                status_message.value = f"Traceroute failed. Error: {stderr.strip()}"
                is_tracing.value = False
                return

            for line in stdout.splitlines():
                parts = line.strip().split()
                if not parts or not parts[0].isdigit():
                    continue
                ip_found = None
                for part in parts:
                    cleaned_part = part.strip("()")
                    if cleaned_part.count(".") == 3:
                        try:
                            ip_parts = cleaned_part.split(".")
                            if all(0 <= int(p) <= 255 for p in ip_parts):
                                ip_found = cleaned_part
                                break
                        except ValueError:
                            continue
                if ip_found:
                    hops.append(ip_found)

    except FileNotFoundError:
        status_message.value = "Error: 'traceroute' command not found. Please install it or check your system's PATH."
        is_tracing.value = False
        return
    except subprocess.TimeoutExpired:
        status_message.value = "Traceroute timed out."
        is_tracing.value = False
        return
    except Exception as e:
        status_message.value = f"An unexpected error occurred: {e}"
        is_tracing.value = False
        return

    if not hops:
        status_message.value = "Traceroute completed but no hops were found."
        is_tracing.value = False
        return

    status_message.value = "Traceroute complete. Looking up locations..."

    temp_locations = [get_my_location()]

    try:
        with geoip2.database.Reader(GEOIP_DATABASE_PATH) as reader:
            for ip in hops:
                try:
                    response = reader.city(ip)
                    if response.location.latitude and response.location.longitude:
                        info = {
                            "lat": response.location.latitude,
                            "lon": response.location.longitude,
                            "city": response.city.name,
                            "country": response.country.name,
                            "ip": ip,
                        }
                        if info not in temp_locations:
                            temp_locations.append(info)
                except geoip2.errors.AddressNotFoundError:
                    pass
    except FileNotFoundError:
        status_message.value = (
            f"Error: GeoIP database not found at '{GEOIP_DATABASE_PATH}'"
        )
        is_tracing.value = False
        return
    except Exception as e:
        status_message.value = f"Error reading GeoIP database: {e}"
        is_tracing.value = False
        return

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

            for lo in loc:
                print(lo)
            print("-" * 10)
            #
            # Createa popup for the marker using ipywidgets HTML
            html = widgets.HTML(value=f"<b>City:</b> {loc['city']}")
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
        solara.AppBarTitle("Interactive Traceroute Visualizer")

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
# 1. Make sure you have solara and ipyleaflet installed (`pip install solara ipyleaflet`).
# 2. Save the code as a Python file (e.g., `traceroute_app.py`).
# 3. Run from your terminal: `solara run traceroute_app.py`

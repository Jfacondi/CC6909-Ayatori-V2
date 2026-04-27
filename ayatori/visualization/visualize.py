"""
Visualizacion de viajes y rutas sobre mapas Folium.
"""

import folium
from typing import Optional

# Paleta de colores para rutas de transito
_ROUTE_COLORS = [
    "#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4",
    "#42d4f4", "#f032e6", "#bfef45", "#fabed4", "#469990",
    "#dcbeff", "#9a6324", "#fffac8", "#800000", "#aaffc3",
    "#808000", "#ffd8b1", "#000075", "#a9a9a9",
]


def visualize_journey(journey, gtfs_data=None, output_path: str = None) -> folium.Map:
    """
    Renderiza un viaje completo en un mapa Folium interactivo.

    Cada tipo de segmento se dibuja con estilo distinto:
      - walk   : linea discontinua gris
      - transit: linea solida de color unico por ruta
      - transfer: marcador naranja en la parada de transbordo

    Args:
        journey: Objeto Journey (de JourneyPlannerV2 o CSA).
        gtfs_data: Instancia de GTFSData para resolver coordenadas de paradas.
                   Opcional si el viaje ya contiene coordenadas.
        output_path: Si se indica, guarda el mapa como HTML en esa ruta.

    Returns:
        folium.Map con el viaje dibujado.
    """
    # Detectar si es Journey del CSA (tiene .segments) o de JourneyPlannerV2 (tiene .legs)
    is_csa = hasattr(journey, "segments") and not hasattr(journey, "legs")
    legs = journey.segments if is_csa else journey.legs

    # Centro inicial: primera coordenada util que encontremos
    center = [-33.45, -70.65]  # Santiago por defecto

    m = folium.Map(location=center, zoom_start=13, tiles="CartoDB positron")

    color_map: dict = {}
    color_idx = 0

    def _stop_latlon(stop_id: str):
        if not gtfs_data or not stop_id:
            return None
        coords = gtfs_data.get_stop_coords(stop_id)
        if coords:
            lon, lat = coords
            return [lat, lon]
        return None

    first_coord = None

    for leg in legs:
        # --- Normalizar campos segun tipo de objeto ---
        if is_csa:
            seg_type = leg.get("type")
            if seg_type == "walk":
                from_label = leg.get("from", "")
                to_label = leg.get("to", "")
                dist_km = leg.get("distance_km", 0)
                # Las caminatas no tienen coordenadas directas; usamos paradas si hay
                from_coords = _stop_latlon(from_label) if from_label not in ("origin", "destination") else None
                to_coords = _stop_latlon(to_label) if to_label not in ("origin", "destination") else None

            elif seg_type == "transit":
                route_id = leg.get("route_id", "?")
                from_stop = leg.get("from_stop")
                to_stop = leg.get("to_stop")
                from_coords = _stop_latlon(from_stop)
                to_coords = _stop_latlon(to_stop)

            elif seg_type == "transfer":
                at_stop = leg.get("at_stop")
                from_route = leg.get("from_route", "")
                to_route = leg.get("to_route", "")
                coords = _stop_latlon(at_stop)

        else:  # JourneyPlannerV2 Journey
            seg_type = leg.leg_type
            if seg_type == "walk":
                dist_km = getattr(leg, "distance", 0)
                from_coords = None
                to_coords = None

            elif seg_type == "transit":
                route_id = leg.route_id or "?"
                from_stop = leg.from_stop
                to_stop = leg.to_stop
                from_coords = _stop_latlon(from_stop)
                to_coords = _stop_latlon(to_stop)

            elif seg_type == "transfer":
                from_route = leg.transfer_from or ""
                to_route = leg.transfer_to or ""
                coords = _stop_latlon(from_stop if hasattr(leg, "from_stop") else None)

        # --- Dibujar segun tipo ---
        if seg_type == "transit" and from_coords and to_coords:
            if route_id not in color_map:
                color_map[route_id] = _ROUTE_COLORS[color_idx % len(_ROUTE_COLORS)]
                color_idx += 1
            color = color_map[route_id]

            folium.PolyLine(
                locations=[from_coords, to_coords],
                color=color,
                weight=5,
                opacity=0.9,
                tooltip=f"Ruta {route_id}",
            ).add_to(m)

            # Marcadores de parada (pequeños circulos)
            for coord, label in [(from_coords, from_stop), (to_coords, to_stop)]:
                folium.CircleMarker(
                    location=coord,
                    radius=4,
                    color=color,
                    fill=True,
                    fill_opacity=0.8,
                    tooltip=label,
                ).add_to(m)

            if first_coord is None:
                first_coord = from_coords

        elif seg_type == "walk":
            if from_coords and to_coords:
                folium.PolyLine(
                    locations=[from_coords, to_coords],
                    color="#888888",
                    weight=3,
                    dash_array="8 4",
                    opacity=0.7,
                    tooltip=f"Caminata {dist_km*1000:.0f} m",
                ).add_to(m)

        elif seg_type == "transfer":
            if "coords" in dir() and coords:
                folium.Marker(
                    location=coords,
                    icon=folium.Icon(color="orange", icon="exchange", prefix="fa"),
                    tooltip=f"Transbordo: {from_route} -> {to_route}",
                ).add_to(m)

    # Marcadores de origen y destino
    origin_coords = None
    dest_coords = None

    if is_csa and legs:
        # Primer segmento walk -> desde "origin"; ultimo walk -> hacia "destination"
        first_transit = next((s for s in legs if s.get("type") == "transit"), None)
        last_transit = next((s for s in reversed(legs) if s.get("type") == "transit"), None)
        if first_transit:
            origin_coords = _stop_latlon(first_transit.get("from_stop"))
        if last_transit:
            dest_coords = _stop_latlon(last_transit.get("to_stop"))
    else:
        if hasattr(journey, "origin_coords"):
            lat, lon = journey.origin_coords
            origin_coords = [lat, lon]
        if hasattr(journey, "destination_coords"):
            lat, lon = journey.destination_coords
            dest_coords = [lat, lon]

    if origin_coords:
        folium.Marker(
            location=origin_coords,
            icon=folium.Icon(color="green", icon="play", prefix="fa"),
            tooltip="Origen",
        ).add_to(m)
        m.location = origin_coords

    if dest_coords:
        folium.Marker(
            location=dest_coords,
            icon=folium.Icon(color="red", icon="flag", prefix="fa"),
            tooltip="Destino",
        ).add_to(m)

    # Leyenda de rutas usadas
    if color_map:
        legend_html = "<div style='position:fixed;bottom:30px;left:30px;z-index:1000;" \
                      "background:white;padding:10px;border:1px solid #ccc;border-radius:6px;" \
                      "font-family:sans-serif;font-size:13px;'>"
        legend_html += "<b>Rutas</b><br>"
        for rid, col in color_map.items():
            legend_html += (
                f"<span style='display:inline-block;width:14px;height:14px;"
                f"background:{col};border-radius:3px;margin-right:5px;'></span>"
                f"{rid}<br>"
            )
        legend_html += "</div>"
        m.get_root().html.add_child(folium.Element(legend_html))

    if output_path:
        m.save(output_path)

    return m


def visualize_routes(route_list: list, gtfs_data, stops: bool = True,
                     orientation: str = "round",
                     output_path: str = None) -> folium.Map:
    """
    Dibuja una o varias rutas GTFS en un mapa Folium.

    Args:
        route_list: Lista de route_id a dibujar.
        gtfs_data: Instancia de GTFSData.
        stops: Si True, agrega marcadores en cada parada.
        orientation: "round" o "return" para filtrar direccion del recorrido.
        output_path: Ruta HTML opcional para guardar el mapa.

    Returns:
        folium.Map con las rutas dibujadas.
    """
    m = folium.Map(location=[-33.45, -70.65], zoom_start=12, tiles="CartoDB positron")

    for i, route_id in enumerate(route_list):
        route_stops = gtfs_data.route_stops.get(route_id, {})
        color = _ROUTE_COLORS[i % len(_ROUTE_COLORS)]

        trip_stops = [
            info for info in route_stops.values()
            if info.get("orientation") == orientation and info.get("coordinates")
        ]
        trip_stops.sort(key=lambda x: x["sequence"])

        if not trip_stops:
            continue

        locations = [[info["coordinates"][1], info["coordinates"][0]] for info in trip_stops]

        folium.PolyLine(
            locations=locations,
            color=color,
            weight=4,
            opacity=0.85,
            tooltip=f"Ruta {route_id}",
        ).add_to(m)

        if stops:
            for info in trip_stops:
                lat, lon = info["coordinates"][1], info["coordinates"][0]
                folium.CircleMarker(
                    location=[lat, lon],
                    radius=4,
                    color=color,
                    fill=True,
                    fill_opacity=0.7,
                    tooltip=info["stop_id"],
                ).add_to(m)

    if output_path:
        m.save(output_path)

    return m


def visualize_stops(gtfs_data, coords, radius_km: float = 0.5,
                    output_path: str = None) -> folium.Map:
    """
    Dibuja las paradas cercanas a unas coordenadas dadas.

    Args:
        gtfs_data: Instancia de GTFSData.
        coords: Tupla (lat, lon) del centro de busqueda.
        radius_km: Radio de busqueda en km.
        output_path: Ruta HTML opcional para guardar el mapa.

    Returns:
        folium.Map con las paradas marcadas.
    """
    lat, lon = coords
    m = folium.Map(location=[lat, lon], zoom_start=15, tiles="CartoDB positron")

    folium.Marker(
        location=[lat, lon],
        icon=folium.Icon(color="blue", icon="crosshairs", prefix="fa"),
        tooltip="Centro de busqueda",
    ).add_to(m)

    nearby = gtfs_data.get_nearby_stops(coords, margin_km=radius_km, max_stops=50)
    for stop_id, distance in nearby:
        stop_coords = gtfs_data.get_stop_coords(stop_id)
        if not stop_coords:
            continue
        s_lon, s_lat = stop_coords
        routes = gtfs_data.get_routes_at_stop(stop_id)
        folium.CircleMarker(
            location=[s_lat, s_lon],
            radius=6,
            color="#3388ff",
            fill=True,
            fill_opacity=0.8,
            tooltip=f"{stop_id} ({distance*1000:.0f} m) — rutas: {', '.join(routes[:5])}",
        ).add_to(m)

    if output_path:
        m.save(output_path)

    return m

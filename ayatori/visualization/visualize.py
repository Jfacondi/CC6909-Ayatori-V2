"""
Visualizacion de viajes y rutas sobre mapas Folium.
"""

import folium
import rustworkx as rx


def _get_walking_street_path(from_latlon, to_latlon, osm_graph) -> list | None:
    """
    Devuelve la lista de [lat, lon] siguiendo calles reales (OSM + dijkstra).
    Retorna None si no hay grafo OSM o si el ruteo falla.
    """
    if osm_graph is None:
        return None
    try:
        lat1, lon1 = from_latlon[0], from_latlon[1]
        lat2, lon2 = to_latlon[0], to_latlon[1]

        src_node = osm_graph.find_nearest_node(lat1, lon1)
        dst_node = osm_graph.find_nearest_node(lat2, lon2)
        if src_node is None or dst_node is None:
            return None

        src_idx = osm_graph._node_id_to_idx.get(src_node)
        dst_idx = osm_graph._node_id_to_idx.get(dst_node)
        if src_idx is None or dst_idx is None:
            return None

        paths = rx.dijkstra_shortest_paths(
            osm_graph.graph,
            src_idx,
            target=dst_idx,
            weight_fn=lambda e: float(e.get("length", e.get("weight", 1.0))),
        )
        if dst_idx not in paths:
            return None

        coords = []
        for idx in paths[dst_idx]:
            node_id = osm_graph._idx_to_node_id.get(idx)
            if node_id and node_id in osm_graph.node_coords:
                lat, lon = osm_graph.node_coords[node_id]
                coords.append([lat, lon])
        return coords if len(coords) >= 2 else None
    except Exception:
        return None


# Paleta de colores para rutas de transito
_ROUTE_COLORS = [
    "#e6194b",
    "#3cb44b",
    "#4363d8",
    "#f58231",
    "#911eb4",
    "#42d4f4",
    "#f032e6",
    "#bfef45",
    "#fabed4",
    "#469990",
    "#dcbeff",
    "#9a6324",
    "#fffac8",
    "#800000",
    "#aaffc3",
    "#808000",
    "#ffd8b1",
    "#000075",
    "#a9a9a9",
]

# Colores de caminata por opcion de viaje (para distinguirlas visualmente)
_WALK_COLORS = [
    "#555555",
    "#1a6bb5",
    "#b55a1a",
    "#1ab55a",
    "#b51a6b",
]


def _stop_latlon(stop_id: str, gtfs_data) -> list | None:
    if not gtfs_data or not stop_id:
        return None
    coords = gtfs_data.get_stop_coords(stop_id)
    if coords:
        lon, lat = coords
        return [lat, lon]
    return None


def _merge_transit_legs(raw_legs: list) -> list:
    """
    Para journeys del CSA: une saltos consecutivos de tránsito en la misma ruta
    en un único segmento con la lista completa de paradas.
    Devuelve la lista de legs procesada.
    """
    legs: list = []
    i = 0
    while i < len(raw_legs):
        leg = raw_legs[i]
        if leg.get("type") == "transit":
            route_id = leg.get("route_id")
            stop_ids: list = [leg.get("from_stop"), leg.get("to_stop")]
            j = i + 1
            while (
                j < len(raw_legs)
                and raw_legs[j].get("type") == "transit"
                and raw_legs[j].get("route_id") == route_id
            ):
                stop_ids.append(raw_legs[j].get("to_stop"))
                j += 1
            # Deduplicar manteniendo orden
            seen: set = set()
            unique_stops: list = []
            for s in stop_ids:
                if s and s not in seen:
                    seen.add(s)
                    unique_stops.append(s)
            legs.append(
                {
                    "type": "transit",
                    "route_id": route_id,
                    "stop_ids": unique_stops,
                    "departure_time": leg.get("departure_time"),
                    "arrival_time": raw_legs[j - 1].get("arrival_time"),
                }
            )
            i = j
        else:
            legs.append(leg)
            i += 1
    return legs


def _draw_journey_into(
    journey, container, gtfs_data, color_map: dict, walk_color: str = "#555555", osm_graph=None
):
    """
    Dibuja los segmentos de un journey en un contenedor Folium (Map o FeatureGroup).
    Muta color_map agregando route_id → color asignado.
    Si se provee osm_graph, los tramos de caminata siguen las calles reales.

    Returns:
        (origin_coords, dest_coords): listas [lat, lon] o None.
    """
    is_csa = hasattr(journey, "segments") and not hasattr(journey, "legs")
    raw_legs = journey.segments if is_csa else journey.legs

    def _sll(stop_id):
        return _stop_latlon(stop_id, gtfs_data)

    legs = _merge_transit_legs(raw_legs) if is_csa else list(raw_legs)

    for leg in legs:
        seg_type = leg.get("type") if is_csa else leg.leg_type

        # ---- Caminata ----
        if seg_type == "walk":
            if is_csa:
                from_label = leg.get("from", "")
                to_label = leg.get("to", "")
                dist_km = leg.get("distance_km", 0)
                from_coords = leg.get("from_latlon") if from_label == "origin" else _sll(from_label)
                to_coords = leg.get("to_latlon") if to_label == "destination" else _sll(to_label)
            else:
                dist_km = getattr(leg, "distance", 0) or getattr(leg, "walking_distance", 0) or 0
                from_coords = None
                to_coords = None

            if from_coords and to_coords:
                walk_min = (dist_km / 5.0) * 60 if dist_km > 0 else 0
                tooltip_walk = f"🚶 Caminata  {dist_km * 1000:.0f} m" + (
                    f" · ~{walk_min:.0f} min" if walk_min > 0 else ""
                )

                # Intentar ruta por calles reales
                street_path = _get_walking_street_path(from_coords, to_coords, osm_graph)
                walk_locations = street_path if street_path else [from_coords, to_coords]

                # Línea de fondo blanca (hace la caminata visible sobre cualquier fondo)
                folium.PolyLine(
                    locations=walk_locations,
                    color="#ffffff",
                    weight=9,
                    opacity=0.6,
                ).add_to(container)

                # Línea punteada de caminata
                folium.PolyLine(
                    locations=walk_locations,
                    color=walk_color,
                    weight=5,
                    dash_array="12 7",
                    opacity=1.0,
                    tooltip=tooltip_walk,
                ).add_to(container)

                # Marcadores de inicio y fin del tramo
                for coord in (from_coords, to_coords):
                    folium.CircleMarker(
                        location=coord,
                        radius=5,
                        color=walk_color,
                        fill=True,
                        fill_color="#ffffff",
                        fill_opacity=1.0,
                        weight=2,
                    ).add_to(container)

                # Ícono de persona caminando en el punto medio
                mid = walk_locations[len(walk_locations) // 2]
                folium.Marker(
                    location=mid,
                    icon=folium.DivIcon(
                        html=(
                            f'<div style="'
                            f"font-size:16px;text-align:center;"
                            f"background:rgba(255,255,255,0.92);"
                            f"border:2px solid {walk_color};"
                            f"border-radius:50%;width:28px;height:28px;"
                            f"line-height:24px;box-shadow:0 1px 4px rgba(0,0,0,0.3);"
                            f'">🚶</div>'
                        ),
                        icon_size=(28, 28),
                        icon_anchor=(14, 14),
                    ),
                    tooltip=tooltip_walk,
                ).add_to(container)

        # ---- Tránsito ----
        elif seg_type == "transit":
            if is_csa:
                route_id = leg.get("route_id", "?")
                stop_ids = leg.get("stop_ids") or []
                locations = [c for c in (_sll(s) for s in stop_ids) if c]
                stop_labels = stop_ids
            else:
                route_id = leg.route_id or "?"
                fc = _sll(leg.from_stop)
                tc = _sll(leg.to_stop)
                locations = [c for c in [fc, tc] if c]
                stop_labels = [leg.from_stop, leg.to_stop]

            if route_id not in color_map:
                color_map[route_id] = _ROUTE_COLORS[len(color_map) % len(_ROUTE_COLORS)]
            color = color_map[route_id]

            if len(locations) >= 2:
                folium.PolyLine(
                    locations=locations,
                    color=color,
                    weight=5,
                    opacity=0.9,
                    tooltip=f"Ruta {route_id}",
                ).add_to(container)

            for stop_id, coord in zip(stop_labels, [_sll(s) for s in stop_labels]):
                if coord:
                    folium.CircleMarker(
                        location=coord,
                        radius=4,
                        color=color,
                        fill=True,
                        fill_opacity=0.8,
                        tooltip=str(stop_id),
                    ).add_to(container)

        # ---- Transbordo ----
        elif seg_type == "transfer":
            if is_csa:
                coords = _sll(leg.get("at_stop"))
                from_route = leg.get("from_route", "")
                to_route = leg.get("to_route", "")
            else:
                coords = _sll(getattr(leg, "from_stop", None))
                from_route = getattr(leg, "transfer_from", "") or ""
                to_route = getattr(leg, "transfer_to", "") or ""

            if coords:
                folium.Marker(
                    location=coords,
                    icon=folium.Icon(color="orange", icon="exchange", prefix="fa"),
                    tooltip=f"Transbordo: {from_route} -> {to_route}",
                ).add_to(container)

    # Calcular coords de origen y destino para los marcadores
    if is_csa:
        raw = journey.segments
        first_walk = next(
            (s for s in raw if s.get("type") == "walk" and s.get("from") == "origin"), None
        )
        last_walk = next(
            (s for s in reversed(raw) if s.get("type") == "walk" and s.get("to") == "destination"),
            None,
        )
        origin_coords = (first_walk.get("from_latlon") if first_walk else None) or (
            _sll(next((s.get("from_stop") for s in raw if s.get("type") == "transit"), None))
        )
        dest_coords = (last_walk.get("to_latlon") if last_walk else None) or (
            _sll(
                next((s.get("to_stop") for s in reversed(raw) if s.get("type") == "transit"), None)
            )
        )
    else:
        origin_coords = list(journey.origin_coords) if hasattr(journey, "origin_coords") else None
        dest_coords = (
            list(journey.destination_coords) if hasattr(journey, "destination_coords") else None
        )

    return origin_coords, dest_coords


def visualize_journey(
    journey, gtfs_data=None, output_path: str = None, osm_graph=None
) -> folium.Map:
    """
    Renderiza un viaje completo en un mapa Folium interactivo.

    Args:
        journey: Objeto Journey (de JourneyPlannerV2 o CSA).
        gtfs_data: Instancia de GTFSData para resolver coordenadas de paradas.
        output_path: Si se indica, guarda el mapa como HTML en esa ruta.
        osm_graph: Instancia de OSMGraph para trazar caminatas por calles reales.

    Returns:
        folium.Map con el viaje dibujado.
    """
    m = folium.Map(location=[-33.45, -70.65], zoom_start=13, tiles="CartoDB positron")
    color_map: dict = {}

    origin_coords, dest_coords = _draw_journey_into(
        journey, m, gtfs_data, color_map, osm_graph=osm_graph
    )

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

    _add_route_legend(m, color_map)

    if output_path:
        m.save(output_path)
    return m


def visualize_journeys(
    journeys: list, gtfs_data=None, output_path: str = None, osm_graph=None
) -> folium.Map:
    """
    Renderiza multiples opciones de viaje en un mapa Folium con capas alternables.

    Cada opcion aparece como una capa en el control de capas (esquina superior derecha).
    La primera opcion se muestra por defecto; las demas se pueden activar para comparar.

    Args:
        journeys: Lista de objetos Journey (de JourneyPlannerV2 o CSA).
        gtfs_data: Instancia de GTFSData para resolver coordenadas de paradas.
        output_path: Si se indica, guarda el mapa como HTML en esa ruta.
        osm_graph: Instancia de OSMGraph para trazar caminatas por calles reales.

    Returns:
        folium.Map con todas las opciones dibujadas.
    """
    if not journeys:
        return folium.Map(location=[-33.45, -70.65], zoom_start=13, tiles="CartoDB positron")

    m = folium.Map(location=[-33.45, -70.65], zoom_start=13, tiles="CartoDB positron")

    # color_map compartido: misma ruta = mismo color en todas las opciones
    color_map: dict = {}
    map_center = None

    for idx, journey in enumerate(journeys):
        dur_min = journey.total_duration.total_seconds() / 60
        transfers = journey.number_of_transfers
        walk_m = journey.total_walking_distance * 1000
        transfer_label = "transbordo" if transfers == 1 else "transbordos"
        has_transit = any(
            (s.get("type") if isinstance(s, dict) else getattr(s, "leg_type", None)) == "transit"
            for s in (journey.segments if hasattr(journey, "segments") else journey.legs)
        )
        if not has_transit:
            layer_name = f"Opción {idx + 1}: 🚶 Solo a pie · {dur_min:.0f} min · {walk_m:.0f} m"
        else:
            layer_name = (
                f"Opción {idx + 1}: {dur_min:.0f} min · "
                f"{transfers} {transfer_label} · "
                f"{walk_m:.0f} m caminata"
            )

        fg = folium.FeatureGroup(name=layer_name, show=(idx == 0))
        walk_color = _WALK_COLORS[idx % len(_WALK_COLORS)]

        origin_coords, dest_coords = _draw_journey_into(
            journey,
            fg,
            gtfs_data,
            color_map,
            walk_color=walk_color,
            osm_graph=osm_graph,
        )

        if origin_coords:
            folium.Marker(
                location=origin_coords,
                icon=folium.Icon(color="green", icon="play", prefix="fa"),
                tooltip="Origen",
            ).add_to(fg)
            if map_center is None:
                map_center = origin_coords

        if dest_coords:
            folium.Marker(
                location=dest_coords,
                icon=folium.Icon(color="red", icon="flag", prefix="fa"),
                tooltip="Destino",
            ).add_to(fg)

        fg.add_to(m)

    if map_center:
        m.location = map_center

    folium.LayerControl(collapsed=False).add_to(m)
    _add_route_legend(m, color_map)
    _add_walk_legend(m)

    if output_path:
        m.save(output_path)
    return m


def _add_walk_legend(m: folium.Map):
    """Agrega una nota visual explicando el estilo de caminata."""
    html = (
        "<div style='position:fixed;bottom:30px;right:30px;z-index:1000;"
        "background:white;padding:8px 12px;border:1px solid #ccc;border-radius:6px;"
        "font-family:sans-serif;font-size:12px;'>"
        "<span style='display:inline-block;width:30px;border-top:3px dashed #555;vertical-align:middle;margin-right:6px;'></span>"
        "🚶 Tramo a pie"
        "<br>"
        "<span style='display:inline-block;width:30px;border-top:4px solid #e6194b;vertical-align:middle;margin-right:6px;'></span>"
        "🚌 Tránsito"
        "</div>"
    )
    m.get_root().html.add_child(folium.Element(html))


def _add_route_legend(m: folium.Map, color_map: dict):
    if not color_map:
        return
    html = (
        "<div style='position:fixed;bottom:30px;left:30px;z-index:1000;"
        "background:white;padding:10px;border:1px solid #ccc;border-radius:6px;"
        "font-family:sans-serif;font-size:13px;'>"
        "<b>Rutas</b><br>"
    )
    for rid, col in color_map.items():
        html += (
            f"<span style='display:inline-block;width:14px;height:14px;"
            f"background:{col};border-radius:3px;margin-right:5px;'></span>"
            f"{rid}<br>"
        )
    html += "</div>"
    m.get_root().html.add_child(folium.Element(html))


def visualize_routes(
    route_list: list,
    gtfs_data,
    stops: bool = True,
    orientation: str = "round",
    output_path: str = None,
) -> folium.Map:
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
            info
            for info in route_stops.values()
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


def visualize_stops(
    gtfs_data, coords, radius_km: float = 0.5, output_path: str = None
) -> folium.Map:
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
            tooltip=f"{stop_id} ({distance * 1000:.0f} m) — rutas: {', '.join(routes[:5])}",
        ).add_to(m)

    if output_path:
        m.save(output_path)
    return m

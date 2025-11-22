from datetime import datetime, date, time, timedelta
from collections import defaultdict
import folium


def find_route_nodes(osm_graph, gtfs_data, route_id, desired_orientation):
    # Checks the desired orientation validity
    if desired_orientation != "round" and desired_orientation != "return":
        # Invalid orientation
        return

    # Get the stops for the specified route
    stops = gtfs_data.route_stops.get(route_id, {})

    # Filter the stops that are visited on the desired orientation
    trip_stops = [
        stop_info for stop_info in stops.values() if stop_info["orientation"] == desired_orientation
    ]

    # Using the find_nearest_node method, finds the (nearest) nodes for each stop of the route
    route_nodes = []
    for stop_info in trip_stops:
        stop_coords = stop_info["coordinates"]
        route_node = osm_graph.find_nearest_node(stop_coords[1], stop_coords[0])
        route_nodes.append(route_node)

    return route_nodes


def find_nearest_stops(osm_graph, gtfs_data, address, margin):
    """
    Given an address and a margin, returns a list of the nearest stop IDs and their orientations.

    Parameters:
    address (str): The address to search around.
    margin (float): The maximum distance (in kilometers) from the given address to include stops in the result.

    Returns:
    tuple: A tuple of two lists. The first list contains the stop IDs that are within the specified margin of the given address.
    The second list contains tuples of stop IDs and their orientations.
    """
    graph = osm_graph.graph

    v = osm_graph.address_locator(str(address))
    v_lon = graph.vertex_properties["lon"][v]
    v_lat = graph.vertex_properties["lat"][v]
    v_coords = (v_lon, v_lat)
    nearest_stops, orientations = gtfs_data.get_near_stop_ids(v_coords, margin)
    return nearest_stops, orientations


def available_route_finder(
    osm_graph,
    gtfs_data,
    source_node_id,
    target_node_id,
    departure_time,
    departure_date,
    margin,
    geolocator,
):
    graph = osm_graph.graph
    route_stops = gtfs_data.route_stops

    selected_path_nodes = [source_node_id, target_node_id]
    selected_path = []
    for node in selected_path_nodes:
        # Getting the coordinates
        lat, lon = graph.vertex_properties["lat"][node], graph.vertex_properties["lon"][node]
        selected_path.append((lat, lon))

    # Coordinates
    source_lat = selected_path[0][0]
    source_lon = selected_path[0][1]
    target_lat = selected_path[-1][0]
    target_lon = selected_path[-1][1]

    # Reversing the geocoding to get the full info on the addresses
    source = geolocator.reverse((source_lat, source_lon))
    target = geolocator.reverse((target_lat, target_lon))

    # Add markers for the nearest stop from the source and target points
    near_source_stops, source_orientations = find_nearest_stops(
        osm_graph, gtfs_data, source, margin
    )
    near_target_stops, target_orientations = find_nearest_stops(
        osm_graph, gtfs_data, target, margin
    )

    fixed_orientation = None
    valid_services = set()
    for source_stop_id in near_source_stops:
        for target_stop_id in near_target_stops:
            # Finding services that connects a stop near the source with one near the target
            services = gtfs_data.connection_finder(source_stop_id, target_stop_id)
            for service in services:
                # Getting the orientations on which the services visits the stops
                source_orientation = gtfs_data.get_bus_orientation(service, source_stop_id)
                target_orientation = gtfs_data.get_bus_orientation(service, target_stop_id)
                # Getting the sequence number (the ordinal value for the visited stop)
                source_sequence = int(gtfs_data.get_trip_sequence(service, source_stop_id))
                target_sequence = int(gtfs_data.get_trip_sequence(service, target_stop_id))
                if source_sequence > target_sequence:
                    # Travel not valid
                    continue
                if isinstance(source_orientation, list) and isinstance(target_orientation, list):
                    # If both source and target orientations are lists, check if any of the values match
                    valid_orientation = any(
                        x in target_orientation for x in source_orientation
                    ) or any(x in source_orientation for x in target_orientation)
                    if valid_orientation and service not in valid_services:
                        valid_services.add(service)
                        fixed_orientation = (
                            [x for x in source_orientation if x in target_orientation][0]
                            if [x for x in source_orientation if x in target_orientation]
                            else source_orientation[0]
                        )
                elif (
                    source_orientation == target_orientation and service not in valid_services
                ):  # Check if both stops are visited in the same orientation
                    valid_services.add(service)
                    fixed_orientation = target_orientation
                elif (
                    isinstance(source_orientation, list)
                    and target_orientation in source_orientation
                    and service not in valid_services
                ):
                    valid_services.add(service)
                    fixed_orientation = target_orientation
                elif (
                    isinstance(target_orientation, list)
                    and source_orientation in target_orientation
                    and service not in valid_services
                ):
                    valid_services.add(service)
                    fixed_orientation = source_orientation

    if len(valid_services) == 0:
        print("Error: There are no available services right now to go to the desired destination.")
        print("Possible reasons: no routes that have stops near the source and target addresses.")
        print("You can try changing the search margin and try again.")
        return

    # Checking flags for time and date
    nighttime_flag = gtfs_data.is_nighttime(departure_time)
    rush_hour_flag = gtfs_data.is_rush_hour(departure_time)
    holiday_flag = gtfs_data.is_holiday(departure_date)

    if holiday_flag:
        # Rush hours modifications (like express routes) doesn't work on holidays
        rush_hour_flag = 0

    # Nighttime check
    daily_time_services = gtfs_data.check_night_routes(valid_services, nighttime_flag)

    if daily_time_services is None:
        print("Error: There are no available services right now to go to the desired destination.")
        print("Possible reasons: Source hour is during nighttime.")
        print("Please take into account that nighttime goes between 00:00:00 and 05:30:00.")
        return

    # Rush hour check
    valid_services = gtfs_data.check_express_routes(daily_time_services, rush_hour_flag)
    # Sorting the valid services
    valid_services = list(set(valid_services))

    # Filters the stops to get only the valid ones
    valid_source_stops = [
        stop_id
        for stop_id in near_source_stops
        if any(
            route_id in valid_services
            for route_id in route_stops.keys()
            if stop_id in route_stops[route_id]
        )
    ]
    valid_source_stops = list(set(valid_source_stops))
    valid_target_stops = [
        stop_id
        for stop_id in near_target_stops
        if any(
            route_id in valid_services
            for route_id in route_stops.keys()
            if stop_id in route_stops[route_id]
        )
    ]
    valid_target_stops = list(set(valid_target_stops))

    info = [
        selected_path,
        source,
        target,
        valid_source_stops,
        valid_target_stops,
        valid_services,
        fixed_orientation,
        near_source_stops,
        near_target_stops,
    ]

    return info


def find_best_option(
    osm_graph,
    gtfs_data,
    selected_path,
    departure_time,
    departure_date,
    valid_source_stops,
    valid_target_stops,
    valid_services,
    fixed_orientation,
):

    graph = osm_graph.graph
    route_stops = gtfs_data.route_stops

    best_option = None
    best_option_times = None
    initial_source_time = timedelta(
        hours=departure_time.hour, minutes=departure_time.minute, seconds=departure_time.second
    )
    source_time = None

    # Set of valid orientations defined by the source
    best_option_orientation = None

    # Checks for valid target stops
    valid_target = []
    for target_stop in valid_target_stops:
        target_routes = gtfs_data.get_routes_at_stop(target_stop)
        valid_target.extend(target_routes)
    valid_target = list(dict.fromkeys(valid_target))

    waiting_time = None
    initial_delta_time = None

    for stop_id in valid_source_stops:
        # Gets the services that visits the stop and filters the valid ones (source)
        routes_at_stop = gtfs_data.get_routes_at_stop(stop_id)
        valid_stop_services = [stop_id for stop_id in valid_services if stop_id in routes_at_stop]
        for valid_service in valid_stop_services:
            # Gets the arrival times and service orientation for this valid service
            arrival_info = gtfs_data.get_arrival_times(valid_service, stop_id, departure_date)
            if arrival_info is not None and arrival_info[0] == fixed_orientation:
                orientation = arrival_info[0]
                flag = False  # A flag to correct the orientation
                for target_stop_id in valid_target_stops:
                    flag = False
                    # Gets the services that visits the stop and filters the valid ones (target)
                    target_stop_routes = gtfs_data.get_routes_at_stop(target_stop_id)
                    if valid_service in target_stop_routes:
                        target_orientation = gtfs_data.get_bus_orientation(
                            valid_service, target_stop_id
                        )
                        if orientation not in target_orientation:
                            flag = True
                            continue

                if flag:
                    continue

                if valid_service not in valid_target:
                    continue

                arrival_times = arrival_info[1]

                # Gets the coordinates for the stop and the source location
                stop_coords = gtfs_data.get_stop_coords(stop_id)

                # Base Coordinates
                source_lat = selected_path[0][0]
                source_lon = selected_path[0][1]
                target_lat = selected_path[-1][0]
                target_lon = selected_path[-1][1]

                location_coords = (source_lon, source_lat)

                # Consider the travel time between the source location and the stop
                # The average walking speed is between 4 km/h and 6.5 km/h, so we consider it as 5 km/h
                initial_walking_time = gtfs_data.walking_travel_time(
                    stop_coords, location_coords, 5
                )
                this_delta_time = timedelta(seconds=initial_walking_time)

                initial_time = (
                    (datetime.combine(date.today(), departure_time) + this_delta_time)
                    .time()
                    .strftime("%H:%M:%S")
                )
                initial_time = datetime.strptime(initial_time, "%H:%M:%S").time()
                source_time = timedelta(
                    hours=initial_time.hour,
                    minutes=initial_time.minute,
                    seconds=initial_time.second,
                )

                # Getting the times of the next buses arrival to the stop
                time_until_next_buses = gtfs_data.get_time_until_next_bus(
                    arrival_times, initial_time, departure_date
                )

                if not time_until_next_buses:
                    print(
                        "Error: There are no available services right now to go to the desired destination."
                    )
                    print(
                        "Possible reasons: There are no buses left today. Maybe the source hour is too close to the ending time for the service."
                    )
                    return

                # Print the time until the next three buses in the desired format
                for i in range(len(time_until_next_buses)):
                    minutes, seconds = time_until_next_buses[i]
                    waiting_time = timedelta(minutes=minutes, seconds=seconds)
                    arrival_time = source_time + waiting_time
                    time_string = gtfs_data.timedelta_to_hhmm(arrival_time)

                    target_orientation = gtfs_data.get_bus_orientation(
                        valid_service, target_stop_id
                    )

                    # Update the best option
                    if (
                        best_option is None or (arrival_time < best_option[2])
                    ) and orientation == fixed_orientation:
                        best_option = (valid_service, stop_id, arrival_time, waiting_time)
                        best_option_times = time_until_next_buses
                        best_option_orientation = orientation
                        if initial_delta_time is None or this_delta_time < initial_delta_time:
                            initial_delta_time = this_delta_time

    best_option_info = [
        best_option,
        initial_delta_time,
        best_option_times,
        initial_source_time,
        valid_target,
        best_option_orientation,
    ]
    return best_option_info


def calculate_stop_density(osm_graph, gtfs_data):
    # Get the stops and their coordinates from the OSMGraph
    stops = osm_graph.get_nodes_by_tag("public_transport", "stop_position")
    stop_coords = {stop: osm_graph.get_node_coordinates(stop) for stop in stops}

    # Get the number of routes that stop at each stop
    stop_routes = defaultdict(set)
    for route_id, graph in gtfs_data.graphs.items():
        for e in graph.edges():
            u = graph.edge_properties["u"][e]
            v = graph.edge_properties["v"][e]
            stop_routes[u].add(route_id)
            stop_routes[v].add(route_id)

    # Create a map with markers for each stop, colored by the number of routes that stop there
    m = folium.Map(location=[-33.45, -70.66], zoom_start=12)
    for stop, coord in stop_coords.items():
        num_routes = len(stop_routes[stop])
        color = (
            "red"
            if num_routes >= 10
            else (
                "orange"
                if num_routes >= 5
                else (
                    "yellow"
                    if num_routes >= 3
                    else "green" if num_routes >= 2 else "blue" if num_routes >= 1 else "purple"
                )
            )
        )
        folium.Marker(location=coord, icon=folium.Icon(color=color)).add_to(m)

    return m

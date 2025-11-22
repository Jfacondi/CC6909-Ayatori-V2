from geopy.geocoders import Nominatim
from datetime import datetime, date, time, timedelta
import time as tm
import pandas as pd
import folium
import OSMGraph
import GTFSData
from utils import find_nearest_stops, find_route_nodes, available_route_finder, find_best_option


osm_graph = OSMGraph()
gtfs_data = GTFSData()


# Define the function to set the optimal zoom level for the map
def fit_bounds(points, m):
    """
    Fits the map bounds to a given set of points.

    Parameters:
    points (list): A list of points in the format [(lat1, lon1), (lat2, lon2), ...].
    m (folium.Map): A folium map object.
    """
    df = pd.DataFrame(points).rename(columns={0: "Lat", 1: "Lon"})[["Lat", "Lon"]]
    sw = df[["Lat", "Lon"]].min().values.tolist()
    ne = df[["Lat", "Lon"]].max().values.tolist()
    m.fit_bounds([sw, ne])


# Lite implementation of the Connection Scan Algorithm
def connection_scan_lite(source_address, target_address, departure_time, departure_date, margin):
    """
    The Connection Scan Algorithm is applied to search for travel routes from the source to the destination,
    given a departure time and date. By default, the algorithm uses the current date and time of the system.
    However, you can specify a different date or time if needed. The margin value let's the user determine
    the range on which a stop is considered as "near" to the source or target addresses.
    Note: this is a "lite" version of CSA that maps possible routes without doing any transfers.

    Parameters:
    source_address (string): the source address of the travel.
    target_address (string): the destination address of the travel.
    departure_time (time): the time at which the travel should start.
    departure_date (date): the date on which the travel should be done.
    margin (float): margin of distance between the nodes and the valid stops.

    Returns:
    folium.Map: the map of the best travel route. It returns None if no routes are found.
    """
    # Getting the nodes corresponding to the addresses
    source_node = osm_graph.address_locator(source_address)
    target_node = osm_graph.address_locator(target_address)

    # Instance of the route_stops dictionary
    route_stops = gtfs_data.route_stops

    if source_node is not None and target_node is not None:
        # Convert source and target node IDs to integers
        source_node_graph_id = osm_graph.graph.vertex_properties["graph_id"][source_node]
        target_node_graph_id = osm_graph.graph.vertex_properties["graph_id"][target_node]

        print("Both addresses have been found.")
        print("Processing...")

        geolocator = Nominatim(user_agent="ayatori")

        route_info = available_route_finder(
            osm_graph,
            gtfs_data,
            source_node_graph_id,
            target_node_graph_id,
            departure_time,
            departure_date,
            margin,
            geolocator,
        )

        selected_path = route_info[0]
        source = route_info[1]
        target = route_info[2]
        valid_source_stops = route_info[3]
        valid_target_stops = route_info[4]
        valid_services = route_info[5]
        fixed_orientation = route_info[6]
        near_source_stops = route_info[7]
        near_target_stops = route_info[8]

        # Create a map that shows the correct public transport services to take from the source to the target
        m = folium.Map(location=[selected_path[0][0], selected_path[0][1]], zoom_start=13)

        # Add markers for the source and target points
        folium.Marker(
            location=[selected_path[0][0], selected_path[0][1]],
            popup="Origen: {}".format(source),
            icon=folium.Icon(color="green"),
        ).add_to(m)
        folium.Marker(
            location=[selected_path[-1][0], selected_path[-1][1]],
            popup="Destino: {}".format(target),
            icon=folium.Icon(color="red"),
        ).add_to(m)

        print("")
        print("Routes have been found.")
        print("Calculating the best route and getting the arrival times for the next buses...")

        best_option_info = find_best_option(
            osm_graph,
            gtfs_data,
            selected_path,
            departure_time,
            departure_date,
            valid_source_stops,
            valid_target_stops,
            valid_services,
            fixed_orientation,
        )

        best_option = best_option_info[0]
        initial_delta_time = best_option_info[1]
        best_option_times = best_option_info[2]
        initial_source_time = best_option_info[3]
        valid_target = best_option_info[4]
        best_option_orientation = best_option_info[5]

        if best_option is None:
            print(
                "Error: There are no available services right now to go to the desired destination."
            )
            print(
                "Possible reasons: the valid routes are not available at the specified date or starting time."
            )
            print(
                "Please take into account that some routes have trips only during or after nighttime, which goes between 00:00:00 and 05:30:00"
            )
            return

        arrival_time = None

        source_stop = best_option[1]

        # Parse Metro stations's names
        metro_stations_dict = gtfs_data.parse_metro_stations("stops.txt")
        possible_metro_name = gtfs_data.is_metro_station(best_option[1], metro_stations_dict)
        if possible_metro_name is not None:
            source_stop = possible_metro_name

        walking_minutes, walking_seconds = gtfs_data.timedelta_separator(initial_delta_time)

        print("")
        print("To go from: {}".format(source))
        print("To: {}".format(target))
        best_arrival_time_str = gtfs_data.timedelta_to_hhmm(best_option[2])
        print("")
        if possible_metro_name is not None:  # Changes the printing to adapt for the use of Metro
            print(
                "The best option is to walk for {} minutes and {} seconds to {} Metro station, and take the line {}.".format(
                    walking_minutes, walking_seconds, source_stop, best_option[0]
                )
            )
            print("The next train arrives at {}.".format(best_arrival_time_str))
            print("The other two next trains arrives in:")
        else:
            print(
                "The best option is to walk for {} minutes and {} seconds to stop {}, and take the route {}.".format(
                    walking_minutes, walking_seconds, source_stop, best_option[0]
                )
            )
            print("The next bus arrives at {}.".format(best_arrival_time_str))
            print("The other two next buses arrives in:")

        # Format and prints the times
        for i in range(len(best_option_times)):
            if i == 0:
                continue
            minutes, seconds = best_option_times[i]
            waiting_time = timedelta(minutes=minutes, seconds=seconds)
            arrival_time = initial_source_time + waiting_time
            time_string = gtfs_data.timedelta_to_hhmm(arrival_time)
            print(f"{minutes} minutes, {seconds} seconds ({time_string})")

        # Base Coordinates
        source_lat = selected_path[0][0]
        source_lon = selected_path[0][1]
        target_lat = selected_path[-1][0]
        target_lon = selected_path[-1][1]

        for stop_id in near_source_stops:
            if stop_id in valid_source_stops:
                # Filters the data for selecting the best source option for its mapping
                stop_coords = gtfs_data.get_stop_coords(str(stop_id))
                routes_at_stop = gtfs_data.get_routes_at_stop(stop_id)
                valid_stop_services = [
                    stop_id for stop_id in valid_services if stop_id in routes_at_stop
                ]

                for service in valid_stop_services:
                    if service == best_option[0] and stop_id == best_option[1]:
                        # Maps the best option to take the best option's service
                        folium.Marker(
                            location=[stop_coords[1], stop_coords[0]],
                            popup="Mejor opción: subirse al recorrido {} en la parada {}.".format(
                                best_option[0], best_option[1]
                            ),
                            icon=folium.Icon(color="cadetblue", icon="plus"),
                        ).add_to(m)
                        initial_distance = [
                            (selected_path[0][0], selected_path[0][1]),
                            (stop_coords[1], stop_coords[0]),
                        ]
                        folium.PolyLine(initial_distance, color="black", dash_array="10").add_to(m)

        for stop_id in near_target_stops:
            if stop_id in valid_target_stops:
                # Filters the data for the possible target stops
                stop_coords = gtfs_data.get_stop_coords(str(stop_id))
                routes_at_stop = gtfs_data.get_routes_at_stop(stop_id)
                valid_stop_services = [
                    stop_id for stop_id in valid_services if stop_id in routes_at_stop
                ]

        target_orientation = None
        for service in valid_target:
            if service == best_option[0]:
                # Generates the trip id to get the approximated travel time
                if fixed_orientation == "round":
                    trip_id = service + "-I-" + gtfs_data.get_trip_day_suffix(departure_date)
                else:
                    trip_id = service + "-R-" + gtfs_data.get_trip_day_suffix(departure_date)

                best_travel_time = None
                selected_stop = None
                for stop_id in valid_target_stops:
                    # Calculates the travel time while taking the service
                    bus_time = gtfs_data.get_travel_time(trip_id, [best_option[1], stop_id])
                    target_stop_routes = gtfs_data.get_routes_at_stop(stop_id)
                    target_orientation = gtfs_data.get_bus_orientation(best_option[0], stop_id)
                    if (
                        service in target_stop_routes
                        and bus_time > timedelta()
                        and (best_travel_time is None or bus_time < best_travel_time)
                    ):
                        # Checking the correct orientation
                        if fixed_orientation in target_orientation:
                            # Updates the selected target stop and travel time
                            best_travel_time = bus_time
                            selected_stop = stop_id

                # Gets the coordinates for the target stop
                selected_stop_coords = gtfs_data.get_stop_coords(selected_stop)
                # Separates the best travel time for the printing
                minutes, seconds = gtfs_data.timedelta_separator(best_travel_time)

                # Gets the sequence number for the source and target stops
                seq_1 = route_stops[best_option[0]][best_option[1]]["sequence"]
                seq_2 = route_stops[best_option[0]][selected_stop]["sequence"]

                # Store the coordinates of the visited stops for their mapping
                visited_stops = []

                # Iterate over the stops of the selected route
                for stop_id, stop_info in route_stops[best_option[0]].items():
                    # Check if the stop sequence number is between seq_1 and seq_2
                    seq_number = stop_info["sequence"]
                    this_orientation = gtfs_data.get_bus_orientation(best_option[0], stop_id)
                    if best_option_orientation in this_orientation and seq_1 <= seq_number <= seq_2:
                        # Append the coordinates of the stop to the visited_stops list
                        lat = stop_info["coordinates"][0]
                        lon = stop_info["coordinates"][1]
                        visited_stops.append((seq_number, (lon, lat)))

                # Sorts the visited stops and gets their coordinates
                visited_stops_sorted = sorted(visited_stops, key=lambda x: x[0])
                visited_stops_sorted_coords = [x[1] for x in visited_stops_sorted]

                # Checks if the stop is a Metro Station (they are stored as a number)
                possible_metro_target_name = gtfs_data.is_metro_station(
                    selected_stop, metro_stations_dict
                )

                if possible_metro_target_name is not None:
                    selected_stop = possible_metro_target_name

                print("")
                if possible_metro_name is not None:  # Changes the message
                    print(
                        "You will get off the train on {} station after {} minutes and {} seconds.".format(
                            selected_stop, minutes, seconds
                        )
                    )
                else:
                    print(
                        "You will get off the bus on stop {} after {} minutes and {} seconds.".format(
                            selected_stop, minutes, seconds
                        )
                    )

                # Maps the best option to get off the best option's service
                folium.Marker(
                    location=[selected_stop_coords[1], selected_stop_coords[0]],
                    popup="Mejor opción: bajarse del recorrido {} en la parada {}.".format(
                        best_option[0], selected_stop
                    ),
                    icon=folium.Icon(color="cadetblue", icon="plus"),
                ).add_to(m)
                ending_distance = [
                    (selected_path[-1][0], selected_path[-1][1]),
                    (selected_stop_coords[1], selected_stop_coords[0]),
                ]
                folium.PolyLine(ending_distance, color="black", dash_array="10").add_to(m)

                # Create a polyline connecting the visited stops
                folium.PolyLine(visited_stops_sorted_coords, color="red").add_to(m)

                # Gets the coordinates for the target stop and target location
                final_stop_coords = (selected_stop_coords[1], selected_stop_coords[0])
                final_location_coords = (target_lat, target_lon)

                # Calculates the walking time between the target stop and location
                end_walking_time = gtfs_data.walking_travel_time(
                    final_stop_coords, final_location_coords, 5
                )
                end_delta_time = timedelta(seconds=end_walking_time)
                end_walk_min, end_walk_sec = gtfs_data.timedelta_separator(end_delta_time)

                # Time walking to stop + waiting the bus + riding the bus + walking to target destination
                total_time = initial_delta_time + best_option[3] + best_travel_time + end_delta_time
                minutes, seconds = gtfs_data.timedelta_separator(total_time)

                # Parses the time for the printing
                destination_time = initial_source_time + total_time
                time_string = gtfs_data.timedelta_to_hhmm(destination_time)
                print(
                    f"After that, you need to walk for {end_walk_min} minutes and {end_walk_sec} seconds to arrive at the target spot."
                )
                print(
                    f"Total travel time: {minutes} minutes, {seconds} seconds. You will arrive your destination at {time_string}."
                )

        # Set the optimal zoom level for the map
        fit_bounds(selected_path, m)

        return m
    else:
        # Empty return
        return


def algorithm_commands():
    """
    Process the inputs given by the user to run the Connection Scan Algorithm.
    """

    # System's date and time
    now = datetime.now()

    # Date formatting
    today = date.today()
    today_format = today.strftime("%d/%m/%Y")

    # Time formatting
    moment = now.strftime("%H:%M:%S")
    used_time = datetime.strptime(moment, "%H:%M:%S").time()

    # User inputs
    # Date and time
    source_date = (
        input("Enter the travel's date, in DD/MM/YYY format (press Enter to use today's date) : ")
        or today_format
    )
    print(source_date)
    source_hour = (
        input("Enter the travel's start time, in HH:MM:SS format (press Enter to start now) : ")
        or used_time
    )
    if source_hour != used_time:
        source_hour = datetime.strptime(source_hour, "%H:%M:%S").time()
    print(source_hour)

    # Source address
    source_example = "Beauchef 850, Santiago"
    while True:
        source_address = (
            input(
                "Enter the starting point's address, in 'Street #No, Province' format (Ex: 'Beauchef 850, Santiago'):"
            )
            or source_example
        )
        if source_address.strip() != "":
            break

    # Destination address
    destination_example = "Campus Antumapu Universidad de Chile, Santiago"
    while True:
        target_address = (
            input(
                "Enter the ending point's address, in 'Street #No, Province' format (Ex: 'Campus Antumapu Universidad de Chile, Santiago'):"
            )
            or destination_example
        )
        if target_address.strip() != "":
            break

    # You can change the final number (the margin) as you please. Bigger numbers increase the range for near stops
    # But bigger numbers imply bigger execution times
    best_route_map = connection_scan_lite(
        source_address, target_address, source_hour, source_date, 0.2
    )

    if not best_route_map:
        print("")
        print("Something went wrong. Please try again later.")
        return

    # Displays the results and return
    return best_route_map


algorithm_commands()

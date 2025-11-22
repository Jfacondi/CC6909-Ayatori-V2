import geopandas as gpd
import matplotlib.pyplot as plt  # Es necesario importar el plot para que se vea
from pyrosm import get_data, OSM
from pyrosm.data import sources
import networkx as nx
import osmnx as ox
from datetime import datetime, date, time


# COMANDOS #
# cd "Desktop/Universidad/15° Semestre (Otoño 2023)/CC6909-Trabajo de Título/CC6909-Ayatori/Dataset/OSM"
# cd Dataset/OSM
# python.exe pbf_processor.py

# print(sources.south_america.available)

fp = get_data(
    "Santiago",
    update=True,
    directory="C:/Users/felip/Desktop/Universidad/15° Semestre (Otoño 2023)/CC6909-Trabajo de Título/CC6909-Ayatori",
)

print("Filepath: ", fp)

osm = OSM(fp)

# RUTA MÁS CORTA #
nodes, edges = osm.get_network(nodes=True)
G = osm.to_graph(nodes, edges, graph_type="networkx")

# EJEMPLOS
# source_address = "Beauchef 850, Santiago" # Campus Beauchef de la Universidad de Chile
# source_address = "Av. Dorsal 1913, Conchali" # Plaza Bicentenario de Conchalí
# target_address = "Beauchef 850, Santiago" # Campus Beauchef de la Universidad de Chile
# target_address = "Pío Nono 1, Providencia" # Facultad de Derecho
# target_address = "Av. Sta. Rosa 11315, La Pintana" # Campus Sur de la Universidad de Chile

# Fecha y hora
now = datetime.now()
dt_string = now.strftime("%d/%m/%Y %H:%M:%S")
print("Fecha y hora actuales =", dt_string)

today = date.today()
today_format = today.strftime("%d/%m/%Y")

moment = now.strftime("%H:%M:%S")

# Inputs de usuario
# Fecha y hora
source_date = (
    input(
        "Ingresa la fecha del viaje en formato DD/MM/YYY (presiona Enter para usar la fecha actual) : "
    )
    or today_format
)
print(source_date)
source_hour = (
    input(
        "Ingresa la hora del viaje en formato HH:MM:SS (presiona Enter para usar la hora actual) : "
    )
    or moment
)
print(source_hour)

# Dirección de inicio
while True:
    source_address = input("Ingresa dirección de inicio (Ejemplo: 'Beauchef 850, Santiago'): ")
    if source_address.strip() != "":
        print("Dirección de Inicio ingresada: " + source_address)
        break

# Dirección de destino
while True:
    target_address = input("Ingresa dirección de destino (Ejemplo: 'Beauchef 850, Santiago'): ")
    if target_address.strip() != "":
        print("Dirección de Destino ingresada: " + target_address)
        break

print("Preparando ruta, por favor espere...")

source = ox.geocode(str(source_address))
target = ox.geocode(str(target_address))

source_node = ox.nearest_nodes(G, source[1], source[0])
target_node = ox.nearest_nodes(G, target[1], target[0])

route = nx.shortest_path(G, source_node, target_node, weight="length")
fig, ax = ox.plot_graph_route(G, route, route_linewidth=6, node_size=0, bgcolor="k")


# GRÁFICOS GENERALES #

# Driving
# nodes, edges = osm.get_network(nodes=True, network_type="driving")
# plt.title('Caminos disponibles en Santiago para viajar: en auto.')

# Cycling
# nodes, edges = osm.get_network(nodes=True, network_type="cycling")
# plt.title('Caminos disponibles en Santiago para viajar: en bicicleta.')

# Walking
# nodes, edges = osm.get_network(nodes=True,network_type="walking")

# ax = edges.plot()
# nodes.plot(ax = ax, color = 'orange', markersize = 2)

# plt.title('Caminos disponibles en Santiago para viajar: caminando.')
# plt.xlabel('Latitud')
# plt.ylabel('Longitud')
# plt.show()

from geopy.geocoders import Nominatim

geolocator = Nominatim(user_agent="my-application")

location = geolocator.geocode("1600 Amphitheatre Parkway, Mountain View, CA")

print((location.latitude, location.longitude))

"""
Script para calcular transferencias de todas las rutas del sistema
Procesa las 427 rutas de Santiago y genera la matriz completa de transferencias
"""

import sys
import os
import time
from datetime import datetime

# Agregar el directorio raíz al path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from ayatori.models import GTFSData, TransferManager

def format_time(seconds):
    """Formatea segundos en formato legible"""
    if seconds < 60:
        return f"{seconds:.1f}s"
    elif seconds < 3600:
        mins = seconds / 60
        return f"{mins:.1f}min"
    else:
        hours = seconds / 3600
        return f"{hours:.2f}h"

def main():
    print("╔══════════════════════════════════════════════════════════════════════════════╗")
    print("║                                                                              ║")
    print("║              CÁLCULO COMPLETO DE TRANSFERENCIAS - SANTIAGO                   ║")
    print("║                                                                              ║")
    print("╚══════════════════════════════════════════════════════════════════════════════╝\n")
    
    # Paso 1: Cargar GTFS
    print("📦 Paso 1/4: Cargando datos GTFS...")
    gtfs_path = "ayatori/data/GTFS/2023-09-16/GTFS-V100-PO20230916.zip"
    
    if not os.path.exists(gtfs_path):
        print(f"❌ Error: No se encontró {gtfs_path}")
        print("   Por favor, asegúrate de que el archivo GTFS existe en la ruta correcta.")
        return 1
    
    start_time = time.time()
    
    try:
        gtfs = GTFSData(gtfs_path)
        load_time = time.time() - start_time
        
        num_routes = len(gtfs.route_stops)
        num_stops = len(gtfs.stop_coords)
        
        print(f"   ✅ GTFS cargado en {format_time(load_time)}")
        print(f"      - Rutas: {num_routes}")
        print(f"      - Paradas: {num_stops}")
        print()
        
    except Exception as e:
        print(f"   ❌ Error cargando GTFS: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    # Paso 2: Calcular transferencias
    print("🔄 Paso 2/4: Calculando transferencias entre todas las rutas...")
    print(f"   Configuración:")
    print(f"      - Distancia máxima: 500m")
    print(f"      - Tiempo máximo de espera: 15 minutos")
    print(f"      - Velocidad de caminata: 5 km/h")
    print()
    
    calc_start = time.time()
    
    try:
        # Calcular todas las transferencias
        transfer_manager = gtfs.compute_all_transfers(
            max_distance_km=0.5,
            max_waiting_minutes=15,
            walking_speed_kmh=5.0
        )
        
        calc_time = time.time() - calc_start
        
        print(f"\n   ✅ Cálculo completado en {format_time(calc_time)}")
        print()
        
    except Exception as e:
        print(f"\n   ❌ Error calculando transferencias: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    # Paso 3: Analizar resultados
    print("📊 Paso 3/4: Analizando resultados...")
    
    try:
        stats = transfer_manager.get_statistics()
        
        print(f"   Transferencias totales: {stats['total_transfers']:,}")
        print(f"   Transferencias viables: {stats['viable_transfers']:,}")
        print(f"   Tasa de viabilidad: {stats['viability_rate']:.1f}%")
        print()
        
        # Análisis por tipo
        types = {}
        for transfer_list in transfer_manager.transfers.values():
            for transfer in transfer_list:
                t_type = transfer.transfer_type
                types[t_type] = types.get(t_type, 0) + 1
        
        print("   Distribución por tipo:")
        for t_type, count in sorted(types.items()):
            percentage = (count / stats['total_transfers']) * 100
            print(f"      - {t_type:12s}: {count:6,} ({percentage:5.1f}%)")
        print()
        
        # Análisis de distancias
        distances = []
        for transfer_list in transfer_manager.transfers.values():
            for transfer in transfer_list:
                if transfer.is_viable():
                    distances.append(transfer.walking_distance_km * 1000)  # a metros
        
        if distances:
            avg_dist = sum(distances) / len(distances)
            min_dist = min(distances)
            max_dist = max(distances)
            
            print("   Distancias de caminata (transferencias viables):")
            print(f"      - Promedio: {avg_dist:.1f}m")
            print(f"      - Mínima: {min_dist:.1f}m")
            print(f"      - Máxima: {max_dist:.1f}m")
        print()
        
    except Exception as e:
        print(f"   ⚠️  Error analizando resultados: {e}")
        import traceback
        traceback.print_exc()
    
    # Paso 4: Guardar resultados
    print("💾 Paso 4/4: Guardando resultados...")
    
    try:
        # Almacenar en el objeto GTFS para uso futuro
        gtfs.transfer_manager = transfer_manager
        
        # Generar reporte
        report_path = "transfer_report.txt"
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write("═" * 80 + "\n")
            f.write("REPORTE DE TRANSFERENCIAS - SISTEMA DE TRANSPORTE SANTIAGO\n")
            f.write("═" * 80 + "\n\n")
            
            f.write(f"Fecha de generación: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Archivo GTFS: {gtfs_path}\n\n")
            
            f.write("RESUMEN GENERAL\n")
            f.write("─" * 80 + "\n")
            f.write(f"Rutas procesadas: {num_routes}\n")
            f.write(f"Paradas totales: {num_stops}\n")
            f.write(f"Transferencias encontradas: {stats['total_transfers']:,}\n")
            f.write(f"Transferencias viables: {stats['viable_transfers']:,}\n")
            f.write(f"Tasa de viabilidad: {stats['viability_rate']:.2f}%\n\n")
            
            f.write("TIEMPOS DE PROCESAMIENTO\n")
            f.write("─" * 80 + "\n")
            f.write(f"Carga de GTFS: {format_time(load_time)}\n")
            f.write(f"Cálculo de transferencias: {format_time(calc_time)}\n")
            f.write(f"Tiempo total: {format_time(load_time + calc_time)}\n\n")
            
            f.write("DISTRIBUCIÓN POR TIPO\n")
            f.write("─" * 80 + "\n")
            for t_type, count in sorted(types.items()):
                percentage = (count / stats['total_transfers']) * 100
                f.write(f"{t_type:15s}: {count:8,} ({percentage:6.2f}%)\n")
            
            if distances:
                f.write("\nESTADÍSTICAS DE DISTANCIA (metros)\n")
                f.write("─" * 80 + "\n")
                f.write(f"Promedio: {avg_dist:8.1f}m\n")
                f.write(f"Mínima:   {min_dist:8.1f}m\n")
                f.write(f"Máxima:   {max_dist:8.1f}m\n")
        
        print(f"   ✅ Reporte guardado en: {report_path}")
        print()
        
    except Exception as e:
        print(f"   ⚠️  Error guardando resultados: {e}")
        import traceback
        traceback.print_exc()
    
    # Resumen final
    total_time = time.time() - start_time
    
    print("╔══════════════════════════════════════════════════════════════════════════════╗")
    print("║                                                                              ║")
    print("║                          ✅ PROCESO COMPLETADO ✅                            ║")
    print("║                                                                              ║")
    print("╚══════════════════════════════════════════════════════════════════════════════╝\n")
    
    print(f"📊 Resumen:")
    print(f"   • {num_routes} rutas procesadas")
    print(f"   • {stats['total_transfers']:,} transferencias encontradas")
    print(f"   • {stats['viable_transfers']:,} transferencias viables ({stats['viability_rate']:.1f}%)")
    print(f"   • Tiempo total: {format_time(total_time)}")
    print(f"   • Promedio: {total_time/num_routes:.3f}s por ruta")
    print()
    
    print("✅ Los datos de transferencias están ahora disponibles en el objeto GTFSData")
    print("   para su uso en planificación de viajes con el Connection Scan Algorithm.")
    print()
    
    return 0

if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)

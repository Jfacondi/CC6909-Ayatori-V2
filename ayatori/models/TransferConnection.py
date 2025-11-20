#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Transfer Connection - Sistema de Transbordos

Este módulo maneja las conexiones de transbordo entre diferentes rutas
de transporte público.
"""

from typing import Optional, Dict, Any
from dataclasses import dataclass


@dataclass
class TransferConnection:
    """
    Representa una conexión de transbordo entre dos paradas de diferentes rutas.
    
    Attributes:
        from_route_id: ID de la ruta de origen
        to_route_id: ID de la ruta de destino
        from_stop_id: ID de la parada de origen
        to_stop_id: ID de la parada de destino
        walking_distance_km: Distancia de caminata entre paradas (km)
        walking_time_seconds: Tiempo de caminata entre paradas (segundos)
        min_transfer_time: Tiempo mínimo de transferencia (incluye espera)
        max_waiting_time: Tiempo máximo de espera recomendado (segundos)
        transfer_type: Tipo de transbordo ('same_stop', 'nearby', 'walking')
    """
    
    from_route_id: str
    to_route_id: str
    from_stop_id: str
    to_stop_id: str
    walking_distance_km: float
    walking_time_seconds: float
    min_transfer_time: int = 120  # 2 minutos por defecto
    max_waiting_time: int = 900   # 15 minutos por defecto
    transfer_type: str = 'nearby'
    
    def is_viable(self) -> bool:
        """
        Verifica si el transbordo es viable.
        
        Un transbordo es viable si:
        - El tiempo de caminata es razonable (< 10 minutos)
        - La distancia es caminable (< 500 metros)
        
        Returns:
            True si el transbordo es viable, False en caso contrario
        """
        # Verificar distancia (máximo 500 metros)
        if self.walking_distance_km > 0.5:
            return False
        
        # Verificar tiempo de caminata (máximo 10 minutos)
        if self.walking_time_seconds > 600:
            return False
        
        return True
    
    def get_total_transfer_time(self, waiting_time_seconds: int = 0) -> float:
        """
        Calcula el tiempo total de transbordo.
        
        Args:
            waiting_time_seconds: Tiempo de espera adicional (opcional)
            
        Returns:
            Tiempo total en segundos (caminata + espera)
        """
        return self.walking_time_seconds + waiting_time_seconds
    
    def __repr__(self):
        return (f"Transfer(Route {self.from_route_id}→{self.to_route_id}, "
                f"Stop {self.from_stop_id}→{self.to_stop_id}, "
                f"{self.walking_distance_km*1000:.0f}m, "
                f"{self.walking_time_seconds:.0f}s)")
    
    def to_dict(self) -> Dict[str, Any]:
        """Convierte la transferencia a diccionario"""
        return {
            'from_route_id': self.from_route_id,
            'to_route_id': self.to_route_id,
            'from_stop_id': self.from_stop_id,
            'to_stop_id': self.to_stop_id,
            'walking_distance_km': self.walking_distance_km,
            'walking_time_seconds': self.walking_time_seconds,
            'min_transfer_time': self.min_transfer_time,
            'max_waiting_time': self.max_waiting_time,
            'transfer_type': self.transfer_type,
            'is_viable': self.is_viable()
        }


class TransferManager:
    """
    Administrador de transferencias entre rutas.
    
    Mantiene un registro de todas las transferencias posibles
    y proporciona métodos para consultar opciones de transbordo.
    """
    
    def __init__(self):
        """Inicializa el administrador de transferencias"""
        # Diccionario: {(from_route, from_stop): [TransferConnection, ...]}
        self.transfers: Dict[tuple, list] = {}
        
        # Índice por ruta de destino para búsquedas rápidas
        # {to_route_id: [(from_route, from_stop, transfer), ...]}
        self.transfers_by_destination: Dict[str, list] = {}
    
    def add_transfer(self, transfer: TransferConnection):
        """
        Agrega una transferencia al registro.
        
        Args:
            transfer: TransferConnection a agregar
        """
        key = (transfer.from_route_id, transfer.from_stop_id)
        
        if key not in self.transfers:
            self.transfers[key] = []
        
        self.transfers[key].append(transfer)
        
        # Actualizar índice por destino
        if transfer.to_route_id not in self.transfers_by_destination:
            self.transfers_by_destination[transfer.to_route_id] = []
        
        self.transfers_by_destination[transfer.to_route_id].append(
            (transfer.from_route_id, transfer.from_stop_id, transfer)
        )
    
    def get_transfers_from(self, route_id: str, stop_id: str) -> list:
        """
        Obtiene todas las transferencias posibles desde una parada.
        
        Args:
            route_id: ID de la ruta actual
            stop_id: ID de la parada actual
            
        Returns:
            Lista de TransferConnection disponibles
        """
        key = (route_id, stop_id)
        return self.transfers.get(key, [])
    
    def get_transfers_to(self, route_id: str) -> list:
        """
        Obtiene todas las transferencias que llegan a una ruta.
        
        Args:
            route_id: ID de la ruta de destino
            
        Returns:
            Lista de tuplas (from_route, from_stop, TransferConnection)
        """
        return self.transfers_by_destination.get(route_id, [])
    
    def get_viable_transfers_from(self, route_id: str, stop_id: str) -> list:
        """
        Obtiene solo las transferencias viables desde una parada.
        
        Args:
            route_id: ID de la ruta actual
            stop_id: ID de la parada actual
            
        Returns:
            Lista de TransferConnection viables
        """
        all_transfers = self.get_transfers_from(route_id, stop_id)
        return [t for t in all_transfers if t.is_viable()]
    
    def count_transfers(self) -> int:
        """Retorna el número total de transferencias registradas"""
        return sum(len(transfers) for transfers in self.transfers.values())
    
    def get_statistics(self) -> Dict[str, Any]:
        """
        Obtiene estadísticas del sistema de transferencias.
        
        Returns:
            Diccionario con estadísticas
        """
        total = self.count_transfers()
        viable = sum(
            1 for transfers in self.transfers.values()
            for t in transfers if t.is_viable()
        )
        
        return {
            'total_transfers': total,
            'viable_transfers': viable,
            'viability_rate': viable / total if total > 0 else 0,
            'routes_with_transfers': len(set(
                t.from_route_id for transfers in self.transfers.values()
                for t in transfers
            ))
        }
    
    def __repr__(self):
        stats = self.get_statistics()
        return (f"TransferManager({stats['total_transfers']} transfers, "
                f"{stats['viable_transfers']} viable)")

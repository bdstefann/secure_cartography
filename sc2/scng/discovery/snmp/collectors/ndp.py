"""
SecureCartography NG - NDP Neighbor Collector (Huawei).

Collects NDP (Neighbor Discovery Protocol) neighbor information.
NDP is Huawei-proprietary and structurally similar to CDP.

Walks HUAWEI-NDP-MIB. Falls back gracefully (returns empty list)
when the MIB is not implemented on the target device, since some
YunShan-based and CloudEngine models prefer LLDP over NDP.
"""

from typing import Optional, Dict, List

from pysnmp.hlapi.v3arch.asyncio import SnmpEngine

from ...oids import HUAWEI_NDP
from ...models import Interface, Neighbor
from ..walker import SNMPWalker, AuthData
from ..parsers import decode_string, decode_ip
from .interfaces import resolve_interface_name


async def get_ndp_neighbors(
    target: str,
    auth: AuthData,
    interface_table: Optional[Dict[int, Interface]] = None,
    engine: Optional[SnmpEngine] = None,
    timeout: float = 5.0,
    verbose: bool = False,
) -> List[Neighbor]:
    """
    Get Huawei NDP neighbors from device.

    Args:
        target: Device IP address
        auth: SNMP authentication data
        interface_table: Pre-fetched interface table for name resolution
        engine: Optional shared SnmpEngine
        timeout: Request timeout per table
        verbose: Enable debug output

    Returns:
        List of Neighbor dataclasses (empty if NDP MIB not implemented)
    """
    walker = SNMPWalker(
        engine=engine,
        auth=auth,
        default_timeout=timeout,
        verbose=verbose,
    )

    def _vprint(msg: str):
        if verbose:
            print(f"  [ndp] {msg}")

    # Temporary storage keyed by NDP index (ifIndex.deviceIndex)
    neighbors_raw: Dict[str, Dict] = {}

    # Anchor walk on device id so we know which entries are real
    _vprint("Querying hwNdpCacheDeviceId...")
    results = await walker.walk(target, HUAWEI_NDP.CACHE_DEVICE_ID, auth)

    if not results:
        _vprint("No NDP data available (MIB likely unsupported)")
        return []

    for oid, value in results:
        device_id = decode_string(value)
        if not device_id:
            continue

        parts = oid.split('.')
        if len(parts) >= 2:
            if_index = int(parts[-2])
            index = f"{parts[-2]}.{parts[-1]}"
            neighbors_raw[index] = {
                'index': index,
                'if_index': if_index,
                'device_id': device_id,
            }

    _vprint(f"Found {len(neighbors_raw)} NDP entries")

    if not neighbors_raw:
        return []

    # Remote port
    _vprint("Querying hwNdpCacheDevicePort...")
    results = await walker.walk(target, HUAWEI_NDP.CACHE_DEVICE_PORT, auth)
    for oid, value in results:
        parts = oid.split('.')
        if len(parts) >= 2:
            index = f"{parts[-2]}.{parts[-1]}"
            if index in neighbors_raw:
                neighbors_raw[index]['remote_port'] = decode_string(value)

    # IP address (binary encoded)
    _vprint("Querying hwNdpCacheAddress...")
    results = await walker.walk(target, HUAWEI_NDP.CACHE_ADDRESS, auth)
    for oid, value in results:
        parts = oid.split('.')
        if len(parts) >= 2:
            index = f"{parts[-2]}.{parts[-1]}"
            if index in neighbors_raw:
                ip_addr = decode_ip(value)
                if ip_addr and '.' in ip_addr:
                    ip_parts = ip_addr.split('.')
                    if len(ip_parts) == 4:
                        try:
                            if all(0 <= int(p) <= 255 for p in ip_parts):
                                neighbors_raw[index]['ip_address'] = ip_addr
                        except ValueError:
                            pass

    # Platform string
    _vprint("Querying hwNdpCachePlatform...")
    results = await walker.walk(target, HUAWEI_NDP.CACHE_PLATFORM, auth)
    for oid, value in results:
        parts = oid.split('.')
        if len(parts) >= 2:
            index = f"{parts[-2]}.{parts[-1]}"
            if index in neighbors_raw:
                neighbors_raw[index]['platform'] = decode_string(value)

    # Software version
    _vprint("Querying hwNdpCacheVersion...")
    results = await walker.walk(target, HUAWEI_NDP.CACHE_VERSION, auth)
    for oid, value in results:
        parts = oid.split('.')
        if len(parts) >= 2:
            index = f"{parts[-2]}.{parts[-1]}"
            if index in neighbors_raw:
                neighbors_raw[index]['version'] = decode_string(value)

    # Convert to Neighbor objects
    neighbors: List[Neighbor] = []

    for index, data in neighbors_raw.items():
        device_id = data.get('device_id', '')
        if not device_id or device_id in ['', 'N/A', 'n/a']:
            if 'ip_address' not in data:
                continue
            device_id = data.get('ip_address', '')

        if_index = data.get('if_index', 0)
        if interface_table:
            local_interface = resolve_interface_name(if_index, interface_table)
        else:
            local_interface = f"ifIndex_{if_index}"

        neighbor = Neighbor.from_ndp(
            local_interface=local_interface,
            device_id=device_id,
            remote_port=data.get('remote_port', ''),
            ip_address=data.get('ip_address'),
            platform=data.get('platform'),
            version=data.get('version'),
            local_if_index=if_index,
            raw_index=index,
        )
        neighbors.append(neighbor)

    _vprint(f"Returning {len(neighbors)} valid NDP neighbors")
    return neighbors

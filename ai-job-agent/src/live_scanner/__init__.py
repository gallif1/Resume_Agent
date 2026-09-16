"""Live Job Scanner — continuous ATS career-board monitoring.

Isolated feature package. Reuses existing jobs DB + matching when possible.
"""

from live_scanner.service import LiveScannerService, get_scanner_service

__all__ = ["LiveScannerService", "get_scanner_service"]

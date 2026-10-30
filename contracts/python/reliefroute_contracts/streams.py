"""Stream and consumer-group names. Stream key == event_type."""

HAZARD_UPDATED = "hazard.updated"
VEHICLE_TELEMETRY = "vehicle.telemetry"
SUPPLIER_STOCK_UPDATED = "supplier.stock.updated"
PLAN_REQUESTED = "plan.requested"
PLAN_APPROVED = "plan.approved"
VEHICLE_DEVIATION = "vehicle.deviation"

# Consumer groups. Each side owns its groups and never reads with the other's.
GROUP_BRAIN = "brain"
GROUP_SENSES = "senses"

# Approximate MAXLEN for XADD trimming; streams are notifications, not storage.
STREAM_MAXLEN = 50_000

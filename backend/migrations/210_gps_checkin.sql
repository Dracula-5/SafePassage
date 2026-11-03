-- When the driver last confirmed on the phone, with no GPS fix, that the truck is still on its route. Each answer
-- holds off the 'offline' re-route for one grace period. See POST /api/vehicles/{id}/check-in.
ALTER TABLE vehicles ADD COLUMN gps_checkin_at timestamptz;

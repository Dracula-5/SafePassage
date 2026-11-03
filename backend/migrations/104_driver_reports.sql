-- A driver's own report of a problem on the road: why a deviation was opened when no GPS rule saw it. See
-- POST /api/vehicles/{id}/problem. A breakdown or accident opens a 'stalled' deviation (the truck cannot go on);
-- traffic or a blocked road opens an 'off_route' one, and the re-route avoids the road just ahead.
ALTER TABLE route_deviations
    ADD COLUMN reported_problem text CHECK (reported_problem IN ('traffic', 'road_blocked', 'breakdown', 'accident')),
    ADD COLUMN reported_note text CHECK (char_length(reported_note) <= 200);

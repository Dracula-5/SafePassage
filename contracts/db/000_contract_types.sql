-- ReliefRoute contract: extensions and shared enum types.
-- FROZEN after Gate 0. Values must match contracts/python/reliefroute_contracts/enums.py.
-- Adding an enum value later is allowed only via an approved contracts/requests/ change.

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgcrypto;  -- gen_random_uuid()

CREATE TYPE hazard_source AS ENUM ('sachet', 'imd', 'open_meteo', 'gdacs', 'firms', 'cwc', 'drill');
CREATE TYPE hazard_type AS ENUM ('flood', 'heavy_rain', 'cyclone', 'storm', 'landslide', 'wildfire', 'heatwave', 'earthquake', 'tsunami', 'other');
CREATE TYPE hazard_severity AS ENUM ('minor', 'moderate', 'severe', 'extreme', 'unknown');
CREATE TYPE hazard_status AS ENUM ('active', 'expired', 'cancelled');
CREATE TYPE facility_kind AS ENUM ('hospital', 'shelter', 'warehouse', 'godown', 'school', 'fuel', 'depot', 'other');
CREATE TYPE facility_source AS ENUM ('osm', 'datagov', 'portal');
CREATE TYPE item_code AS ENUM ('WATER_L', 'RICE_KG', 'PULSES_KG', 'RTE_MEAL', 'TARPAULIN', 'BLANKET', 'HYGIENE_KIT', 'MEDICAL_KIT', 'ORS_SACHET');
CREATE TYPE stock_report_channel AS ENUM ('portal', 'whatsapp', 'api');
CREATE TYPE vehicle_status AS ENUM ('available', 'assigned', 'en_route', 'maintenance');
CREATE TYPE plan_trigger_kind AS ENUM ('hazard', 'manual', 'replan', 'drill');
CREATE TYPE plan_status AS ENUM ('pending', 'planning', 'awaiting_approval', 'approved', 'rejected', 'dispatched', 'superseded', 'unreachable', 'failed');
CREATE TYPE route_status AS ENUM ('planned', 'active', 'completed', 'abandoned');
CREATE TYPE order_status AS ENUM ('draft', 'approved', 'dispatched', 'in_transit', 'delivered', 'cancelled');
CREATE TYPE approval_decision AS ENUM ('approved', 'rejected');
CREATE TYPE agent_node AS ENUM ('supervisor', 'sentinel', 'needs', 'procurement', 'route_planner', 'critic', 'dispatcher', 'replanner', 'human');
CREATE TYPE agent_event_type AS ENUM ('node_started', 'node_finished', 'tool_call', 'tool_result', 'decision', 'critic_rejection', 'approval_requested', 'approval_decided', 'dispatch', 'data_stale', 'error');
CREATE TYPE deviation_kind AS ENUM ('off_route', 'stalled', 'offline', 'hazard_on_route');

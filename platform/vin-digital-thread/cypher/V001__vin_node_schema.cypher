-- Migration V001: VIN node uniqueness constraint.
-- Idempotent: IF NOT EXISTS guards re-execution.
CREATE CONSTRAINT vin_unique IF NOT EXISTS
  FOR (v:VIN)
  REQUIRE v.vin IS UNIQUE;

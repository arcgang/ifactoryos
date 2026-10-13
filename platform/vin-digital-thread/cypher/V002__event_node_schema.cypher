-- Migration V002: Event node uniqueness constraint.
-- eventId is the idempotency anchor — MERGE (e:Event {eventId: $id}) uses this.
CREATE CONSTRAINT event_unique IF NOT EXISTS
  FOR (e:Event)
  REQUIRE e.eventId IS UNIQUE;

-- Migration V003: Event node indexes for Process Replay and shop-boundary traversal.

-- Paint prep stage filter in Process Replay: WHERE e.eventType = 'PAINT_PREP_STAGE' ORDER BY e.occurredAt
CREATE INDEX event_by_type_time IF NOT EXISTS
  FOR (e:Event)
  ON (e.eventType, e.occurredAt);

-- 14-stage replay scan: WHERE e.stageNumber = $n
CREATE INDEX event_by_stage_number IF NOT EXISTS
  FOR (e:Event)
  ON (e.stageNumber);

-- Shop-boundary gap detection: WHERE e.shop = $shop
CREATE INDEX event_by_shop IF NOT EXISTS
  FOR (e:Event)
  ON (e.shop);

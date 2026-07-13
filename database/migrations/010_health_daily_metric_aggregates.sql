-- Apple Health schema v3: causally ordered per-metric daily aggregates.
-- Additive expand migration. The schema-v2 table and legacy raw table remain
-- available until the explicit contract phase.

BEGIN;

CREATE TABLE IF NOT EXISTS health_daily_metric_aggregates (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    source data_source NOT NULL,
    collector VARCHAR(32) NOT NULL,
    metric_date DATE NOT NULL,
    metric_family VARCHAR(32) NOT NULL,
    timezone VARCHAR(64) NOT NULL,
    total_value NUMERIC(15, 4) NOT NULL DEFAULT 0,
    average_value NUMERIC(15, 4),
    sample_count INTEGER NOT NULL DEFAULT 0,
    samples_received INTEGER NOT NULL DEFAULT 0,
    samples_aggregated INTEGER NOT NULL DEFAULT 0,
    metrics JSONB NOT NULL DEFAULT '{}',
    snapshot_generated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    payload_hash VARCHAR(64) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    CONSTRAINT health_daily_metric_aggregates_natural_key
        UNIQUE (user_id, source, collector, metric_date, metric_family),
    CONSTRAINT health_daily_metric_aggregates_family_check CHECK (
        metric_family IN ('steps', 'active_energy', 'heart_rate', 'hrv', 'sleep')
    ),
    CONSTRAINT health_daily_metric_aggregates_total_check CHECK (total_value >= 0),
    CONSTRAINT health_daily_metric_aggregates_total_finite_check
        CHECK (total_value <> 'NaN'::numeric),
    CONSTRAINT health_daily_metric_aggregates_average_check CHECK (
        average_value IS NULL OR (
            average_value >= 0 AND average_value <> 'NaN'::numeric
        )
    ),
    CONSTRAINT health_daily_metric_aggregates_sample_count_check CHECK (sample_count >= 0),
    CONSTRAINT health_daily_metric_aggregates_received_check CHECK (samples_received >= 0),
    CONSTRAINT health_daily_metric_aggregates_aggregated_check CHECK (samples_aggregated >= 0)
);

-- CREATE TABLE IF NOT EXISTS does not add new constraints to a table created by
-- an earlier pre-merge run. Converge those databases without dropping data.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'health_daily_metric_aggregates_natural_key'
          AND conrelid = 'health_daily_metric_aggregates'::regclass
    ) THEN
        ALTER TABLE health_daily_metric_aggregates
            ADD CONSTRAINT health_daily_metric_aggregates_natural_key
            UNIQUE (user_id, source, collector, metric_date, metric_family);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'health_daily_metric_aggregates_total_finite_check'
          AND conrelid = 'health_daily_metric_aggregates'::regclass
    ) THEN
        ALTER TABLE health_daily_metric_aggregates
            ADD CONSTRAINT health_daily_metric_aggregates_total_finite_check
            CHECK (total_value <> 'NaN'::numeric);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'health_daily_metric_aggregates_average_check'
          AND conrelid = 'health_daily_metric_aggregates'::regclass
    ) THEN
        ALTER TABLE health_daily_metric_aggregates
            ADD CONSTRAINT health_daily_metric_aggregates_average_check CHECK (
                average_value IS NULL OR (
                    average_value >= 0 AND average_value <> 'NaN'::numeric
                )
            );
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_health_daily_metric_aggregates_user_date
    ON health_daily_metric_aggregates(user_id, metric_date);
CREATE INDEX IF NOT EXISTS idx_health_daily_metric_aggregates_family_freshness
    ON health_daily_metric_aggregates(user_id, metric_family, metric_date, snapshot_generated_at DESC);
CREATE INDEX IF NOT EXISTS idx_health_daily_metric_aggregates_collector
    ON health_daily_metric_aggregates(collector);

DROP TRIGGER IF EXISTS update_health_daily_metric_aggregates_updated_at
    ON health_daily_metric_aggregates;
CREATE TRIGGER update_health_daily_metric_aggregates_updated_at
    BEFORE UPDATE ON health_daily_metric_aggregates
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- Preserve any schema-v2 rows written before this migration. Each legacy row
-- is expanded into five family rows. A zero is still authoritative because the
-- old contract claimed complete coverage for every stored column.
INSERT INTO health_daily_metric_aggregates
       (user_id, source, collector, metric_date, metric_family, timezone,
        total_value, average_value, sample_count, samples_received,
        samples_aggregated, metrics, snapshot_generated_at, payload_hash)
SELECT h.user_id,
       h.source,
       'legacy_daily',
       h.metric_date,
       family.metric_family,
       h.timezone,
       family.total_value,
       family.average_value,
       family.sample_count,
       family.samples_received,
       family.samples_aggregated,
       family.metrics,
       COALESCE(h.snapshot_generated_at, h.updated_at, h.created_at, NOW()),
       md5(concat_ws('|', h.id::text, family.metric_family,
                     family.total_value::text, family.average_value::text,
                     family.sample_count::text,
                     COALESCE(h.snapshot_generated_at, h.updated_at, h.created_at, NOW())::text))
FROM health_daily_aggregates AS h
CROSS JOIN LATERAL (
    VALUES
        ('steps'::varchar,
         h.steps::numeric,
         NULL::numeric,
         COALESCE((h.metrics->'records_by_type'->>'step_count')::integer, 0),
         COALESCE((h.metrics->'records_by_type'->>'step_count')::integer, 0),
         COALESCE((h.metrics->'records_by_type'->>'step_count')::integer, 0),
         jsonb_build_object('records_by_type', jsonb_build_object(
             'step_count', COALESCE((h.metrics->'records_by_type'->>'step_count')::integer, 0)))),
        ('active_energy'::varchar,
         h.active_energy_kcal::numeric,
         NULL::numeric,
         COALESCE((h.metrics->'records_by_type'->>'active_energy')::integer, 0),
         COALESCE((h.metrics->'records_by_type'->>'active_energy')::integer, 0),
         COALESCE((h.metrics->'records_by_type'->>'active_energy')::integer, 0),
         jsonb_build_object('records_by_type', jsonb_build_object(
             'active_energy', COALESCE((h.metrics->'records_by_type'->>'active_energy')::integer, 0)))),
        ('heart_rate'::varchar,
         0::numeric,
         h.avg_heart_rate::numeric,
         h.heart_rate_samples,
         h.heart_rate_samples,
         h.heart_rate_samples,
         jsonb_build_object('records_by_type', jsonb_build_object(
             'heart_rate', h.heart_rate_samples))),
        ('hrv'::varchar,
         0::numeric,
         h.avg_hrv_ms::numeric,
         h.hrv_samples,
         h.hrv_samples,
         h.hrv_samples,
         jsonb_build_object('records_by_type', jsonb_build_object(
             'heart_rate_variability', h.hrv_samples))),
        ('sleep'::varchar,
         h.sleep_seconds::numeric,
         NULL::numeric,
         COALESCE((h.metrics->'records_by_type'->>'sleep_analysis')::integer, 0),
         COALESCE((h.metrics->'records_by_type'->>'sleep_analysis')::integer, 0),
         COALESCE((h.metrics->'records_by_type'->>'sleep_analysis')::integer, 0),
         jsonb_build_object('records_by_type', jsonb_build_object(
             'sleep_analysis', COALESCE((h.metrics->'records_by_type'->>'sleep_analysis')::integer, 0))))
) AS family(metric_family, total_value, average_value, sample_count,
            samples_received, samples_aggregated, metrics)
ON CONFLICT ON CONSTRAINT health_daily_metric_aggregates_natural_key DO NOTHING;

COMMIT;

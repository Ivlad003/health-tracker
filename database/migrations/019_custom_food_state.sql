-- Personal products that we also try to create in the user's FatSecret profile.
-- food_id is the only storable FatSecret value. Names and calories the user
-- typed stay on the durable manual nutrition revision, not in these columns.

ALTER TABLE food_products
    ADD COLUMN IF NOT EXISTS custom_fs_food_id VARCHAR(50),
    ADD COLUMN IF NOT EXISTS custom_fs_serving_id VARCHAR(32),
    ADD COLUMN IF NOT EXISTS custom_fs_state VARCHAR(16);

DO $$ BEGIN
    ALTER TABLE food_products
        ADD CONSTRAINT food_products_custom_fs_state_check
        CHECK (custom_fs_state IS NULL OR custom_fs_state IN ('needs_macros', 'refused', 'created'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

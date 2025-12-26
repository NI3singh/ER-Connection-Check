-- Get transactions records
SELECT * FROM public.transactions
ORDER BY id ASC 

-- Get analysis state records
SELECT * FROM public.analysis_state
ORDER BY id ASC 

-- Get clusters records
SELECT * FROM public.smurf_clusters
ORDER BY id ASC 

-- Get user metadata records
SELECT * FROM public.user_metadata
ORDER BY id ASC 

-- Get signal strength records
SELECT * FROM public.signal_strengths
ORDER BY id ASC 

-- Get entity links records
SELECT * FROM public.entity_links
ORDER BY id ASC 

-- Get investigation records
SELECT * FROM public.investigation_flags
ORDER BY id ASC 

-- Delete all data
TRUNCATE user_metadata, entity_links, signal_strengths, smurf_clusters, analysis_state CASCADE;

--Delete all tables
DROP TABLE IF EXISTS user_metadata, entity_links, signal_strengths, smurf_clusters, analysis_state, investigation_flags;

-- Deletes all derived and state tables.
DELETE FROM public.analysis_state
DELETE FROM public.smurf_clusters
DELETE FROM public.user_metadata
DELETE FROM public.entity_links
DELETE FROM public.signal_strengths

-- Check how many times a specific user appears
SELECT user_id, occurrence_count FROM user_metadata WHERE user_id='alice';

-- Inspect strength and confidence of a specific signal
SELECT signal_type, signal_value, user_count, confidence_weight FROM signal_strengths WHERE signal_value='103.45.67.99';

-- Check current processing state of the AML worker
SELECT last_processed_metadata_id, worker_status FROM analysis_state;



-- ============================================
-- SYSTEM HEALTH / ROW COUNTS
-- ============================================
-- Quick snapshot of table sizes and active entities
-- Helps verify ingestion, linking, and clustering health

SELECT 'user_metadata' as table, COUNT(*) as count FROM user_metadata
UNION ALL
SELECT 'signal_strengths', COUNT(*) FROM signal_strengths
UNION ALL
SELECT 'entity_links', COUNT(*) FROM entity_links WHERE is_active=true
UNION ALL
SELECT 'smurf_clusters', COUNT(*) FROM smurf_clusters WHERE is_active=true
UNION ALL
SELECT 'analysis_state', COUNT(*) FROM analysis_state;

SELECT user_a, user_b, shared_signals, adjusted_confidence FROM entity_links WHERE (user_a IN ('alice', 'bob', 'dave') AND user_b IN ('alice', 'bob', 'dave')) ORDER BY user_a, user_b;

SELECT id, user_id, device_hash, ip_address, first_seen FROM user_metadata WHERE user_id IN ('alice', 'bob', 'dave') ORDER BY id;
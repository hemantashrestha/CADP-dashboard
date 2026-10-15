-- CADP: read-only database user for the dashboard (NFR6, check P9).
-- Run once in Supabase: SQL Editor > New query > paste > Run.
-- Change the password on the next line before running. Keep it out of GitHub.

CREATE ROLE cadp_reader LOGIN PASSWORD 'CHANGE_ME_to_a_long_password';

GRANT CONNECT ON DATABASE postgres TO cadp_reader;
GRANT USAGE ON SCHEMA public TO cadp_reader;
GRANT SELECT ON fact_campaign_performance, dim_channel, dim_campaign_type, dim_segment, dim_date TO cadp_reader;

-- Check: should list five tables, all with privilege_type = SELECT only.
SELECT table_name, privilege_type
FROM information_schema.role_table_grants
WHERE grantee = 'cadp_reader'
ORDER BY table_name;

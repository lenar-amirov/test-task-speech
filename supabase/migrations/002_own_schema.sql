-- Speech QC micro-project lives in its own schema `qc`, not exposed to the public API.
-- Only the qc-check edge function reaches it (direct Postgres connection). Table is empty at this point.
create schema if not exists qc;
alter table public.qc_checks set schema qc;
alter table qc.qc_checks rename to checks;
alter index qc.qc_checks_created_at rename to checks_created_at;
alter index qc.qc_checks_ip_recent rename to checks_ip_recent;
drop view if exists public.qc_checks_feed;
drop view if exists public.qc_checks_stats;
revoke all on schema qc from public, anon, authenticated;
revoke all on all tables in schema qc from public, anon, authenticated;
comment on schema qc is 'Speech auto-QC demo (inDrive DataTasks test task): decision log. Edge function qc-check, bucket qc-recordings.';

-- Every QC decision is logged: the buyer report, the 10% batch rule and threshold tuning are built from this table.
create table if not exists public.qc_checks (
  id            bigint generated always as identity primary key,
  created_at    timestamptz not null default now(),
  lang          text not null,
  scenario      text,
  prompt        text not null,
  transcript    text,
  detected_lang text,
  cer           real,
  signal        jsonb not null,
  phone         jsonb,
  accept        boolean not null,
  reasons       text[] not null default '{}',
  usable_s      real not null default 0,
  asr_model     text,
  audio_path    text,
  ip_hash       text
);
create index if not exists qc_checks_created_at on public.qc_checks (created_at desc);
create index if not exists qc_checks_ip_recent on public.qc_checks (ip_hash, created_at desc);

-- Only the edge function (service role) writes. The public page reads the feed through a view without audio or network data.
alter table public.qc_checks enable row level security;

create or replace view public.qc_checks_feed with (security_invoker = false) as
  select id, created_at, lang, scenario, prompt, transcript, detected_lang, cer, accept, reasons, usable_s,
         (signal->>'speech_s')::real as speech_s, (signal->>'snr_db')::real as snr_db
  from public.qc_checks
  order by created_at desc
  limit 50;
revoke all on public.qc_checks_feed from anon, authenticated;
grant select on public.qc_checks_feed to anon, authenticated;

create or replace view public.qc_checks_stats with (security_invoker = false) as
  select count(*)::int as total,
         count(*) filter (where accept)::int as accepted,
         coalesce(sum(usable_s), 0)::real as usable_seconds
  from public.qc_checks;
revoke all on public.qc_checks_stats from anon, authenticated;
grant select on public.qc_checks_stats to anon, authenticated;

-- Recordings stay private: only the service role reads or writes them.
insert into storage.buckets (id, name, public) values ('qc-recordings', 'qc-recordings', false)
  on conflict (id) do nothing;

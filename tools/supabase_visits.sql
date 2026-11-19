-- Visit log for the admin page. Run once in Supabase: SQL Editor > New query > paste all of this > Run.
-- Each row is one visit (one browser tab): who, when it started, when it was last active, pages viewed, device.
-- Row Level Security: a signed-in user can only add and update their own visits; only accounts listed in
-- public.admins can read everyone's. The site never holds a secret key, and no email address is kept in the code.
--
-- AFTER running this, make yourself admin with a second query, typing your own sign-in email between the quotes:
--     insert into public.admins (user_id) select id from auth.users where email = 'YOUR-EMAIL-HERE';

create table if not exists public.admins (
  user_id uuid primary key references auth.users (id) on delete cascade
);
alter table public.admins enable row level security;
drop policy if exists "see own admin row" on public.admins;
create policy "see own admin row" on public.admins for select to authenticated using (user_id = auth.uid());

create table if not exists public.visits (
  id         uuid primary key,
  user_id    uuid not null default auth.uid() references auth.users (id) on delete cascade,
  email      text not null default (auth.jwt() ->> 'email'),
  started    timestamptz not null default now(),
  last_seen  timestamptz not null default now(),
  pages      integer not null default 1,
  first_path text,
  device     text,
  country    text
);
create index if not exists visits_started on public.visits (started desc);
alter table public.visits enable row level security;

drop policy if exists "add own visits" on public.visits;
create policy "add own visits" on public.visits for insert to authenticated with check (user_id = auth.uid());
drop policy if exists "update own visits" on public.visits;
create policy "update own visits" on public.visits for update to authenticated using (user_id = auth.uid()) with check (user_id = auth.uid());
drop policy if exists "read own visits, admins read all" on public.visits;
create policy "read own visits, admins read all" on public.visits for select to authenticated
  using (user_id = auth.uid() or exists (select 1 from public.admins a where a.user_id = auth.uid()));

-- One call per page view / once a minute while the page is open: start the visit or mark it still active.
create or replace function public.visit_ping(p_sid uuid, p_path text, p_page boolean, p_device text, p_country text)
returns void language sql security invoker set search_path = public as $$
  insert into public.visits as v (id, first_path, device, country)
  values (p_sid, left(p_path, 200), left(p_device, 60), left(p_country, 60))
  on conflict (id) do update
     set last_seen = now(), pages = v.pages + case when p_page then 1 else 0 end
   where v.user_id = auth.uid();
$$;
revoke all on function public.visit_ping(uuid, text, boolean, text, text) from public, anon;
grant execute on function public.visit_ping(uuid, text, boolean, text, text) to authenticated;

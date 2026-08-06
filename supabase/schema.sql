-- Esquema del radar SECOP para Supabase.
--
-- Se corre UNA vez, pegándolo completo en el SQL Editor del proyecto.
-- Es idempotente: volver a correrlo no rompe nada ni borra datos.

-- ---------------------------------------------------------------------------
-- Búsqueda insensible a tildes
-- ---------------------------------------------------------------------------
-- El motor del radar normaliza el texto antes de comparar, y el buscador tiene
-- que hacer lo mismo: si escribir "formacion" no encuentra "formación", el
-- dashboard reintroduce por la puerta de atrás el problema que el radar existe
-- para resolver.
create extension if not exists unaccent;

-- unaccent() no es IMMUTABLE por sí sola (depende del diccionario cargado), y
-- una columna generada exige inmutabilidad. Este envoltorio fija el diccionario
-- explícitamente, que es lo que la vuelve determinista.
create or replace function public.f_unaccent(text)
returns text
language sql
immutable
strict
parallel safe
as $$ select public.unaccent('public.unaccent'::regdictionary, $1) $$;

-- ---------------------------------------------------------------------------
-- Tabla principal
-- ---------------------------------------------------------------------------
-- La clave es (id, perfil): un mismo proceso de SECOP puede interesarle a
-- varios perfiles con puntajes distintos, y cada uno lleva su propio
-- seguimiento y su propio estado de retiro.
create table if not exists public.procesos (
    id              text        not null,
    perfil          text        not null,
    dataset         text,
    entidad         text,
    objeto          text,
    departamento    text,
    ciudad          text,
    valor           numeric,
    modalidad       text,
    estado          text,
    fecha_cierre    timestamptz,
    fecha_publicacion timestamptz,
    url             text,
    unspsc          text,
    score           integer,
    reasons         text,
    raw             jsonb,
    -- first_seen es la evidencia de cuándo apareció el proceso de verdad,
    -- independiente de lo que el portal muestre después.
    first_seen      timestamptz not null default now(),
    last_seen       timestamptz not null default now(),
    sweeps_seen     integer     not null default 1,
    alerted_at      timestamptz,
    disappeared_at  timestamptz,
    primary key (id, perfil)
);

-- Columna de búsqueda: objeto + entidad + ubicación, sin tildes y con el
-- diccionario español (maneja plurales y raíces: "capacitaciones" encuentra
-- "capacitación").
alter table public.procesos
    drop column if exists busqueda;

alter table public.procesos
    add column busqueda tsvector
    generated always as (
        to_tsvector(
            'spanish',
            public.f_unaccent(
                coalesce(objeto, '') || ' ' ||
                coalesce(entidad, '') || ' ' ||
                coalesce(departamento, '') || ' ' ||
                coalesce(ciudad, '')
            )
        )
    ) stored;

create index if not exists procesos_busqueda_idx  on public.procesos using gin (busqueda);
create index if not exists procesos_score_idx     on public.procesos (score desc);
create index if not exists procesos_perfil_idx    on public.procesos (perfil);
create index if not exists procesos_first_seen_idx on public.procesos (first_seen desc);
create index if not exists procesos_cierre_idx    on public.procesos (fecha_cierre);

-- ---------------------------------------------------------------------------
-- Bitácora de barridos
-- ---------------------------------------------------------------------------
create table if not exists public.barridos (
    id          bigserial primary key,
    corrido_at  timestamptz not null default now(),
    perfil      text not null,
    dataset     text,
    descargados integer,
    archivadas  integer,
    alertables  integer,
    nuevas      integer
);

create index if not exists barridos_fecha_idx on public.barridos (corrido_at desc);

-- ---------------------------------------------------------------------------
-- Seguridad
-- ---------------------------------------------------------------------------
-- El HTML del dashboard es público, pero sin sesión no devuelve una sola fila.
-- La anon key sólo sirve para autenticarse; los datos exigen usuario logueado.
-- La service_role key que usa GitHub Actions salta RLS por diseño.
alter table public.procesos enable row level security;
alter table public.barridos enable row level security;

drop policy if exists "lectura autenticada" on public.procesos;
create policy "lectura autenticada"
    on public.procesos for select
    to authenticated
    using (true);

drop policy if exists "lectura autenticada barridos" on public.barridos;
create policy "lectura autenticada barridos"
    on public.barridos for select
    to authenticated
    using (true);

-- ---------------------------------------------------------------------------
-- Buscador
-- ---------------------------------------------------------------------------
-- Toda la lógica de búsqueda vive acá para que el frontend sea sólo pantalla.
-- SECURITY INVOKER (el default) hace que RLS se siga aplicando: un usuario sin
-- sesión no obtiene nada aunque llame la función directamente.
create or replace function public.buscar_procesos(
    q               text    default null,
    p_perfil        text    default null,
    p_departamento  text    default null,
    p_min_score     integer default null,
    p_min_valor     numeric default null,
    p_solo_abiertos boolean default false,
    p_incluir_retirados boolean default false,
    p_limit         integer default 100,
    p_offset        integer default 0
)
returns setof public.procesos
language sql
stable
as $$
    select *
    from public.procesos p
    where
        -- Consulta de texto: se normaliza igual que la columna indexada, así
        -- que buscar "formacion" o "formación" da exactamente lo mismo.
        (q is null or q = '' or p.busqueda @@ websearch_to_tsquery('spanish', public.f_unaccent(q)))
        and (p_perfil is null or p.perfil = p_perfil)
        and (p_departamento is null or p.departamento ilike '%' || p_departamento || '%')
        and (p_min_score is null or p.score >= p_min_score)
        and (p_min_valor is null or p.valor >= p_min_valor)
        and (not p_solo_abiertos or p.fecha_cierre is null or p.fecha_cierre >= now())
        and (p_incluir_retirados or p.disappeared_at is null)
    order by p.score desc nulls last, p.first_seen desc
    limit  greatest(1, least(coalesce(p_limit, 100), 500))
    offset greatest(0, coalesce(p_offset, 0));
$$;

-- Cifras para las tarjetas de arriba del dashboard.
create or replace function public.resumen_radar()
returns table (
    perfil          text,
    activos         bigint,
    alta_prioridad  bigint,
    retirados       bigint,
    cierran_pronto  bigint,
    valor_total     numeric
)
language sql
stable
as $$
    select
        p.perfil,
        count(*) filter (where p.disappeared_at is null)                       as activos,
        count(*) filter (where p.disappeared_at is null and p.score >= 80)     as alta_prioridad,
        count(*) filter (where p.disappeared_at is not null)                   as retirados,
        count(*) filter (where p.disappeared_at is null
                           and p.fecha_cierre between now() and now() + interval '10 days') as cierran_pronto,
        coalesce(sum(p.valor) filter (where p.disappeared_at is null), 0)      as valor_total
    from public.procesos p
    group by p.perfil
    order by p.perfil;
$$;

-- Lista de perfiles con datos, para poblar el selector del buscador.
create or replace function public.perfiles_disponibles()
returns table (perfil text, n bigint)
language sql
stable
as $$
    select p.perfil, count(*) as n
    from public.procesos p
    group by p.perfil
    order by p.perfil;
$$;

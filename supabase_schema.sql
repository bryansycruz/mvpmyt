-- ─────────────────────────────────────────────────────────────
-- supabase_schema.sql
-- Tabla del histórico de mampostería para Supabase (PostgreSQL).
--
-- Cómo usarlo:
--   Supabase → tu proyecto → SQL Editor → New query → pega esto → Run.
--
-- Los nombres de columna van entre comillas para que coincidan EXACTAMENTE
-- (mayúsculas incluidas) con las columnas que usa la app (data_schema.COLUMNAS).
-- ─────────────────────────────────────────────────────────────

create table if not exists registros_mamposteria (
    id                      bigint generated always as identity primary key,
    "Fecha"                 date,
    "Oficial"               text,
    "Ayudante"              text,
    "Sector"                text,
    "Piso"                  text,
    "Zona"                  text,
    "Largo_m"               double precision,
    "Alto_m"                double precision,
    "M2_ejecutados"         double precision,
    "Num_sacos"             double precision,
    "Consumo_real_sac_m2"   double precision,
    "Consumo_mortero_kg"    double precision,
    "Num_dovelas"           double precision,
    "ML_dovelas"            double precision,
    "Tipo_ladrillo"         text,
    "Cumple_meta"           boolean,
    "Observaciones"         text,
    "Grupo_id"              text,
    "Timestamp_registro"    timestamptz,
    created_at              timestamptz default now()
);

-- Índices útiles para los filtros/cierres de la app.
create index if not exists idx_registros_fecha   on registros_mamposteria ("Fecha");
create index if not exists idx_registros_oficial on registros_mamposteria ("Oficial");
create index if not exists idx_registros_grupo   on registros_mamposteria ("Grupo_id");

-- ─────────────────────────────────────────────────────────────
-- SEGURIDAD (elige UNA opción):
--
-- Opción A (recomendada para este MVP): usa la clave `service_role` en los
--   secrets de Streamlit. Esa clave omite RLS y vive SOLO del lado servidor
--   (nunca se expone al navegador en Streamlit). No necesitas políticas.
--
-- Opción B: usa la clave `anon` + habilita RLS con políticas permisivas.
--   Descomenta lo siguiente si vas por este camino:
--
-- alter table registros_mamposteria enable row level security;
--
-- create policy "lectura_publica" on registros_mamposteria
--     for select using (true);
--
-- create policy "insercion_publica" on registros_mamposteria
--     for insert with check (true);
--
-- create policy "borrado_publico" on registros_mamposteria
--     for delete using (true);   -- habilita "🗑️ Eliminar registros (admin)"
-- ─────────────────────────────────────────────────────────────


-- ─────────────────────────────────────────────────────────────
-- CONFIGURACIÓN EDITABLE (meta, kg/saco, proyecto)
-- Tabla clave-valor que la app lee/escribe desde el panel "⚙️ Configuración
-- (admin)". Si no creas esta tabla, la app sigue funcionando con los valores
-- por defecto (no es obligatoria, solo habilita la edición permanente).
-- ─────────────────────────────────────────────────────────────
create table if not exists config_app (
    clave           text primary key,
    valor           text not null,
    actualizado_en  timestamptz default now()
);

-- Valores iniciales (opcional: si faltan, la app usa sus defectos).
insert into config_app (clave, valor) values
    ('meta_sac_m2', '0.84'),
    ('kg_por_saco', '42.5'),
    ('proyecto',    'Serrania Campestre')
on conflict (clave) do nothing;

-- Igual que arriba: con la key `service_role` (Opción A) NO necesitas políticas.
-- Si usas la key `anon` (Opción B), habilita RLS y descomenta:
--
-- alter table config_app enable row level security;
--
-- create policy "config_lectura_publica" on config_app
--     for select using (true);
--
-- create policy "config_escritura_publica" on config_app
--     for insert with check (true);
--
-- create policy "config_actualizacion_publica" on config_app
--     for update using (true) with check (true);


-- ─────────────────────────────────────────────────────────────
-- DESPERDICIO POR TIPO DE BLOQUE (P.V. / P.H.)
-- Correr esta sección ANTES de desplegar la versión con la pestaña
-- 🧱 Materiales: la app hace SELECT explícito de estas columnas y
-- fallaría si no existen.
-- ─────────────────────────────────────────────────────────────

-- Columnas nuevas del histórico (los registros viejos quedan en null):
--   Tipo_bloque_PH  → bloque divisorio del grupo (Tipo_ladrillo guarda el P.V.)
--   Bloques_*_teo   → bloques teóricos por muro (snapshot al guardar)
alter table registros_mamposteria
    add column if not exists "Tipo_bloque_PH"  text,
    add column if not exists "Bloques_PV_teo"  double precision,
    add column if not exists "Bloques_PH_teo"  double precision;

-- Salidas de almacén (un vale por fila; Cantidad SIEMPRE en unidades,
-- la app convierte estibas → unidades al digitar).
create table if not exists almacen_salidas (
    id                      bigint generated always as identity primary key,
    "Fecha"                 date,
    "Sector"                text,
    "Piso"                  text,
    "Tipo_bloque"           text,
    "Cantidad"              double precision,
    "No_vale"               text,
    "Observaciones"         text,
    "Timestamp_registro"    timestamptz,
    created_at              timestamptz default now()
);

create index if not exists idx_salidas_fecha on almacen_salidas ("Fecha");
create index if not exists idx_salidas_tipo  on almacen_salidas ("Tipo_bloque");

-- Entradas de almacén (una remisión de compra por fila; Cantidad SIEMPRE en
-- unidades reales recibidas. Estibas_ing/Estibas_dev son control de pallets y
-- NO descuentan ladrillos. El acumulado se calcula en la app, no se guarda).
-- Las restricciones van dentro del CREATE para que el script sea idempotente
-- (un `alter table ... add constraint` fallaría si se corre dos veces).
create table if not exists almacen_entradas (
    id                      bigint generated always as identity primary key,
    "Fecha"                 date not null,
    "Tipo_bloque"           text not null,
    "Cantidad"              double precision not null check ("Cantidad" > 0),
    "Estibas_ing"           double precision check (coalesce("Estibas_ing", 0) >= 0),
    "Estibas_dev"           double precision check (coalesce("Estibas_dev", 0) >= 0),
    "No_remision"           text,
    "Proveedor"             text,
    "Observaciones"         text,
    "Timestamp_registro"    timestamptz,
    created_at              timestamptz default now()
);

create index if not exists idx_entradas_fecha on almacen_entradas ("Fecha");
create index if not exists idx_entradas_tipo  on almacen_entradas ("Tipo_bloque");

-- Estibas devueltas (pallets de madera VACÍOS regresados al proveedor). Ledger
-- APARTE del material: NO está unido al pedido ni a los ladrillos y NO afecta el
-- stock de bloque. `Cantidad` = nº de pallets devueltos. El borrado de un
-- movimiento mal digitado se hace por `id` y funciona con la clave service_role
-- (omite RLS); con la clave anon haría falta una policy DELETE.
create table if not exists almacen_estibas_dev (
    id                      bigint generated always as identity primary key,
    "Fecha"                 date not null,
    "Cantidad"              double precision not null check ("Cantidad" > 0),
    "Proveedor"             text,
    "No_remision"           text,
    "Observaciones"         text,
    "Timestamp_registro"    timestamptz,
    created_at              timestamptz default now()
);

create index if not exists idx_estibas_fecha on almacen_estibas_dev ("Fecha");

-- Config nueva del módulo de desperdicio (si faltan, la app usa sus defectos):
--   umbral_desperdicio_pct → semáforo de la conciliación (verde ≤ umbral)
--   factor_ajuste_bloques  → multiplica el teórico en la conciliación para
--                            cubrir medios bloques/trabas (ej. 1.03-1.05)
insert into config_app (clave, valor) values
    ('umbral_desperdicio_pct', '7'),
    ('factor_ajuste_bloques',  '1.0')
on conflict (clave) do nothing;

-- El catálogo de bloques se guarda como JSON en config_app (clave
-- 'catalogo_bloques') desde la propia app; no necesita tabla aparte.


-- ─────────────────────────────────────────────────────────────
-- INTEGRIDAD DE DATOS (aplicado en producción 2026-06-11)
-- Defensa en profundidad: la app valida, la BD re-valida. Bloquea
-- valores negativos/nulos aunque alguien inserte por fuera de la app.
-- ─────────────────────────────────────────────────────────────
alter table registros_mamposteria
    add constraint chk_reg_no_negativos check (
        coalesce("Largo_m", 0)            >= 0 and
        coalesce("Alto_m", 0)             >= 0 and
        coalesce("M2_ejecutados", 0)      >= 0 and
        coalesce("Num_sacos", 0)          >= 0 and
        coalesce("Consumo_mortero_kg", 0) >= 0 and
        coalesce("Num_dovelas", 0)        >= 0 and
        coalesce("ML_dovelas", 0)         >= 0 and
        coalesce("Bloques_PV_teo", 0)     >= 0 and
        coalesce("Bloques_PH_teo", 0)     >= 0
    );

alter table almacen_salidas
    alter column "Fecha"       set not null,
    alter column "Tipo_bloque" set not null,
    alter column "Cantidad"    set not null,
    add constraint chk_sal_cantidad_positiva check ("Cantidad" > 0);


-- ─────────────────────────────────────────────────────────────
-- POLÍTICAS MVP APLICADAS EN PRODUCCIÓN (Opción B: clave anon) — 2026-06-22
-- Estado real del proyecto: la app usa la clave `anon`, así que RLS está
-- activado en todas las tablas CON políticas permisivas. Lo de abajo ya está
-- aplicado en prod (vía migraciones `almacen_estibas_dev` y `politicas_delete_mvp`);
-- se deja aquí para reproducirlo. Idempotente. Si algún día se pasa a
-- `service_role` (Opción A, más abajo), estas políticas se eliminan.
--
-- RLS + lectura/inserción de la tabla nueva de estibas devueltas:
-- alter table almacen_estibas_dev enable row level security;
-- create policy "estibas_lectura_mvp"   on almacen_estibas_dev
--     for select to anon, authenticated using (true);
-- create policy "estibas_insercion_mvp" on almacen_estibas_dev
--     for insert to anon, authenticated with check (true);
--
-- DELETE (corregir movimientos/registros mal digitados desde la app). Las de
-- SELECT/INSERT de las otras tablas ya existían; faltaban las de borrado:
-- create policy "entradas_borrado_mvp" on almacen_entradas
--     for delete to anon, authenticated using (true);
-- create policy "salidas_borrado_mvp" on almacen_salidas
--     for delete to anon, authenticated using (true);
-- create policy "estibas_borrado_mvp" on almacen_estibas_dev
--     for delete to anon, authenticated using (true);
-- create policy "borrado_mvp" on registros_mamposteria
--     for delete to anon, authenticated using (true);


-- ─────────────────────────────────────────────────────────────
-- ENDURECIMIENTO PARA PRODUCCIÓN (datos sensibles) — correr DESPUÉS
-- de cambiar las claves en secrets, en este orden:
--
--   1. Dashboard → Settings → API: copiar la clave `service_role`.
--   2. En secrets:  SUPABASE_KEY      = service_role   (datos, solo servidor)
--                   SUPABASE_ANON_KEY = anon            (solo login)
--   3. Reiniciar la app y verificar que lee/escribe bien.
--   4. RECIÉN ENTONCES descomentar y correr esto — elimina el acceso
--      permisivo de la clave anon a los datos (la service_role omite RLS;
--      RLS queda activado sin políticas = denegar todo a anon):
--
-- drop policy if exists "lectura_mvp"               on registros_mamposteria;
-- drop policy if exists "insercion_mvp"             on registros_mamposteria;
-- drop policy if exists "borrado_mvp"               on registros_mamposteria;
-- drop policy if exists "salidas_lectura_mvp"       on almacen_salidas;
-- drop policy if exists "salidas_insercion_mvp"     on almacen_salidas;
-- drop policy if exists "salidas_borrado_mvp"       on almacen_salidas;
-- drop policy if exists "entradas_lectura_mvp"      on almacen_entradas;
-- drop policy if exists "entradas_insercion_mvp"    on almacen_entradas;
-- drop policy if exists "entradas_borrado_mvp"      on almacen_entradas;
-- drop policy if exists "estibas_lectura_mvp"       on almacen_estibas_dev;
-- drop policy if exists "estibas_insercion_mvp"     on almacen_estibas_dev;
-- drop policy if exists "estibas_borrado_mvp"       on almacen_estibas_dev;
-- drop policy if exists "config_lectura_mvp"        on config_app;
-- drop policy if exists "config_insercion_mvp"      on config_app;
-- drop policy if exists "config_actualizacion_mvp"  on config_app;
--
-- Si se corre ANTES de cambiar la clave, la app deja de ver los datos
-- (la anon quedaría sin ninguna política). Para revertir: re-crear las
-- políticas *_mvp de las secciones de arriba.

-- Con la key `service_role` (Opción A) NO necesitas políticas. Con `anon`
-- (Opción B), habilita RLS y descomenta:
--
-- alter table almacen_salidas enable row level security;
--
-- create policy "salidas_lectura_publica" on almacen_salidas
--     for select using (true);
--
-- create policy "salidas_insercion_publica" on almacen_salidas
--     for insert with check (true);


-- ─────────────────────────────────────────────────────────────
-- ✅ RLS RECOMENDADO PARA PRODUCCIÓN (un solo bloque, copia y corre)
--
-- Postura más segura y simple para este MVP:
--   1) En secrets:  SUPABASE_KEY      = service_role  (DATOS, solo servidor)
--                   SUPABASE_ANON_KEY = anon          (solo el LOGIN)
--   2) Corre ESTE bloque para ACTIVAR RLS en todas las tablas SIN crear
--      políticas. Resultado: la clave `anon` (que es pública) queda SIN
--      acceso a los datos; solo el servidor con `service_role` (que omite
--      RLS) puede leer/escribir. Así, aunque se filtre la anon, no expone
--      ni permite borrar nada.
--
-- ⚠️ NO corras esto si SUPABASE_KEY es la clave `anon`: dejarías la app sin
--    acceso a los datos (RLS sin políticas = denegar todo a anon). En ese
--    caso usa primero las políticas permisivas (Opción B) de arriba.
-- ─────────────────────────────────────────────────────────────
-- alter table registros_mamposteria enable row level security;
-- alter table almacen_salidas       enable row level security;
-- alter table almacen_entradas      enable row level security;
-- alter table almacen_estibas_dev   enable row level security;
-- alter table config_app            enable row level security;
--
-- Verifica que NO queden políticas permisivas que dejen entrar a la anon:
-- select schemaname, tablename, policyname, cmd
--   from pg_policies
--  where schemaname = 'public';

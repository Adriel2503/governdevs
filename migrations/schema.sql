-- ============================================================================
-- context-hub (governdevs) — esquema completo de la base de datos
-- ----------------------------------------------------------------------------
-- Motor: PostgreSQL 17 (imagen ParadeDB pg17, trae pg_search precargado).
-- Diseño: docs/diseno-bd-context-hub.md
--
-- Este archivo crea TODO el esquema de una vez. La demo arranca de 0, así que
-- no usamos migraciones incrementales versionadas: se corre este script una
-- vez sobre una base limpia. Todo es idempotente (IF NOT EXISTS / ON CONFLICT),
-- así que re-ejecutarlo no rompe nada.
--
-- NO migra datos desde SQLite: la wiki se repuebla corriendo /wiki/sync contra
-- los .md de wiki_data/. Solo crea la estructura.
--
-- Orden obligado por las FKs:
--   capas -> lineamientos
--   git_credentials -> repos -> (index_jobs, revisiones)
-- ============================================================================


-- ============================================================================
-- 0. EXTENSIONES
-- ============================================================================
-- pg_search ya viene instalado a nivel del sistema en la imagen paradedb y con
-- shared_preload_libraries configurado; aquí solo se ACTIVA. Da búsqueda
-- full-text con ranking BM25 (el mismo que hoy usamos en SQLite FTS5).
CREATE EXTENSION IF NOT EXISTS pg_search;
-- pgvector queda para v2 (búsqueda semántica híbrida). No cambia la imagen.
-- CREATE EXTENSION IF NOT EXISTS vector;


-- ============================================================================
-- DOMINIO A — WIKI / LINEAMIENTOS
-- ============================================================================

-- capas: catálogo de capas del arquetipo. Alimenta el dropdown del front y da
-- integridad por FK a lineamientos (no se cuela una capa inventada).
CREATE TABLE IF NOT EXISTS capas (
    slug        TEXT PRIMARY KEY,
    nombre      TEXT NOT NULL,
    descripcion TEXT,
    orden       INT  NOT NULL DEFAULT 0
);

INSERT INTO capas (slug, nombre, orden) VALUES
    ('endpoints',         'Endpoints',         1),
    ('ruteo',             'Ruteo',             2),
    ('handlers',          'Handlers',          3),
    ('queries',           'Queries',           4),
    ('validators',        'Validators',        5),
    ('custom-exceptions', 'Custom Exceptions', 6)
ON CONFLICT (slug) DO NOTHING;


-- lineamientos: documentación/normas oficiales. Reemplaza la tabla `reglas` de
-- wiki_index.db (SQLite FTS5). Cada fila es UNA VERSION de un documento; el
-- documento lógico se identifica por ruta_relativa y es_vigente marca la activa.
-- Subir una versión nueva desactiva la anterior (historial, no se borra).
CREATE TABLE IF NOT EXISTS lineamientos (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    capa_slug        TEXT NOT NULL REFERENCES capas(slug),
    ruta_relativa    TEXT NOT NULL,            -- identificador lógico del documento
    titulo           TEXT,
    contenido        TEXT NOT NULL,            -- TEXTO EXTRAÍDO (lo que se busca/sirve)
    formato_original TEXT NOT NULL DEFAULT 'md'
                     CHECK (formato_original IN ('md', 'pdf', 'docx', 'txt')),
    version          INT  NOT NULL DEFAULT 1,
    es_vigente       BOOLEAN NOT NULL DEFAULT true,
    subido_por       TEXT,                     -- nullable hoy; futuro FK -> users
    fuente           TEXT,                     -- de dónde vino (URL repo GitHub o 'wiki_data'); trazabilidad
    creado_en        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Solo puede haber UNA versión vigente por documento.
CREATE UNIQUE INDEX IF NOT EXISTS uq_lineamiento_vigente
    ON lineamientos (ruta_relativa)
    WHERE es_vigente;

-- Filtro por capa (get_regla / dropdown del front).
CREATE INDEX IF NOT EXISTS idx_lineamientos_capa
    ON lineamientos (capa_slug)
    WHERE es_vigente;

-- Índice BM25 de pg_search: el equivalente al ranking FTS5 de hoy, en Postgres.
-- Se consulta con el operador @@@ y se ordena por paradedb.score(id). El filtro
-- por capa/es_vigente va como predicado SQL normal combinado con la búsqueda.
-- (Sintaxis pg_search >= 0.24; ajustar si la versión instalada difiere.)
CREATE INDEX IF NOT EXISTS idx_lineamientos_bm25
    ON lineamientos
    USING bm25 (id, titulo, contenido)
    WITH (key_field = 'id');


-- ============================================================================
-- DOMINIO B — REPOS & GRAFO
-- ============================================================================

-- git_credentials: abstracción de credencial git. Una credencial cubre varios
-- repos. El discriminador `tipo` selecciona la implementación:
--   pat        -> demo, cuenta personal (solo token_cifrado).
--   github_app -> v2 (app_id + installation_id + private_key_cifrada).
-- Los secretos se guardan CIFRADOS a nivel app (clave en env CREDENTIALS_KEY),
-- nunca en texto plano.
CREATE TABLE IF NOT EXISTS git_credentials (
    id                         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tipo                       TEXT NOT NULL CHECK (tipo IN ('pat', 'github_app')),
    alias                      TEXT NOT NULL,   -- "Cuenta de Amado (demo)"
    owner                      TEXT NOT NULL,   -- quién la registró; futuro FK -> users
    github_login               TEXT,            -- dueño en GitHub (ej. amadofrias)
    -- Campos PAT
    token_cifrado              TEXT,
    -- Campos GitHub App (v2)
    app_id                     INTEGER,
    installation_id            TEXT,
    private_key_cifrada        TEXT,
    app_webhook_secret_cifrado TEXT,
    creado_en                  TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Cada tipo exige sus campos mínimos.
    CONSTRAINT chk_credencial_por_tipo CHECK (
        (tipo = 'pat'        AND token_cifrado IS NOT NULL)
        OR
        (tipo = 'github_app' AND app_id IS NOT NULL
                             AND installation_id IS NOT NULL
                             AND private_key_cifrada IS NOT NULL)
    )
);


-- repos: registro de repos + estado de sync. Evoluciona la tabla `repos` de
-- repos.db (SQLite) con lo que el flujo GitHub/webhook necesita.
CREATE TABLE IF NOT EXISTS repos (
    name                   TEXT PRIMARY KEY,        -- slug amigable
    source                 TEXT NOT NULL,           -- URL git o ruta local
    credential_id          UUID REFERENCES git_credentials(id) ON DELETE SET NULL,
    owner                  TEXT NOT NULL,           -- quién lo registró; futuro FK -> users
    local_path             TEXT,                    -- clon PERSISTENTE (git fetch incremental)
    rama                   TEXT NOT NULL DEFAULT 'main',   -- rama canónica vigilada
    watch_paths            TEXT[] NOT NULL DEFAULT '{}',   -- globs; vacío = reindexar ante cualquier cambio
    cbm_project            TEXT,                    -- slug interno del grafo canónico (main)
    webhook_github_id      BIGINT,                  -- id del webhook en GitHub (borrar/actualizar)
    webhook_secret_cifrado TEXT,                    -- verificar x-hub-signature-256
    last_indexed_commit    TEXT,                    -- SHA del último push indexado (incremental)
    last_synced_at         TIMESTAMPTZ,
    status                 TEXT NOT NULL DEFAULT 'registrado'
                           CHECK (status IN ('registrado', 'clonando', 'indexando', 'listo', 'error')),
    error                  TEXT,
    registered_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_repos_credential ON repos (credential_id);


-- ============================================================================
-- DOMINIO C — SYNC (CI/CD)
-- ============================================================================

-- index_jobs: cola persistente + historial de reindexados. Cada push encolado
-- es una fila. La cola in-memory FIFO por repo es el runtime; esta tabla es el
-- registro durable (sobrevive reinicios, da historial para la demo).
CREATE TABLE IF NOT EXISTS index_jobs (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    repo_name     TEXT NOT NULL REFERENCES repos(name) ON DELETE CASCADE,
    commit_sha    TEXT,
    evento        TEXT NOT NULL DEFAULT 'push'
                  CHECK (evento IN ('push', 'manual', 'registro_inicial')),
    estado        TEXT NOT NULL DEFAULT 'encolado'
                  CHECK (estado IN ('encolado', 'corriendo', 'ok', 'error')),
    mensaje       TEXT,
    encolado_en   TIMESTAMPTZ NOT NULL DEFAULT now(),
    iniciado_en   TIMESTAMPTZ,
    finalizado_en TIMESTAMPTZ
);

-- Idempotencia: no reindexar dos veces el mismo commit si GitHub reintenta.
CREATE UNIQUE INDEX IF NOT EXISTS uq_index_job_commit
    ON index_jobs (repo_name, commit_sha)
    WHERE commit_sha IS NOT NULL AND evento = 'push';

CREATE INDEX IF NOT EXISTS idx_index_jobs_repo_estado
    ON index_jobs (repo_name, estado);


-- revisiones: verificación final de rama al abrir/actualizar un PR. Corre
-- detect_changes (enfoque A) + cruce con lineamientos -> veredicto comentado en
-- el PR. NO genera grafo temporal navegable (por eso no hay cbm_project_temp).
CREATE TABLE IF NOT EXISTS revisiones (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    repo_name      TEXT NOT NULL REFERENCES repos(name) ON DELETE CASCADE,
    pr_numero      INT,                        -- número del PR en GitHub
    rama           TEXT NOT NULL,              -- rama origen (head)
    autor          TEXT,                       -- login del autor del PR; futuro FK -> users
    base_commit    TEXT,                       -- SHA de main
    head_commit    TEXT,                       -- SHA de la rama
    estado         TEXT NOT NULL DEFAULT 'generando'
                   CHECK (estado IN ('generando', 'ok', 'advertencias', 'viola', 'error')),
    diff_resumen   JSONB,                      -- cambios + impacto (salida de detect_changes)
    violaciones    JSONB,                      -- reglas de lineamientos incumplidas
    comentario_url TEXT,                       -- URL del comentario dejado en el PR
    creado_en      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Idempotencia (simétrica a uq_index_job_commit): GitHub reintenta la entrega
-- del webhook si tardamos en responder, y un 'synchronize' puede llegar
-- duplicado. Sin esto, cada reintento inserta otra revisión y termina dejando
-- OTRO comentario en el mismo PR. El head_commit es lo que identifica el estado
-- revisado: si el dev pushea de nuevo, el SHA cambia y sí corresponde revisar.
CREATE UNIQUE INDEX IF NOT EXISTS uq_revision_pr_head
    ON revisiones (repo_name, pr_numero, head_commit)
    WHERE head_commit IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_revisiones_repo_pr
    ON revisiones (repo_name, pr_numero);


-- ============================================================================
-- FUTURO (v2, NO activar en la demo) — RBAC
-- ----------------------------------------------------------------------------
-- Cuando haya login multiusuario se agrega esta tabla y se convierten en FK las
-- columnas owner / subido_por / autor. La migración es aditiva.
--
-- CREATE TABLE users (
--     id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
--     email     TEXT NOT NULL UNIQUE,
--     rol       TEXT NOT NULL CHECK (rol IN ('admin', 'dev')),
--     creado_en TIMESTAMPTZ NOT NULL DEFAULT now()
-- );
-- ============================================================================

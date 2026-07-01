#!/bin/sh
set -eu

APP_USER="dsa"
APP_GROUP="dsa"
APP_UID="1000"
APP_GID="1000"
WRITABLE_DIRS="/app/data /app/logs /app/reports"
DATABASE_FILE="${DATABASE_PATH:-/app/data/stock_analysis.db}"
MIGRATION_LOCK_DIR="/app/data/.dsa-startup-migration.lock"
MIGRATION_LOCK_TIMEOUT_SECONDS=300

warn() {
    printf '%s\n' "$*" >&2
}

run_as_app_user() {
    if [ "$(id -u)" = "0" ]; then
        gosu "$APP_USER:$APP_GROUP" "$@"
    else
        "$@"
    fi
}

can_write_dir_as_app_user() {
    gosu "$APP_USER:$APP_GROUP" sh -c '
        tmp="$1/.dsa-write-check.$$"
        : > "$tmp" && rm -f "$tmp"
    ' sh "$1"
}

can_write_file_as_app_user() {
    gosu "$APP_USER:$APP_GROUP" test -w "$1"
}

has_unwritable_mount_path() {
    dir="$1"

    if ! can_write_dir_as_app_user "$dir"; then
        return 0
    fi

    if [ "$dir" = "/app/data" ]; then
        for file in "$DATABASE_FILE" "$DATABASE_FILE-wal" "$DATABASE_FILE-shm"; do
            if [ -e "$file" ] && ! can_write_file_as_app_user "$file"; then
                return 0
            fi
        done
    fi

    return 1
}

directory_needs_repair() {
    dir="$1"

    if has_unwritable_mount_path "$dir"; then
        return 0
    fi

    mismatched_path="$(
        find "$dir" \
            \( ! -user "$APP_UID" -o ! -group "$APP_GID" \) \
            -print -quit 2>/dev/null || true
    )"
    if [ -n "$mismatched_path" ]; then
        return 0
    fi

    return 1
}

should_run_startup_migrations() {
    command_name="${1:-}"
    command_name="${command_name##*/}"

    case "$command_name" in
        python|python3)
            if [ "${2:-}" = "backend/main.py" ]; then
                return 0
            fi
            if [ "${2:-}" = "-m" ] && [ "${3:-}" = "uvicorn" ]; then
                return 0
            fi
            ;;
        uvicorn|gunicorn)
            return 0
            ;;
    esac

    return 1
}

run_startup_migrations() {
    if ! should_run_startup_migrations "$@"; then
        return 0
    fi

    elapsed=0
    while ! mkdir "$MIGRATION_LOCK_DIR" 2>/dev/null; do
        if [ "$elapsed" -ge "$MIGRATION_LOCK_TIMEOUT_SECONDS" ]; then
            warn "ERROR: timed out waiting for database migration lock: $MIGRATION_LOCK_DIR"
            return 1
        fi
        warn "Waiting for database migration lock: $MIGRATION_LOCK_DIR"
        sleep 2
        elapsed=$((elapsed + 2))
    done

    migration_lock_acquired=1
    trap 'if [ "${migration_lock_acquired:-0}" = "1" ]; then rmdir "$MIGRATION_LOCK_DIR" 2>/dev/null || true; fi' EXIT INT TERM

    warn "Running database migrations before application startup..."
    run_as_app_user alembic upgrade head
    warn "Database migrations completed."

    rmdir "$MIGRATION_LOCK_DIR" 2>/dev/null || true
    migration_lock_acquired=0
    trap - EXIT INT TERM
}

if [ "$(id -u)" = "0" ]; then
    for dir in $WRITABLE_DIRS; do
        if ! mkdir -p "$dir"; then
            warn "WARN: unable to create $dir; application writes may fail for this path."
            continue
        fi

        if ! directory_needs_repair "$dir"; then
            continue
        fi

        if chown -R "$APP_UID:$APP_GID" "$dir"; then
            if ! chmod -R u+rwX "$dir"; then
                warn "WARN: unable to adjust owner permissions for $dir after ownership repair; check read-only, rootless, or NFS mount permissions if writes fail."
            fi
        else
            warn "WARN: unable to set ownership for $dir; skipping owner-only chmod because it would not grant writes to $APP_USER without ownership."
        fi

        if has_unwritable_mount_path "$dir"; then
            warn "WARN: $dir is still not writable by $APP_USER after permission repair; check host mount ownership or read-only, rootless, and NFS mount settings."
        fi
    done

    run_startup_migrations "$@"
    exec gosu "$APP_USER:$APP_GROUP" "$@"
fi

run_startup_migrations "$@"
exec "$@"

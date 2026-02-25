#!/bin/bash
# Health Tracker - Database Initialization Script
# Usage: ./database/init-db.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
ENV_FILE="$PROJECT_ROOT/.env"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo "========================================="
echo "  Health Tracker - Database Init"
echo "========================================="

# Load .env file
if [ -f "$ENV_FILE" ]; then
    echo -e "${GREEN}[OK]${NC} Found .env file"
    while IFS= read -r line || [ -n "$line" ]; do
        # Skip comments and empty lines
        [[ "$line" =~ ^#.*$ ]] && continue
        [[ -z "$line" ]] && continue
        # Export each VAR=VALUE line
        if [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]]; then
            key="${BASH_REMATCH[1]}"
            value="${BASH_REMATCH[2]}"
            # Strip surrounding quotes if present
            value="${value%\"}"
            value="${value#\"}"
            value="${value%\'}"
            value="${value#\'}"
            export "$key=$value"
        fi
    done < "$ENV_FILE"
else
    echo -e "${RED}[ERROR]${NC} .env file not found at $ENV_FILE"
    echo "  Copy .env.example to .env and fill in your values:"
    echo "  cp .env.example .env"
    exit 1
fi

# Parse DATABASE_URL
if [ -z "$DATABASE_URL" ]; then
    echo -e "${RED}[ERROR]${NC} DATABASE_URL not set in .env"
    exit 1
fi

echo -e "${GREEN}[OK]${NC} DATABASE_URL found"

# Extract components from DATABASE_URL
# Format: postgresql://user:password@host:port/dbname
DB_USER=$(echo "$DATABASE_URL" | sed -n 's|postgresql://\([^:]*\):.*|\1|p')
DB_PASS=$(echo "$DATABASE_URL" | sed -n 's|postgresql://[^:]*:\([^@]*\)@.*|\1|p')
DB_HOST=$(echo "$DATABASE_URL" | sed -n 's|postgresql://[^@]*@\([^:]*\):.*|\1|p')
DB_PORT=$(echo "$DATABASE_URL" | sed -n 's|postgresql://[^@]*@[^:]*:\([^/]*\)/.*|\1|p')
DB_NAME=$(echo "$DATABASE_URL" | sed -n 's|postgresql://[^/]*/\(.*\)|\1|p')

echo ""
echo "Connection details:"
echo "  Host:     $DB_HOST"
echo "  Port:     $DB_PORT"
echo "  Database: $DB_NAME"
echo "  User:     $DB_USER"
echo ""

# Determine psql command - use native psql or fall back to Docker
PSQL_CMD=""
if command -v psql &> /dev/null; then
    PSQL_CMD="psql"
    echo -e "${GREEN}[OK]${NC} Using native psql"
elif command -v docker &> /dev/null; then
    echo -e "${YELLOW}[INFO]${NC} psql not found, using Docker postgres:15 image"
    # Pull image if not present
    docker pull postgres:15 -q 2>/dev/null || true
    PSQL_CMD="docker run --rm -i -e PGPASSWORD=$DB_PASS postgres:15 psql"
    # For docker, we set PGPASSWORD inside the container via -e flag
else
    echo -e "${RED}[ERROR]${NC} Neither psql nor Docker found. Install one of:"
    echo "  brew install libpq     # for psql"
    echo "  brew install --cask docker  # for Docker"
    exit 1
fi

# Helper function to run psql commands
run_psql() {
    if command -v psql &> /dev/null; then
        PGPASSWORD="$DB_PASS" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" "$@"
    else
        docker run --rm -i \
            -e PGPASSWORD="$DB_PASS" \
            postgres:15 \
            psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" "$@"
    fi
}

# Helper function to run psql with file input (needs volume mount for Docker)
run_psql_file() {
    local file="$1"
    if command -v psql &> /dev/null; then
        PGPASSWORD="$DB_PASS" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -f "$file"
    else
        docker run --rm -i \
            -e PGPASSWORD="$DB_PASS" \
            -v "$file:/tmp/migration.sql:ro" \
            postgres:15 \
            psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -f /tmp/migration.sql
    fi
}

# Test connection
echo -e "${YELLOW}[...]${NC} Testing database connection..."
if run_psql -c "SELECT 1;" > /dev/null 2>&1; then
    echo -e "${GREEN}[OK]${NC} Database connection successful"
else
    echo -e "${RED}[ERROR]${NC} Cannot connect to database"
    echo ""
    echo "  Troubleshooting:"
    echo "  1. Check if PostgreSQL is running on $DB_HOST:$DB_PORT"
    echo "  2. Verify credentials in .env"
    echo "  3. Check if database '$DB_NAME' exists"
    echo "  4. Check firewall/network access"
    exit 1
fi

# Run migrations in order
for MIGRATION_FILE in "$SCRIPT_DIR"/migrations/*.sql; do
    MIGRATION_NAME=$(basename "$MIGRATION_FILE")

    if [ ! -f "$MIGRATION_FILE" ]; then
        continue
    fi

    echo -e "${YELLOW}[...]${NC} Running migration: $MIGRATION_NAME..."
    if run_psql_file "$MIGRATION_FILE" 2>&1; then
        echo -e "${GREEN}[OK]${NC} $MIGRATION_NAME applied"
    else
        echo -e "${RED}[ERROR]${NC} $MIGRATION_NAME failed"
        exit 1
    fi
    echo ""
done

# Verify tables
echo ""
echo -e "${YELLOW}[...]${NC} Verifying tables..."
TABLE_COUNT=$(run_psql -t -c "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public' AND table_type = 'BASE TABLE';")
TABLE_COUNT=$(echo "$TABLE_COUNT" | xargs)

echo -e "${GREEN}[OK]${NC} Found $TABLE_COUNT tables:"
run_psql -t -c "SELECT '  - ' || table_name FROM information_schema.tables WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY table_name;"

echo ""
echo "========================================="
echo -e "  ${GREEN}Database initialized successfully!${NC}"
echo "========================================="
echo ""
echo "Next steps:"
echo "  1. Configure environment variables in .env"
echo "  2. Set WHOOP env vars: WHOOP_CLIENT_ID, WHOOP_CLIENT_SECRET, WHOOP_REDIRECT_URI"
echo "  3. Start the app: uvicorn app.main:app"

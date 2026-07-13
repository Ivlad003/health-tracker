import os
import shutil
import subprocess
from pathlib import Path


def test_init_db_skips_rollback_migrations(tmp_path):
    project_root = tmp_path / "health-tracker"
    database_dir = project_root / "database"
    migrations_dir = database_dir / "migrations"
    bin_dir = tmp_path / "bin"
    migrations_dir.mkdir(parents=True)
    bin_dir.mkdir()

    source_script = Path(__file__).parents[1] / "database" / "init-db.sh"
    init_script = database_dir / "init-db.sh"
    shutil.copy2(source_script, init_script)
    (project_root / ".env").write_text(
        "DATABASE_URL=postgresql://test:test@localhost:5432/test\n",
        encoding="utf-8",
    )

    (migrations_dir / "001_forward.sql").write_text("SELECT 1;\n", encoding="utf-8")
    (migrations_dir / "001_forward_rollback.sql").write_text(
        "SELECT 'rollback';\n", encoding="utf-8"
    )
    (migrations_dir / "002_forward.sql").write_text("SELECT 2;\n", encoding="utf-8")

    psql_log = tmp_path / "psql.log"
    fake_psql = bin_dir / "psql"
    fake_psql.write_text(
        "#!/bin/bash\n"
        "printf '%s\\n' \"$*\" >> \"$PSQL_LOG\"\n"
        "if [[ \"$*\" == *\"COUNT(*)\"* ]]; then printf '0\\n'; fi\n",
        encoding="utf-8",
    )
    fake_psql.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["PSQL_LOG"] = str(psql_log)
    result = subprocess.run(
        ["bash", str(init_script)],
        capture_output=True,
        check=False,
        env=env,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "Running migration: 001_forward.sql" in result.stdout
    assert "Running migration: 002_forward.sql" in result.stdout
    assert "001_forward_rollback.sql" not in result.stdout
    psql_calls = psql_log.read_text(encoding="utf-8").splitlines()
    assert "001_forward_rollback.sql" not in "\n".join(psql_calls)
    migration_calls = [call for call in psql_calls if " -f " in f" {call} "]
    assert migration_calls
    assert all("-v ON_ERROR_STOP=1" in call for call in migration_calls)

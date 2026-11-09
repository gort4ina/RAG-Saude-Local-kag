"""scripts/gen_env.py: segredos novos, sem sobrescrever e aceitos pelo Settings."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from app.config import Settings

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "gen_env.py"
_spec = importlib.util.spec_from_file_location("gen_env", _SCRIPT)
gen_env = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(gen_env)


def _parse(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def test_render_replaces_every_secret_and_the_database_url() -> None:
    template = (_SCRIPT.parent.parent / ".env.example").read_text(encoding="utf-8")
    content, generated = gen_env.render_env(template)
    values = _parse(content)

    assert set(generated) == set(gen_env.GENERATED)
    assert len(values["JWT_SECRET"]) >= 32
    assert len(values["BOOTSTRAP_ADMIN_PASSWORD"]) >= 12
    # a senha do banco aparece na URL local e precisa ser a mesma
    assert values["POSTGRES_PASSWORD"] in values["DATABASE_URL"]
    # nenhum valor de exemplo sobrou
    assert "troque-por" not in content


def test_every_run_generates_different_secrets() -> None:
    template = (_SCRIPT.parent.parent / ".env.example").read_text(encoding="utf-8")
    first = _parse(gen_env.render_env(template)[0])
    second = _parse(gen_env.render_env(template)[0])
    assert first["JWT_SECRET"] != second["JWT_SECRET"]
    assert first["POSTGRES_PASSWORD"] != second["POSTGRES_PASSWORD"]


def test_refuses_to_overwrite_existing_env(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / ".env"
    target.write_text("JWT_SECRET=nao-encosta\n", encoding="utf-8")
    # --output relativo a ROOT; caminho absoluto sobrepoe a juncao
    code = gen_env.main(["--output", str(target)])
    assert code == 1
    assert target.read_text(encoding="utf-8") == "JWT_SECRET=nao-encosta\n"
    assert "ja existe" in capsys.readouterr().err


def test_never_prints_the_secrets(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "gerado.env"
    assert gen_env.main(["--output", str(target)]) == 0
    values = _parse(target.read_text(encoding="utf-8"))
    out = capsys.readouterr().out
    for key in gen_env.GENERATED:
        assert values[key] not in out


def test_generated_production_env_is_accepted_by_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """O .env de producao gerado passa nas regras de producao sem edicao manual
    dos segredos (os dominios de exemplo sao validos para o Settings)."""
    target = tmp_path / "prod.env"
    assert gen_env.main(["--production", "--output", str(target)]) == 0
    env = _parse(target.read_text(encoding="utf-8"))
    env["DATABASE_URL"] = (
        f"postgresql+asyncpg://rag:{env['POSTGRES_PASSWORD']}@postgres:5432/rag"
    )
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    settings = Settings()
    assert settings.is_production
    assert settings.jwt_secret == env["JWT_SECRET"]

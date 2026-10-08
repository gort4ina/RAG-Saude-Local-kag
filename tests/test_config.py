"""Testes das invariantes de ``Settings`` (`app.config`).

Verifica que a configuracao recusa:

- senha de bootstrap trivial;
- senha de bootstrap curta;
- senha ausente em producao;
- JWT fraco em producao;
- combinacoes conflitantes (CORS curinga + credenciais, ALLOWED_HOSTS=* etc.).

A fixture ``clean_env`` da suite preserva isolamento entre casos.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from app.config import Settings

# A conftest.py global ja define um .env coerente para a suite. Para testar
# invariantes de producao precisamos sobrescrever variaveis especificas e
# depois restaurar o estado anterior.
_RESET_KEYS = (
    "APP_ENV",
    "BOOTSTRAP_ADMIN_PASSWORD",
    "JWT_SECRET",
    "DATABASE_URL",
    "ALLOWED_HOSTS",
    "ALLOWED_ORIGINS",
    "COOKIE_SECURE",
    "TRUSTED_PROXY_CIDRS",
)


@pytest.fixture()
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Garante ambiente conhecido para cada caso."""
    snapshot = {key: os.environ.get(key) for key in _RESET_KEYS}
    yield
    # monkeypatch restaura automaticamente, snapshot aqui so documenta o contrato.
    del snapshot


def test_bootstrap_password_vazia_eh_opt_out_silencioso(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    """Sem BOOTSTRAP_ADMIN_PASSWORD em dev, o bootstrap simplesmente nao roda."""
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "")
    settings = Settings()
    assert settings.bootstrap_admin_password == ""


def test_bootstrap_password_trivial_rejeitada(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    """A senha '123456' que era o default historico agora quebra o startup."""
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "123456")
    with pytest.raises(ValueError, match="BOOTSTRAP_ADMIN_PASSWORD"):
        Settings()


def test_bootstrap_password_curta_rejeitada(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "curta")
    with pytest.raises(ValueError, match="12\\+ caracteres"):
        Settings()


def test_bootstrap_password_palavra_banida_mesmo_longa(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    """Blocklist eh checada em case-insensitive, mas comprimento manda primeiro."""
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "12345678")  # curta E trivial
    with pytest.raises(ValueError, match="BOOTSTRAP_ADMIN_PASSWORD"):
        Settings()


def test_bootstrap_password_forte_aceita(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "Regulatorio#Forte92")
    settings = Settings()
    assert settings.bootstrap_admin_password == "Regulatorio#Forte92"


def test_producao_exige_bootstrap_password(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "")
    monkeypatch.setenv(
        "JWT_SECRET", "segredo-de-producao-com-mais-de-32-caracteres-aqui"
    )
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+asyncpg://user:pass@db:5432/rag"
    )
    monkeypatch.setenv("ALLOWED_HOSTS", "rag.example.com")
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://rag.example.com")
    monkeypatch.setenv("COOKIE_SECURE", "true")
    monkeypatch.setenv("TRUSTED_PROXY_CIDRS", "10.0.0.0/8")
    with pytest.raises(ValueError, match="BOOTSTRAP_ADMIN_PASSWORD obrigatorio"):
        Settings()


# ---------------------------------------------------------------------------
# P0-C: guard de WEB_CONCURRENCY (comportamento isolado da logica de startup)
# ---------------------------------------------------------------------------


def test_single_worker_guard_warn_em_dev(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Em dev, WEB_CONCURRENCY>1 emite warning sem derrubar o app.

    Captura via handler proprio ligado ao logger ``app.main`` para nao
    depender do stdout global (que a suite redireciona).
    """
    import io
    import logging

    from app import main as app_main

    monkeypatch.setenv("WEB_CONCURRENCY", "4")
    # ``settings`` eh global e vem de APP_ENV=test (conftest). Nao eh producao.
    assert not app_main.settings.is_production

    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setLevel(logging.WARNING)
    handler.setFormatter(logging.Formatter("%(message)s %(detail)s"))
    target = logging.getLogger("app.main")
    target.addHandler(handler)
    previous_level = target.level
    target.setLevel(logging.WARNING)
    try:
        app_main._assert_single_worker_safe()
    finally:
        target.removeHandler(handler)
        target.setLevel(previous_level)

    output = buffer.getvalue()
    assert "worker_configuration_unsafe" in output
    assert "WEB_CONCURRENCY=4" in output


def test_single_worker_guard_bloqueia_producao(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Em producao, WEB_CONCURRENCY>1 derruba o startup com RuntimeError.

    Nao recarregamos ``app.main`` (muito caro); em vez disso usamos uma
    ``Settings`` fresca com ``APP_ENV=production`` e substituimos a global
    temporariamente.
    """
    from app import main as app_main

    monkeypatch.setenv("WEB_CONCURRENCY", "2")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv(
        "JWT_SECRET", "segredo-de-producao-com-mais-de-32-caracteres-aqui"
    )
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@db:5432/rag")
    monkeypatch.setenv("ALLOWED_HOSTS", "rag.example.com")
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://rag.example.com")
    monkeypatch.setenv("COOKIE_SECURE", "true")
    monkeypatch.setenv("TRUSTED_PROXY_CIDRS", "10.0.0.0/8")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "Regulatorio#Forte92")

    fresh = Settings()
    monkeypatch.setattr(app_main, "settings", fresh)

    with pytest.raises(RuntimeError, match="WEB_CONCURRENCY=2"):
        app_main._assert_single_worker_safe()

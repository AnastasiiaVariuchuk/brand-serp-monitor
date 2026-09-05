import pytest

from brand_serp.cli import build_parser, warn_on_incoherent_sources

MISMATCHED = [("fixture", "http"), ("api", "fixture")]
COHERENT = [("fixture", "fixture"), ("api", "http"),
            ("fixture", "none"), ("api", "none")]


@pytest.mark.parametrize("source,evidence", MISMATCHED)
def test_mismatched_sources_warn_on_stderr(source, evidence, capsys):
    message = warn_on_incoherent_sources(source, evidence)
    assert message is not None
    err = capsys.readouterr().err
    assert err.startswith("[warn]")
    assert message in err


@pytest.mark.parametrize("source,evidence", COHERENT)
def test_coherent_sources_stay_quiet(source, evidence, capsys):
    assert warn_on_incoherent_sources(source, evidence) is None
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("source,evidence", MISMATCHED)
def test_force_silences_the_warning(source, evidence, capsys):
    assert warn_on_incoherent_sources(source, evidence, force=True) is None
    assert capsys.readouterr().err == ""


def test_warning_names_the_flag_to_change_and_how_to_silence_it(capsys):
    message = warn_on_incoherent_sources("fixture", "http")
    capsys.readouterr()
    assert "--evidence fixture" in message      # the coherent alternative
    assert "--source api" in message
    assert "--force" in message


def test_live_serp_with_stale_snapshots_names_its_own_alternatives(capsys):
    message = warn_on_incoherent_sources("api", "fixture")
    capsys.readouterr()
    assert "--evidence http" in message
    assert "--source fixture" in message


def test_the_warning_never_aborts_or_rewrites_flags(capsys):
    """It returns a string and prints — it must not raise or exit."""
    for source, evidence in MISMATCHED:
        assert isinstance(warn_on_incoherent_sources(source, evidence), str)
    capsys.readouterr()


# ------------------------------------------------------------------- parsing
def test_run_accepts_force_and_defaults_it_to_false():
    parser = build_parser()
    assert parser.parse_args(["run"]).force is False
    assert parser.parse_args(["run", "--force"]).force is True


def test_force_does_not_leak_into_the_other_subcommands():
    parser = build_parser()
    for command in ("history", "diff", "serve"):
        assert not hasattr(parser.parse_args([command]), "force")

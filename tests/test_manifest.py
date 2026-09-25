import json
from pathlib import Path

from company_reach.manifest import finish_manifest, manifest_path, start_manifest
from company_reach.settings import Settings
from company_reach.tools import llm

RUN = "r1"


def read(settings: Settings) -> dict:
    return json.loads(manifest_path(RUN, settings=settings).read_text())


def start(settings: Settings):
    return start_manifest(RUN, settings=settings, goal="make and sell", seed=7)


def test_written_at_start_as_running(settings: Settings):
    start(settings)
    m = read(settings)
    assert m["status"] == "running"
    assert m["run_id"] == RUN
    assert m["goal"] == "make and sell"
    assert m["seed"] == 7
    assert m["started_at"]


def test_the_path_is_under_the_run_directory(settings: Settings):
    path = start(settings)
    assert path == settings.data_dir / "runs" / RUN / "manifest.json"
    assert path.is_file()


def test_prompts_carry_a_version_and_a_sha(settings: Settings):
    """The version says which prompt; the sha says whether it was edited
    without the version being raised."""
    start(settings)
    prompts = read(settings)["prompts"]
    assert "score" in prompts
    assert prompts["score"]["version"]
    assert len(prompts["score"]["sha256"]) == 64


def test_the_settings_that_shaped_the_run_are_recorded(settings: Settings):
    start(settings)
    recorded = read(settings)["settings"]
    assert recorded["batch_size"] == settings.batch_size
    assert recorded["draw_min_score"] == settings.draw_min_score
    assert recorded["max_batches_per_run"] == settings.max_batches_per_run
    assert recorded["llm_model"] == settings.llm_model


def test_the_api_key_never_reaches_the_file(settings: Settings):
    """The whole file is searched, not one field: a manifest is written to
    disk and read by a human, and a leaked key would be there for ever."""
    start(settings)
    text = manifest_path(RUN, settings=settings).read_text()
    assert settings.llm_api_key.get_secret_value() not in text
    assert "test-key" not in text


def test_finish_keeps_what_start_wrote(settings: Settings):
    start(settings)
    finish_manifest(
        RUN, settings=settings, status="done", counts={"sendable": 2, "results": 10}
    )
    m = read(settings)
    assert m["goal"] == "make and sell"  # still there
    assert m["status"] == "done"
    assert m["counts"]["sendable"] == 2
    assert m["finished_at"]


def test_a_failed_run_says_so(settings: Settings):
    start(settings)
    finish_manifest(RUN, settings=settings, status="failed", counts={})
    assert read(settings)["status"] == "failed"


def test_the_git_commit_is_recorded_or_explicitly_absent(settings: Settings):
    start(settings)
    m = read(settings)
    assert "git_commit" in m  # None outside a repository, never missing


def test_a_failed_run_records_its_reason(settings: Settings):
    start_manifest("r1", settings=settings, goal="g", seed=0)
    finish_manifest(
        "r1", settings=settings, status="failed", counts={}, reason="SearchError: down"
    )
    data = json.loads(manifest_path("r1", settings=settings).read_text())
    assert data["reason"] == "SearchError: down"


def test_no_dead_prompt_is_listed(settings: Settings):
    """Audit: three prompt files no code loads (translate, translate_tr,
    german_probe) were shipped and listed in every manifest, and a broken
    header in one of them stopped every run. The manifest lists the prompts
    the code uses, and the package ships no other."""
    start(settings)
    listed = set(read(settings)["prompts"])
    shipped = {
        p.name.removesuffix(".md")
        for p in llm.PROMPT_DIR.iterdir()
        if p.name.endswith(".md")
    }
    assert listed == set(llm.PROMPTS) == shipped
    assert {"translate", "translate_tr", "german_probe"}.isdisjoint(listed)


def test_every_listed_prompt_is_loaded_somewhere():
    """The other direction: a name in llm.PROMPTS that no module asks for
    would be a dead prompt again, only listed by hand."""
    sources = [
        path.read_text(encoding="utf-8")
        for path in Path(llm.__file__).parents[1].rglob("*.py")
        if path.name != "llm.py"
    ]
    for name in llm.PROMPTS:
        assert any(f'"{name}"' in source for source in sources), name


def test_a_successful_resume_clears_the_old_reason(settings: Settings):
    start_manifest("r1", settings=settings, goal="g", seed=0)
    finish_manifest("r1", settings=settings, status="failed", counts={}, reason="x")
    finish_manifest("r1", settings=settings, status="done", counts={})
    data = json.loads(manifest_path("r1", settings=settings).read_text())
    assert data.get("reason") is None

"""Tests for scripts/run.py, which run.bat and run.sh run."""

import importlib.util
import io
import sys
import tarfile
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
# run.py imports its helpers from bootstrap.py next to it, as it does when run as a script.
sys.path.insert(0, str(_SCRIPTS))
_SPEC = importlib.util.spec_from_file_location("run_script", _SCRIPTS / "run.py")
assert _SPEC is not None and _SPEC.loader is not None
run_script = importlib.util.module_from_spec(_SPEC)
sys.modules["run_script"] = run_script
_SPEC.loader.exec_module(run_script)
# The helpers that run.py imports, loaded from bootstrap.py next to it.
bootstrap = sys.modules["bootstrap"]

STACK = """\
services:
  bot:
    build: .
  media:
    build: ../api-media
    environment:
      API_SECRET: ${API_MEDIA_SECRET:?Set API_MEDIA_SECRET in .env}
"""


def test_no_action_means_start() -> None:
    assert run_script.parse_args([]) == "start"
    assert run_script.parse_args(["stop"]) == "stop"


@pytest.mark.parametrize("argv", [["help"], ["--help"]])
def test_help_lists_the_actions(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        run_script.parse_args(argv)

    assert exit_info.value.code == 0
    assert "update" in capsys.readouterr().out


def test_unknown_action_is_rejected() -> None:
    with pytest.raises(SystemExit) as exit_info:
        run_script.parse_args(["launch"])

    assert exit_info.value.code == 2


@pytest.mark.parametrize(
    "output",
    [
        '{"Service":"bot","ID":"abc","State":"running","Health":""}\n'
        '{"Service":"media","ID":"def","State":"running","Health":"healthy"}\n',
        '[{"Service":"bot","ID":"abc","State":"running","Health":""},'
        '{"Service":"media","ID":"def","State":"running","Health":"healthy"}]',
    ],
    ids=["one-object-per-line", "array"],
)
def test_parse_ps_reads_both_compose_formats(output: str) -> None:
    states = run_script.parse_ps(output)

    assert [(s.service, s.container, s.health) for s in states] == [
        ("bot", "abc", ""),
        ("media", "def", "healthy"),
    ]


def test_parse_ps_without_containers() -> None:
    assert run_script.parse_ps("") == []


@pytest.mark.parametrize(
    ("state", "health", "ready", "failed"),
    [
        ("running", "", True, False),
        ("running", "healthy", True, False),
        ("running", "starting", False, False),
        ("created", "", False, False),
        ("running", "unhealthy", False, True),
        ("restarting", "", False, True),
        ("exited", "", False, True),
    ],
)
def test_service_state(state: str, health: str, ready: bool, failed: bool) -> None:
    service = run_script.ServiceState("bot", "abc", state, health)

    assert service.ready is ready
    assert service.failed is failed


@pytest.mark.parametrize(
    ("compose", "port"),
    [
        ('    ports:\n      - "8001:8000"\n', 8001),
        ("    ports:\n      - 8004:8000\n", 8004),
        ("services:\n  api:\n    build: .\n", 8000),
    ],
)
def test_host_port(compose: str, port: int) -> None:
    assert run_script.host_port(compose) == port


def make_bot(root: Path, env: str) -> Path:
    bot = root / "discord-bot-x"
    bot.mkdir()
    (bot / "bot.py").write_text("", encoding="utf-8")
    (bot / "compose.stack.yml").write_text(STACK, encoding="utf-8")
    (bot / ".env").write_text(env, encoding="utf-8")
    return bot


def test_detect_project_picks_the_stack_for_a_bot(tmp_path: Path) -> None:
    project = run_script.detect_project(make_bot(tmp_path, ""))

    assert project.is_bot
    assert project.compose == ["docker", "compose", "-f", "compose.stack.yml"]


def test_detect_project_picks_docker_compose_for_an_api(tmp_path: Path) -> None:
    (tmp_path / "src" / "media_api").mkdir(parents=True)
    (tmp_path / "src" / "media_api" / "main.py").write_text("", encoding="utf-8")
    (tmp_path / "docker-compose.yml").write_text(
        'ports:\n  - "8001:8000"\n', encoding="utf-8"
    )

    project = run_script.detect_project(tmp_path)

    assert not project.is_bot
    assert project.compose_file == "docker-compose.yml"
    assert run_script.api_package(tmp_path) == "media_api"


def test_check_setup_without_env(tmp_path: Path) -> None:
    bot = make_bot(tmp_path, "")
    (bot / ".env").unlink()
    report = run_script.Report()

    assert not run_script.check_setup(run_script.detect_project(bot), report)
    assert ".env is missing" in report.problems[0].what


def test_check_setup_lists_every_missing_piece(tmp_path: Path) -> None:
    bot = make_bot(tmp_path, "DISCORD_TOKEN=your-token-here\n")
    (bot / ".git").mkdir()
    report = run_script.Report()

    assert not run_script.check_setup(run_script.detect_project(bot), report)
    assert [p.what.split()[0] for p in report.problems] == [
        "DISCORD_TOKEN",
        "API_MEDIA_SECRET",
        "api-media",
    ]


def test_downloaded_bot_needs_no_service_folders(tmp_path: Path) -> None:
    bot = make_bot(tmp_path, "DISCORD_TOKEN=a.b.c\nAPI_MEDIA_SECRET=generated\n")
    report = run_script.Report()

    project = run_script.detect_project(bot)

    assert project.published
    assert run_script.check_setup(project, report)
    assert report.problems == []


def test_check_setup_passes_when_everything_is_in_place(tmp_path: Path) -> None:
    bot = make_bot(tmp_path, "DISCORD_TOKEN=a.b.c\nAPI_MEDIA_SECRET=generated\n")
    (tmp_path / "api-media").mkdir()
    report = run_script.Report()

    assert run_script.check_setup(run_script.detect_project(bot), report)
    assert report.problems == []


def test_update_refuses_a_downloaded_copy_before_pulling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bot = make_bot(tmp_path, "DISCORD_TOKEN=a.b.c\nAPI_MEDIA_SECRET=generated\n")
    (bot / ".git").mkdir()
    (tmp_path / "api-media").mkdir()
    commands: list[list[str]] = []
    monkeypatch.setattr(
        run_script, "run", lambda command: commands.append(command) or True
    )
    report = run_script.Report()

    assert not run_script.update(run_script.detect_project(bot), report)
    assert "api-media is a downloaded copy" in report.problems[0].what
    assert commands == []


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("run.bat", True),
        ("scripts/run.py", True),
        ("compose.stack.yml", True),
        (".env", False),
        ("cogs/media.py", False),
        ("scripts/../.env", False),
    ],
)
def test_is_release_file(name: str, expected: bool) -> None:
    assert run_script.is_release_file(name) is expected


def archive(files: dict[str, str]) -> bytes:
    """Packs files into a tar archive, as read_release_files returns it."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


NEW_RELEASE = archive(
    {
        "run.bat": "new run.bat",
        "run.sh": "same run.sh",
        "scripts/run.py": "new run.py",
        "scripts/added.py": "added",
        ".env": "DISCORD_TOKEN=stolen",
    }
)


def write_release(root: Path) -> None:
    """Gives a project folder the release files of the running version."""
    (root / "scripts").mkdir()
    (root / "run.bat").write_text("old run.bat", encoding="utf-8")
    (root / "run.sh").write_text("same run.sh", encoding="utf-8")
    (root / "scripts" / "run.py").write_text("old run.py", encoding="utf-8")


def test_release_files_are_replaced_and_put_back(tmp_path: Path) -> None:
    write_release(tmp_path)
    (tmp_path / ".env").write_text("DISCORD_TOKEN=mine", encoding="utf-8")

    backup = run_script.replace_release_files(NEW_RELEASE, tmp_path)

    assert (tmp_path / "run.bat").read_text(encoding="utf-8") == "new run.bat"
    assert (tmp_path / "scripts" / "added.py").is_file()
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "DISCORD_TOKEN=mine"
    assert sorted(backup.saved) == ["run.bat", "scripts/run.py"]

    run_script.restore_release_files(backup, tmp_path)

    assert (tmp_path / "run.bat").read_text(encoding="utf-8") == "old run.bat"
    assert (tmp_path / "scripts" / "run.py").read_text(encoding="utf-8") == "old run.py"
    assert not (tmp_path / "scripts" / "added.py").exists()


def test_update_state_survives_a_damaged_file(tmp_path: Path) -> None:
    path = run_script.UpdateState.path(tmp_path)
    path.parent.mkdir()
    path.write_text("{not json", encoding="utf-8")

    assert run_script.UpdateState.load(tmp_path).broken == set()

    run_script.UpdateState(broken={"sha256:bad"}).save(tmp_path)

    assert run_script.UpdateState.load(tmp_path).broken == {"sha256:bad"}


def test_schedule_refuses_a_git_clone(tmp_path: Path) -> None:
    bot = make_bot(tmp_path, "")
    (bot / ".git").mkdir()
    report = run_script.Report()

    assert not run_script.schedule(run_script.detect_project(bot), report)
    assert "Git clone" in report.problems[0].what


BOT_IMAGE = "ghcr.io/lempki/discord-bot-x:latest"
MEDIA_IMAGE = "ghcr.io/lempki/api-media:latest"
OLD_IMAGES = {BOT_IMAGE: "sha256:bot-old", MEDIA_IMAGE: "sha256:media-old"}


@pytest.mark.parametrize(
    ("ref", "previous"),
    [
        (BOT_IMAGE, "ghcr.io/lempki/discord-bot-x:previous-bot"),
        (
            "localhost:5000/lempki/api-media",
            "localhost:5000/lempki/api-media:previous-bot",
        ),
        (
            "localhost:5000/lempki/api-media:latest",
            "localhost:5000/lempki/api-media:previous-bot",
        ),
    ],
)
def test_previous_ref(ref: str, previous: str) -> None:
    assert run_script.previous_ref(ref, "bot") == previous


class FakeDocker:
    """Stands in for Docker in the update tests.

    Docker deletes an image when its last name goes, so ids only holds names that exist.

    Attributes:
        ids: The image ID that each image name points to on this machine.
        pulled: The IDs that a pull gives the image names.
        starts: Whether each start succeeds, in order.
        commands: The quiet docker commands, such as tag and image rm.
        running: The image ID that each service's container runs.
    """

    def __init__(self, pulled: dict[str, str], starts: list[bool]) -> None:
        self.ids = dict(OLD_IMAGES)
        self.pulled = pulled
        self.starts = starts
        self.commands: list[tuple[str, ...]] = []
        self.running: dict[str, str] = {}

    def pull(self, *_args: object, **_kwargs: object) -> bool:
        self.ids.update(self.pulled)
        return True

    def quietly(self, *arguments: str) -> bool:
        self.commands.append(arguments)
        if arguments[0] == "tag":
            source, target = arguments[1], arguments[2]
            self.ids[target] = self.ids.get(source, source)
        elif arguments[:2] == ("image", "rm"):
            self.ids.pop(arguments[2], None)
        return True

    def launch(self, *_args: object) -> bool:
        return self.starts.pop(0)


@pytest.fixture
def released_bot(tmp_path: Path) -> Path:
    """A downloaded bot release with the release files of the running version."""
    bot = make_bot(tmp_path, "DISCORD_TOKEN=a.b.c\nAPI_MEDIA_SECRET=generated\n")
    write_release(bot)
    return bot


def fake_docker(
    monkeypatch: pytest.MonkeyPatch, pulled: dict[str, str], starts: list[bool]
) -> FakeDocker:
    """Replaces every Docker call that the update makes."""
    docker = FakeDocker(pulled, starts)
    images = [
        bootstrap.ServiceImage("bot", BOT_IMAGE, own=True),
        bootstrap.ServiceImage("media", MEDIA_IMAGE, own=False),
    ]
    monkeypatch.setattr(run_script, "check_docker", lambda _report: True)
    monkeypatch.setattr(run_script, "service_images", lambda *_args: images)
    monkeypatch.setattr(run_script, "image_id", docker.ids.get)
    monkeypatch.setattr(run_script, "running_images", lambda _project: docker.running)
    monkeypatch.setattr(run_script, "pull_images", docker.pull)
    monkeypatch.setattr(run_script, "docker_quietly", docker.quietly)
    monkeypatch.setattr(run_script, "launch", docker.launch)
    monkeypatch.setattr(run_script, "read_release_files", lambda _image: NEW_RELEASE)
    monkeypatch.setattr(run_script, "version_of", lambda _target: "")
    monkeypatch.setattr(run_script, "show_status", lambda _project: None)
    monkeypatch.setattr(
        run_script,
        "backup_database",
        lambda _project, _report: docker.commands.append(("backup",)),
    )
    return docker


def test_update_without_a_new_release_only_makes_sure_it_runs(
    released_bot: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    docker = fake_docker(monkeypatch, pulled={}, starts=[True])
    report = run_script.Report()

    assert run_script.update(run_script.detect_project(released_bot), report)
    assert docker.starts == []
    assert docker.ids == OLD_IMAGES
    assert (released_bot / "run.bat").read_text(encoding="utf-8") == "old run.bat"


def test_update_installs_the_new_release(
    released_bot: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    docker = fake_docker(
        monkeypatch, pulled={BOT_IMAGE: "sha256:bot-new"}, starts=[True]
    )
    report = run_script.Report()

    assert run_script.update(run_script.detect_project(released_bot), report)
    assert (released_bot / "run.bat").read_text(encoding="utf-8") == "new run.bat"
    # The previous versions lose their extra names, which lets Docker delete them.
    assert docker.ids == {BOT_IMAGE: "sha256:bot-new", MEDIA_IMAGE: "sha256:media-old"}
    assert not (released_bot / ".update" / "previous").exists()
    assert report.problems == []
    # The database is backed up before anything changes.
    assert docker.commands[0] == ("backup",)


def test_update_that_fails_to_start_is_undone(
    released_bot: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    docker = fake_docker(
        monkeypatch,
        pulled={BOT_IMAGE: "sha256:bot-new", MEDIA_IMAGE: "sha256:media-new"},
        starts=[False, True],
    )
    report = run_script.Report()

    assert not run_script.update(run_script.detect_project(released_bot), report)
    assert docker.ids == OLD_IMAGES
    assert (released_bot / "run.bat").read_text(encoding="utf-8") == "old run.bat"
    assert not (released_bot / "scripts" / "added.py").exists()
    assert run_script.UpdateState.load(released_bot).broken == {
        "sha256:bot-new",
        "sha256:media-new",
    }
    assert "undone" in report.problems[-1].what


def test_update_skips_a_release_that_failed_before(
    released_bot: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    docker = fake_docker(
        monkeypatch, pulled={BOT_IMAGE: "sha256:bot-new"}, starts=[True]
    )
    run_script.UpdateState(broken={"sha256:bot-new"}).save(released_bot)
    report = run_script.Report()

    assert run_script.update(run_script.detect_project(released_bot), report)
    assert docker.ids == OLD_IMAGES
    assert (released_bot / "run.bat").read_text(encoding="utf-8") == "old run.bat"
    assert "failed to start before" in capsys.readouterr().out
    assert report.problems == []


def test_update_counts_a_tag_that_another_project_already_moved(
    released_bot: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    docker = fake_docker(monkeypatch, pulled={}, starts=[True])
    # Another bot's stack pulled the shared image, but this project still runs the old one.
    docker.ids[MEDIA_IMAGE] = "sha256:media-new"
    docker.running = {"bot": "sha256:bot-old", "media": "sha256:media-old"}
    report = run_script.Report()

    assert run_script.update(run_script.detect_project(released_bot), report)
    assert (
        "tag",
        "sha256:media-old",
        "ghcr.io/lempki/api-media:previous-discord-bot-x",
    ) in (docker.commands)
    assert report.problems == []


def test_start_downloads_only_the_missing_images(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bot = make_bot(tmp_path, "DISCORD_TOKEN=a.b.c\nAPI_MEDIA_SECRET=generated\n")
    pulls: list[list[str]] = []
    monkeypatch.setattr(run_script, "check_docker", lambda _report: True)
    monkeypatch.setattr(run_script, "missing_images", lambda *_args: ["media"])
    monkeypatch.setattr(
        run_script,
        "pull_images",
        lambda _compose, _report, services: pulls.append(services) or True,
    )
    monkeypatch.setattr(run_script, "launch", lambda *_args: True)
    monkeypatch.setattr(run_script, "show_status", lambda _project: None)

    assert run_script.start(run_script.detect_project(bot), run_script.Report())
    assert pulls == [["media"]]


def test_backup_names_sort_from_oldest_to_newest() -> None:
    older = run_script.backup_name(
        time.strptime("2026-10-09 04:00:05", "%Y-%m-%d %H:%M:%S")
    )
    newer = run_script.backup_name(
        time.strptime("2026-10-10 03:59:59", "%Y-%m-%d %H:%M:%S")
    )

    assert older == "bot-2026-10-09-040005.db"
    assert sorted([newer, older]) == [older, newer]


def make_backups(root: Path, days: int) -> list[Path]:
    """Creates one backup per day, oldest first."""
    folder = root / run_script.BACKUP_FOLDER
    folder.mkdir()
    paths = []
    for day in range(1, days + 1):
        path = folder / f"bot-2026-10-{day:02d}-040000.db"
        path.write_bytes(run_script.SQLITE_HEADER + bytes([day]))
        paths.append(path)
    return paths


def test_only_the_newest_backups_stay(tmp_path: Path) -> None:
    paths = make_backups(tmp_path, days=9)
    (tmp_path / run_script.BACKUP_FOLDER / "notes.txt").write_text("", encoding="utf-8")

    deleted = run_script.prune_backups(tmp_path)

    assert deleted == [paths[1], paths[0]]
    assert run_script.list_backups(tmp_path) == paths[:1:-1]
    assert (tmp_path / run_script.BACKUP_FOLDER / "notes.txt").exists()


class FakeRun:
    """Stands in for subprocess.run and records each command with its input."""

    def __init__(self, stdout: bytes = b"", returncode: int = 0) -> None:
        self.stdout = stdout
        self.returncode = returncode
        self.calls: list[tuple[list[str], bytes | None]] = []

    def __call__(self, command: list[str], **kwargs: object) -> object:
        data = kwargs.get("input")
        self.calls.append((command, data if isinstance(data, bytes) else None))
        return SimpleNamespace(
            returncode=self.returncode, stdout=self.stdout, stderr=b"boom"
        )


def test_backup_writes_the_database_into_the_backups_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bot = make_bot(tmp_path, "")
    database = run_script.SQLITE_HEADER + b"settings"
    fake = FakeRun(stdout=database)
    monkeypatch.setattr(run_script.subprocess, "run", fake)
    report = run_script.Report()

    path = run_script.backup_database(run_script.detect_project(bot), report)

    assert path is not None and path.read_bytes() == database
    assert path.parent == bot / run_script.BACKUP_FOLDER
    command = fake.calls[0][0]
    assert command[:4] == ["docker", "compose", "-f", "compose.stack.yml"]
    assert command[4:8] == ["run", "--rm", "--no-deps", "-T"]
    assert "bot" in command


def test_bot_without_a_database_needs_no_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bot = make_bot(tmp_path, "")
    monkeypatch.setattr(run_script.subprocess, "run", FakeRun(stdout=b""))
    report = run_script.Report()

    assert run_script.backup_database(run_script.detect_project(bot), report) is None
    assert report.problems == []
    assert not (bot / run_script.BACKUP_FOLDER).exists()


def test_failed_backup_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bot = make_bot(tmp_path, "")
    monkeypatch.setattr(run_script.subprocess, "run", FakeRun(returncode=1))
    report = run_script.Report()

    assert run_script.backup_database(run_script.detect_project(bot), report) is None
    assert "could not be backed up" in report.problems[0].what


@pytest.mark.parametrize(
    ("answer", "index"),
    [("", 0), ("1", 0), (" 3 ", 2), ("n", None), ("No", None)],
)
def test_read_backup_choice(answer: str, index: int | None) -> None:
    assert run_script.read_backup_choice(answer, 3) == index


@pytest.mark.parametrize("answer", ["0", "4", "newest", "-1"])
def test_read_backup_choice_rejects_other_answers(answer: str) -> None:
    with pytest.raises(ValueError, match="1 to 3"):
        run_script.read_backup_choice(answer, 3)


def ready_bot_for_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answers: list[str]
) -> Path:
    """A released bot with three backups, Docker stubbed out, and scripted answers."""
    bot = make_bot(tmp_path, "DISCORD_TOKEN=a.b.c\nAPI_MEDIA_SECRET=generated\n")
    make_backups(bot, days=3)
    replies = iter(answers)
    monkeypatch.setattr("builtins.input", lambda _prompt: next(replies))
    monkeypatch.setattr(run_script, "ask", lambda _question: True)
    monkeypatch.setattr(run_script, "check_docker", lambda _report: True)
    monkeypatch.setattr(run_script, "run", lambda _command: True)
    monkeypatch.setattr(run_script, "start", lambda _project, _report: True)
    return bot


def test_restore_puts_the_chosen_backup_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bot = ready_bot_for_restore(tmp_path, monkeypatch, answers=["2"])
    fake = FakeRun(stdout=run_script.SQLITE_HEADER + b"current")
    monkeypatch.setattr(run_script.subprocess, "run", fake)
    report = run_script.Report()

    assert run_script.restore(run_script.detect_project(bot), report)
    # The first run backs up the current database, and the second writes the chosen one.
    assert len(fake.calls) == 2
    assert fake.calls[1][1] == run_script.SQLITE_HEADER + bytes([2])
    assert report.problems == []


def test_restore_stops_when_the_current_database_cannot_be_saved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bot = ready_bot_for_restore(tmp_path, monkeypatch, answers=[""])
    fake = FakeRun(returncode=1)
    monkeypatch.setattr(run_script.subprocess, "run", fake)
    report = run_script.Report()

    assert not run_script.restore(run_script.detect_project(bot), report)
    assert len(fake.calls) == 1


def test_restore_refuses_a_file_that_is_not_a_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bot = ready_bot_for_restore(tmp_path, monkeypatch, answers=[""])
    run_script.list_backups(bot)[0].write_bytes(b"not a database")
    report = run_script.Report()

    assert not run_script.restore(run_script.detect_project(bot), report)
    assert "not a SQLite database" in report.problems[0].what

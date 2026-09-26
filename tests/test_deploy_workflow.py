from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "deploy-vps.yml"
DEPLOY_SCRIPT = ROOT / "scripts" / "deploy_vps.sh"
SYSTEMD_UNIT = ROOT / "deploy" / "systemd" / "turnaround.service"


def test_deploy_workflow_pins_the_tested_sha():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "github.event.workflow_run.head_sha" in workflow
    assert "DEPLOY_SHA" in workflow
    assert 'git reset --hard "$DEPLOY_SHA"' in workflow
    assert "git pull --ff-only origin main" not in workflow


def test_deploy_script_verifies_expected_sha_and_service():
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert "EXPECTED_SHA" in script
    assert 'ACTUAL_SHA="$(git rev-parse HEAD)"' in script
    assert 'UNIT_SOURCE="$APP_DIR/deploy/systemd/turnaround.service"' in script
    assert 'sudo -n install -m 0644 "$UNIT_SOURCE" "$UNIT_TARGET"' in script
    assert "sudo -n systemctl daemon-reload" in script
    assert "turnaround.service não pôde ser carregado" in script


def test_systemd_unit_matches_production_layout():
    unit = SYSTEMD_UNIT.read_text(encoding="utf-8")

    assert "User=ubuntu" in unit
    assert "WorkingDirectory=/home/ubuntu/turnaround-project" in unit
    assert "/home/ubuntu/turnaround-project/.venv/bin/streamlit run Planejamento.py" in unit
    assert "--server.address=127.0.0.1" in unit
    assert "--server.port=8501" in unit

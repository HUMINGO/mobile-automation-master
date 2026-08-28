import importlib.util
from pathlib import Path


EXAMPLE = Path(__file__).parents[1] / "examples" / "yun_phone_test.py"
SPEC = importlib.util.spec_from_file_location("yun_phone_test", str(EXAMPLE))
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class FakeClient:
    def __init__(self, xml):
        self.xml = xml
        self.taps = []

    def dump_ui(self):
        return self.xml

    def tap(self, x, y):
        self.taps.append((x, y))


def test_ssh_command_matches_cloud_phone_forwarding_command():
    assert MODULE.SSH_COMMAND == [
        "ssh",
        "-oStrictHostKeyChecking=accept-new",
        "s@162.128.224.130",
        "-p",
        "1824",
        "-L",
        "61046:localhost:1",
        "-Nf",
    ]


def test_wait_and_click_my_uses_nav_resource_id(tmp_path):
    xml = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
    <hierarchy rotation="0">
      <node text="My" resource-id="com.baitu.qingshu:id/navMe"
            class="android.view.View" enabled="true" clickable="true"
            bounds="[100,200][300,400]" />
    </hierarchy>"""
    client = FakeClient(xml)

    assert MODULE.wait_and_click_my(client, tmp_path, attempts=1) == (200, 300)
    assert client.taps == [(200, 300)]
    assert (tmp_path / "poppo_ui_attempt_1.xml").exists()


def test_cli_defaults_to_cloud_phone_and_reuses_tunnel():
    args = MODULE.build_parser().parse_args([])
    assert args.serial == "localhost:61046"
    assert args.package == "com.baitu.qingshu"
    assert args.attempts == 3
    assert args.retry_delay == 5.0
    assert args.skip_tunnel is False

"""CDK app: composes one stack per concern for the environment given by `-c env=<name>`."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import aws_cdk as cdk  # noqa: E402

from infra.config import cdk_environment, load_config  # noqa: E402
from infra.stacks.buses_stack import BusesStack  # noqa: E402
from infra.stacks.commands_stack import CommandsStack  # noqa: E402
from infra.stacks.translator_stack import TranslatorStack  # noqa: E402

app = cdk.App()
cfg = load_config(app)
env = cdk_environment()

buses = BusesStack(app, cfg.stack_name("buses"), cfg, env=env)
translator = TranslatorStack(
    app,
    cfg.stack_name("translator"),
    cfg,
    env=env,
    ingress_bus=buses.ingress_bus,
    domain_bus=buses.domain_bus,
    alarms=buses.alarms,
)
translator.add_dependency(buses)
commands = CommandsStack(
    app, cfg.stack_name("commands"), cfg, env=env, domain_bus=buses.domain_bus, alarms=buses.alarms
)
commands.add_dependency(buses)

for key, value in cfg.tags.items():
    cdk.Tags.of(app).add(key, value)

app.synth()

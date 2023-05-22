import sys

import click
import ruamel.yaml

from .base import map_to_click_exceptions
from ..consts import known_instances


@click.command()
@map_to_click_exceptions
def instances():
    """List known Dandi Archive instances that the CLI can interact with"""
    yaml = ruamel.yaml.YAML(typ="safe")
    yaml.default_flow_style = False
    yaml.dump({k: v._asdict() for k, v in known_instances.items()}, sys.stdout)

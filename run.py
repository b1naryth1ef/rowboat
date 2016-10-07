#!/usr/bin/env python

import os

from disco.cli import disco_main
from disco.bot import Bot, BotConfig, Plugin

from rowboat.plugins.mod import ModPlugin
from rowboat.plugins.util import UtilPlugin


def get_config(cls):
    path = os.path.join('config', cls.__name__.lower() + '.yaml')
    return Plugin.load_config_from_path(cls.CONFIG_CLS, path, format='yaml')


cfg = BotConfig()
cfg.plugin_config_provider = get_config


bot = Bot(disco_main(), cfg)
bot.add_plugin(ModPlugin)
bot.add_plugin(UtilPlugin)
bot.run_forever()

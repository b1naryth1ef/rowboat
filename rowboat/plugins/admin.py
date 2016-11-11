from rowboat import RowboatPlugin as Plugin
from rowboat.types.plugin import PluginConfig


"""
levels:
    my_role_name: 33
    my_user_name: 300

commands:
    prefix: '!'

    overrides:
        wowsocool:
            disabled: true

        ban:
            rename: fukem

        kick:
            level: 30000
"""


class AdminConfig(PluginConfig):
    pass


class AdminPlugin(Plugin):
    pass

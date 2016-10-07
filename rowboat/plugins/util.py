import json

from disco.bot.plugin import Plugin


class UtilPluginConfig(object):
    admins = []


class UtilPlugin(Plugin):
    CONFIG_CLS = UtilPluginConfig

    def __init__(self, bot, config, *args, **kwargs):
        super(UtilPlugin, self).__init__(bot, config or UtilPluginConfig(), *args, **kwargs)

    @Plugin.command('roles')
    def on_roles_command(self, event):
        buff = []

        for role in sorted(event.guild.roles.values(), key=lambda i: i.position):
            buff.append('{} - {} - {}'.format(role.id, role.name, role.permissions.value))

        event.msg.reply('```\n{}\n```'.format('\n'.join(buff)))

    @Plugin.command('role', '<role:Role>')
    def on_role_command(self, event, role):
        if len(event.msg.mention_roles) == 1:
            role = event.msg.mention_roles[0]
        else:
            role = role

        role = event.guild.roles.get(role)
        if not role:
            return event.msg.reply('Unknown role `{}`'.format(role))

        event.msg.reply('```json\n{}\n```'.format(
            json.dumps(role.permissions.to_dict(), sort_keys=True, indent=2, separators=(',', ': '))))

from disco.util.sanitize import S

from datetime import datetime, timedelta
from playhouse.shortcuts import cast
from holster.emitter import Priority

from rowboat.plugins import RowboatPlugin as Plugin, CommandFail, CommandSuccess
from rowboat.types.plugin import PluginConfig
from rowboat.models.message import Highlight
from rowboat.models.user import User
from rowboat.redis import rdb


USER_DM_ID_KEY = 'udi:{}'


class NotificationsConfig(PluginConfig):
    pass


@Plugin.with_config(NotificationsConfig)
class NotificationsPlugin(Plugin):
    global_plugin = True

    MAX_HIGHLIGHTS = 100

    @Plugin.listen('MessageDelete', priority=Priority.AFTER)
    def on_message_delete(self, event):
        if not event.rowboat_message.guild_id:
            return

        users = list(User.select(User.user_id).where(
            (User.user_id << event.rowboat_message.mentions) &
            (cast(User.settings['notifications']['shadow'], 'bool') == True)  # noqa: E712
        ))

        if not users:
            return

        channel = self.state.channels.get(event.rowboat_message.channel_id)

        for user in users:
            perms = channel.get_permissions(user)
            if not perms.read_messages:
                continue

            if event.rowboat_message.timestamp < (datetime.utcnow() - timedelta(days=30)):
                continue

            if user.user_id == event.rowboat_message.author_id:
                continue

            # TODO: dont notify if we can guarentee you saw the mention?

            content = event.rowboat_message.content
            if len(content) > 1024:
                content = content[:1024] + '...'

            dm_id = rdb.get(USER_DM_ID_KEY.format(user.user_id))
            if not dm_id:
                dm_id = self.client.api.users_me_dms_create(user.user_id).id
                rdb.set(USER_DM_ID_KEY.format(user.user_id), str(dm_id))

            self.client.api.channels_messages_create(dm_id,
                u'**[ShadowMention]** {} deleted a mention in {}: {}'.format(
                    event.rowboat_message.author,
                    channel.mention,
                    content,
                ))

    @Plugin.listen('MessageDeleteBulk')
    def on_message_delete_bulk(self, event):
        pass

    def _on_message_delete(self, message):
        # 1. Execute query which:
        #   Selects all users within the mentions array for the message, that also
        #   have shadow mentions config setting enabled.
        # 2. Determine whether the users can actually see the message
        # 3. Dispatch DMs
        pass

    @Plugin.command('add', '<word:str>', group='highlight', global_=True)
    def cmd_highlight_add(self, event, word):
        try:
            count = Highlight.select().where(
                (Highlight.user_id == event.author.id)
            ).count()

            if count > self.MAX_HIGHLIGHTS:
                raise CommandFail('you have too many highlights, please remove some')

            Highlight.create(word=word, user_id=event.author.id)
            raise CommandSuccess('I will now notify you when I see the word {}'.format(
                S(word)
            ))
        except:
            raise CommandFail('you are already tracking that word')

    @Plugin.command('remove', '<word:str>', group='highlight', aliases=['delete'], global_=True)
    def cmd_highlight_remove(self, event, word):
        count = Highlight.delete().where(
            (Highlight.word == word) &
            (Highlight.user_id == event.author.id)
        ).execute()

        if count:
            raise CommandSuccess('you will no longer get notifications for that word')
        raise CommandFail('you where not tracking that word')

    @Plugin.command('list', group='highlight', global_=True)
    def cmd_highlight_list(self, event):
        pass

    @Plugin.command('shadow', '<toggle:str>', global_=True, aliases=['shadowmention'])
    def cmd_shadow(self, event, toggle):
        toggle = {'on': True, 'off': False, 'yes': True, 'no': False}.get(
            toggle.lower()
        )
        if toggle is None:
            raise CommandFail('please pass "on" or "off"')

        user = User.with_id(event.author.id)
        user.settings = user.settings or {}
        user.settings.setdefault('notifications', {})
        user.settings['notifications']['shadow'] = toggle
        user.save()
        raise CommandSuccess(
            'ok, now tracking shadow mentions'
            if toggle else
            'ok, no longer tracking shadow mentions'
        )

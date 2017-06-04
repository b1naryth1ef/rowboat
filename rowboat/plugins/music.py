from disco.bot import CommandLevels

from rowboat.plugins import RowboatPlugin as Plugin
from rowboat.types.plugin import PluginConfig
from rowboat.models.guild import Guild
from rowboat.redis import rdb
# from rowboat.types import ChannelField, Field, SlottedModel, ListField, DictField


class MusicConfig(PluginConfig):
    pass


@Plugin.with_config(MusicConfig)
class MusicPlugin(Plugin):
    WHITELIST_FLAG = Guild.WhitelistFlags.MUSIC

    @Plugin.listen('VoiceStateUpdate')
    def on_voice_state_update(self, event):
        rdb.publish('voice.voice_state_update', event.to_dict())

    @Plugin.command('join', group='music', level=CommandLevels.TRUSTED)
    def on_music_join(self, event):
        event.msg.reply('would join voice')

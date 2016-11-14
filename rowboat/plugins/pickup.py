from peewee import (
    BigIntegerField, IntegerField, CharField, DateTimeField
)
from disco.bot.command import CommandLevels, CommandError
from holster.enum import Enum
from playhouse.postgres_ext import BinaryJSONField
from datetime import datetime

from rowboat import RowboatPlugin as Plugin
from rowboat.util import C
from rowboat.sql import BaseModel
from rowboat.types import SlottedModel, Field, ListField, DictField
from rowboat.types.plugin import PluginConfig
from rowboat.plugins.profile import Platforms


PickupState = Enum(
    'waiting',
    'setup',
    'finished'
)

TeamSelection = Enum(
    'random',
    'ranked',
    'captains',
)

MapSelection = Enum(
    'random',
)


@BaseModel.register
class PickupGame(BaseModel):
    guild_id = BigIntegerField()
    channel_id = BigIntegerField()
    template = CharField()

    state = IntegerField(default=PickupState.WAITING.index)
    teams = BinaryJSONField(default={})
    results = BinaryJSONField(default={})

    created_at = DateTimeField(default=datetime.utcnow)

    @property
    def all_players(self):
        return sum(self.teams.values(), list())

    def on_player_update(self):
        pass

    def add_player(self, uid, team=None):
        team = team or ''

        if team not in self.teams:
            self.teams[team] = []

        self.teams[team].append(uid)
        self.save()
        self.on_player_update()

    def rmv_player(self, uid):
        for team in self.teams:
            if uid in self.teams[team]:
                self.teams[team].remove(uid)
                self.save()
                self.on_player_update()
                return


class TemplateConfig(SlottedModel):
    players = Field(int, default=5)
    teams = ListField(str, default=['Team A', 'Team B'])

    team_selection = Field(TeamSelection, default=TeamSelection.RANDOM)
    map_selection = Field(MapSelection, default=MapSelection.RANDOM)
    platform = Field(Platforms)
    maps = ListField(str, default=[])

    created_fmt = Field(str, default='{user} has created a new pickup game')
    joined_fmt = Field(str, default='{user} has joined the pickup game')
    left_fmt = Field(str, default='{user} has left the pickup game')


class PickupConfig(PluginConfig):
    templates = DictField(str, TemplateConfig)


class PickupPlugin(Plugin):
    whitelisted = True

    @Plugin.command('create', '<template:str>', level=CommandLevels.MOD, group='pug')
    def create(self, event, template):
        try:
            PickupGame.select().where(
                (PickupGame.guild_id == event.guild.id) &
                (PickupGame.channel_id == event.channel.id) &
                (PickupGame.state < PickupState.FINISHED.index)
            ).get()
            return event.msg.reply(':warning: a pickup game is already running in this channel')
        except PickupGame.DoesNotExist:
            pass

        if template not in event.config.templates:
            return event.msg.reply(':warning: unknown pickup game template `{}`'.format(C(template)))

        PickupGame.create(
            guild_id=event.guild.id,
            channel_id=event.channel.id,
            template=template,
        )

        event.msg.reply(event.config.templates[template].created_fmt.format(user=event.author))

    @Plugin.command('delete', level=CommandLevels.MOD, group='pug')
    def delete(self, event):
        try:
            game = PickupGame.select().where(
                (PickupGame.guild_id == event.guild.id) &
                (PickupGame.channel_id == event.channel.id) &
                (PickupGame.state < PickupState.FINISHED.index)
            ).get()
            game.delete_instance()
            event.msg.reply(':ok_hand: deleted the currently running pickup game')
        except PickupGame.DoesNotExist:
            event.msg.reply(':warning: no pickup game running in this channel')

    def get_game(self, event, state=PickupState.WAITING):
        try:
            return PickupGame.select().where(
                (PickupGame.guild_id == event.guild.id) &
                (PickupGame.channel_id == event.channel.id) &
                (PickupGame.state == state.index)
            ).get()
        except PickupGame.DoesNotExist:
            raise CommandError('No pickup game currently running here')

    @Plugin.command('join', aliases=['add'])
    def join(self, event):
        game = self.get_game(event)

        if event.author.id in game.all_players:
            return event.msg.reply(':warning: you are already joined to this pickup game')

        game.add_player(event.author.id)
        event.msg.reply(':ok_hand: you have joined the current pickup game')

    @Plugin.command('leave', aliases=['remove'])
    def leave(self, event):
        game = self.get_game(event)

        if event.author.id not in game.all_players:
            return event.msg.reply(':warning: you are not joined to this pickup game')

        game.rmv_player(event.author.id)
        event.msg.reply(':ok_hand: you have left the current pickup game')

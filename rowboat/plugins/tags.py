
from disco.bot import CommandLevels
from disco.util.sanitize import S
from disco.types.message import MessageEmbed

from rowboat.plugins import RowboatPlugin as Plugin, CommandFail, CommandSuccess
from rowboat.types import Field
from rowboat.types.plugin import PluginConfig
from rowboat.models.tags import Tag
from rowboat.models.user import User


class TagsConfig(PluginConfig):
    max_tag_length = Field(int)
    min_level_remove_others = Field(int, default=int(CommandLevels.MOD))


@Plugin.with_config(TagsConfig)
class TagsPlugin(Plugin):
    @Plugin.command('create', '<name:str> <content:str...>', group='tags', aliases=['add'], level=CommandLevels.TRUSTED)
    def on_tags_create(self, event, name, content):
        name = S(name)
        content = S(content)

        if len(content) > event.config.max_tag_length:
            raise CommandFail('tag content is too long (max {} characters)'.format(event.config.max_tag_length))

        _, created = Tag.get_or_create(
            guild_id=event.guild.id,
            author_id=event.author.id,
            name=name,
            content=content
        )

        if not created:
            raise CommandFail('a tag by that name already exists')

        raise CommandSuccess(u'ok, your tag named `{}` has been created'.format(name))

    @Plugin.command('tags', '<name:str>', aliases=['tag'], level=CommandLevels.TRUSTED)
    @Plugin.command('show', '<name:str>', group='tags', level=CommandLevels.TRUSTED)
    def on_tags(self, event, name):
        try:
            tag = Tag.select(Tag, User).join(
                User, on=(User.user_id == Tag.author_id)
            ).where(
                (Tag.guild_id == event.guild.id) &
                (Tag.name == S(name))
            ).get()
        except Tag.DoesNotExist:
            raise CommandFail('no tag by that name exists')

        # Track the usage of the tag
        Tag.update(times_used=Tag.times_used + 1).where(
            (Tag.guild_id == tag.guild_id) &
            (Tag.name == tag.name)
        ).execute()

        event.msg.reply(u':information_source: {}'.format(
            tag.content
        ))

    @Plugin.command('remove', '<name:str>', group='tags', aliases=['del', 'rm'], level=CommandLevels.TRUSTED)
    def on_tags_remove(self, event, name):
        try:
            tag = Tag.select(Tag, User).join(
                User, on=(User.user_id == Tag.author_id)
            ).where(
                (Tag.guild_id == event.guild.id) &
                (Tag.name == S(name))
            ).get()
        except Tag.DoesNotExist:
            raise CommandFail('no tag by that name exists')

        if tag.author_id != event.author.id:
            if event.user_level <= event.config.min_level_remove_others:
                raise CommandFail('you do not have the required permissions to remove other users tags')

        tag.delete_instance()
        raise CommandSuccess(u'ok, deleted tag `{}`'.format(tag.name))

    @Plugin.command('info', '<name:str>', group='tags', level=CommandLevels.TRUSTED)
    def on_tags_info(self, event, name):
        try:
            tag = Tag.select(Tag, User).join(
                User, on=(User.user_id == Tag.author_id).alias('author')
            ).where(
                (Tag.guild_id == event.guild.id) &
                (Tag.name == S(name))
            ).get()
        except Tag.DoesNotExist:
            raise CommandFail('no tag by that name exists')

        embed = MessageEmbed()
        embed.title = tag.name
        embed.description = tag.content
        embed.add_field(name='Author', value=unicode(tag.author), inline=True)
        embed.add_field(name='Times Used', value=str(tag.times_used), inline=True)
        embed.timestamp = tag.created_at.isoformat()
        event.msg.reply(embed=embed)

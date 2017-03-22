import peewee

from disco.bot import CommandLevels
from disco.types.message import MessageEmbed

from rowboat.plugins import RowboatPlugin as Plugin
from rowboat.types.plugin import PluginConfig
from rowboat.types import ChannelField, Field, SlottedModel, ListField, DictField
from rowboat.models.message import StarboardEntry, Message
from rowboat.util.timing import Debounce


STAR_EMOJI = u'\U00002B50'


def is_star_event(e):
    if e.emoji.name == STAR_EMOJI:
        return True


class ChannelConfig(SlottedModel):
    sources = ListField(ChannelField, default=[])

    # Delete the star when the message is deleted
    clear_on_delete = Field(bool, default=True)

    # Min number of stars to post on the board
    min_stars = Field(int, default=1)
    min_stars_pin = Field(int, default=15)

    # The number which represents the "max" star level
    star_color_max = Field(int, default=15)

    def get_color(self, count):
        ratio = min(count / float(self.star_color_max), 1.0)

        return (
            (255 << 16) +
            (int((194 * ratio) + (253 * (1 - ratio))) << 8) +
            int((12 * ratio) + (247 * (1 - ratio))))


class StarboardConfig(PluginConfig):
    channels = DictField(ChannelField, ChannelConfig)

    # TODO: validate that each source channel has only one starboard mapping

    def get_board(self, channel_id):
        # Starboards can't work recursively
        if channel_id in self.channels:
            return (None, None)

        for starboard, config in self.channels.items():
            if not config.sources or channel_id in config.sources:
                return (starboard, config)
        return (None, None)


@Plugin.with_config(StarboardConfig)
class StarboardPlugin(Plugin):
    def load(self, ctx):
        super(StarboardPlugin, self).load(ctx)
        self.updates = {}
        self.locks = {}

    @Plugin.command('update', group='stars', level=CommandLevels.ADMIN)
    def force_update_stars(self, event):
        # First, iterate over stars and repull their reaction count
        stars = StarboardEntry.select().join(Message).where(
            (Message.guild_id == event.guild.id) &
            (~ (StarboardEntry.star_message_id >> None))
        ).order_by(Message.timestamp.desc()).limit(100)

        for star in stars:
            self.log.info('Attempting to update stars for %s', star.message_id)
            msg = self.client.api.channels_messages_get(
                star.message.channel_id,
                star.message_id)

            users = [i.id for i in msg.get_reactors(STAR_EMOJI)]

            if set(users) != set(star.stars):
                self.log.warning('star %s had outdated reactors list %s vs %s',
                    star.message_id,
                    users,
                    star.stars)

                StarboardEntry.update(
                    stars=users,
                    dirty=True,
                ).where(
                    (StarboardEntry.message_id == star.message_id)
                ).execute()

        self.queue_update(event.guild.id, event.config)

    @Plugin.command('lock', group='stars', level=CommandLevels.ADMIN)
    def lock_stars(self, event):
        if event.guild.id in self.locks:
            event.msg.reply(':warning: starboard is already locked')
            return

        self.locks[event.guild.id] = True
        event.msg.reply(':white_check_mark: starboard has been locked')

    @Plugin.command('unlock', group='stars', level=CommandLevels.ADMIN)
    def unlock_stars(self, event):
        if event.guild.id in self.locks:
            del self.locks[event.guild.id]
            event.msg.reply(':white_check_mark: starboard has been unlocked')
            return
        event.msg.reply(':warning: starboard is not locked')

    def queue_update(self, guild_id, config):
        if guild_id in self.locks:
            return

        if guild_id not in self.updates or not self.updates[guild_id].active():
            if guild_id in self.updates:
                del self.updates[guild_id]
            self.updates[guild_id] = Debounce(self.update_starboard, 2, 6, guild_id=guild_id, config=config.get())
        else:
            self.updates[guild_id].touch()

    def update_starboard(self, guild_id, config):
        self.log.info('Attempting to update starboard %s / %s', guild_id, config)

        # Grab all dirty stars
        stars = StarboardEntry.select().where(
            (StarboardEntry.dirty == 1)
        )

        for star in stars:
            sb_id, sb_config = config.get_board(star.message.channel_id)

            if not sb_id:
                StarboardEntry.update(dirty=False).where(StarboardEntry.message_id == star.message_id).execute()
                continue

            # If this star has no stars, delete it from the starboard
            if not star.stars:
                if not star.star_channel_id:
                    StarboardEntry.update(dirty=False).where(StarboardEntry.message_id == star.message_id).execute()
                    continue

                self.delete_star(star)
                continue

            # Grab the original message
            try:
                source_msg = self.client.api.channels_messages_get(
                    star.message.channel_id,
                    star.message_id)
            except:
                self.log.exception('Star message went missing %s / %s: ', star.message.channel_id, star.message_id)
                # TODO: really delete this
                self.delete_star(star, update=True)
                continue

            # If we previously posted this in the wrong starboard, delete it
            if star.star_channel_id and (star.star_channel_id != sb_id or len(star.stars) < sb_config.min_stars):
                self.delete_star(star, update=True)

            if len(star.stars) < sb_config.min_stars:
                StarboardEntry.update(dirty=False).where(StarboardEntry.message_id == star.message_id).execute()
                continue

            self.post_star(star, source_msg, sb_id, sb_config)

    def delete_star(self, star, update=True):
        self.log.info('Removing starboard entry %s', star)
        try:
            self.client.api.channels_messages_delete(
                star.star_channel_id,
                star.star_message_id,
            )
        except:
            pass

        if update:
            StarboardEntry.update(
                dirty=False,
                star_channel_id=None,
                star_message_id=None,
            ).where(
                (StarboardEntry.message_id == star.message_id)
            ).execute()

            # Update this for post_star
            star.star_channel_id = None
            star.star_message_id = None

    def post_star(self, star, source_msg, starboard_id, config):
        self.log.info('Posting starboard entry for %s', star)

        # Generate the embed and post it
        content, embed = self.get_embed(star, source_msg, config)

        if not star.star_message_id:
            try:
                msg = self.client.api.channels_messages_create(
                        starboard_id,
                        content,
                        embed=embed)
            except:
                self.log.exception('Failed to post starboard message: ')
                return
        else:
            msg = self.client.api.channels_messages_modify(
                star.star_channel_id,
                star.star_message_id,
                content,
                embed=embed)

        # Update our starboard entry
        StarboardEntry.update(
            dirty=False,
            star_channel_id=msg.channel_id,
            star_message_id=msg.id,
        ).where(
            (StarboardEntry.message_id == star.message_id)
        ).execute()

    @Plugin.listen('MessageReactionAdd', conditional=is_star_event)
    def on_message_reaction_add(self, event):
        try:
            StarboardEntry.add_star(event.message_id, event.user_id)
        except peewee.IntegrityError:
            msg = self.client.api.channels_messages_get(
                event.channel_id,
                event.message_id)

            if msg:
                Message.from_disco_message(msg)
                StarboardEntry.add_star(event.message_id, event.user_id)
            else:
                return

        self.queue_update(event.guild.id, event.config)

    @Plugin.listen('MessageReactionRemove', conditional=is_star_event)
    def on_message_reaction_remove(self, event):
        StarboardEntry.remove_star(event.message_id, event.user_id)
        self.queue_update(event.guild.id, event.config)

    @Plugin.listen('MessageReactionRemoveAll')
    def on_message_reaction_remove_all(self, event):
        StarboardEntry.update(
            stars=[],
            dirty=True
        ).where(
            (StarboardEntry.message_id == event.message_id)
        ).execute()
        self.queue_update(event.guild.id, event.config)

    @Plugin.listen('MessageUpdate')
    def on_message_update(self, event):
        sb_id, sb_config = event.config.get_board(event.channel_id)
        if not sb_id:
            return

        count = StarboardEntry.update(
            dirty=True
        ).where(
            (StarboardEntry.message_id == event.message.id)
        ).execute()

        if count:
            self.queue_update(event.guild.id, event.config)

    @Plugin.listen('MessageDelete')
    def on_message_delete(self, event):
        sb_id, sb_config = event.config.get_board(event.channel_id)
        if not sb_id:
            return

        if sb_config.clear_on_delete:
            stars = list(StarboardEntry.delete().where(
                (StarboardEntry.message_id == event.id)
            ).returning(StarboardEntry).execute())

            self.log.info('Clearing stars for (%s) on deletion: %s', event.id, stars)

            for star in stars:
                self.delete_star(star, update=False)

    def get_embed(self, star, msg, config):
        # Create the 'header' (non-embed) text
        stars = ':star:'

        if len(star.stars) > 1:
            if len(star.stars) >= config.star_color_max:
                stars = ':star2:'
            stars = stars + ' {}'.format(len(star.stars))

        content = '{} <#{}> ({})'.format(
            stars,
            msg.channel_id,
            msg.id
        )

        # Generate embed section
        embed = MessageEmbed()
        embed.description = msg.content

        if msg.attachments:
            attach = list(msg.attachments.values())[0]
            if attach.url.lower().endswith(('png', 'jpeg', 'jpg', 'gif', 'webp')):
                embed.set_image(url=attach.url)

        if msg.embeds:
            if msg.embeds[0].image.url:
                embed.set_image(url=msg.embeds[0].image.url)
            elif msg.embeds[0].thumbnail.url:
                embed.set_image(url=msg.embeds[0].thumbnail.url)

        author = msg.guild.get_member(msg.author)
        if author:
            embed.set_author(
                name=author.name,
                icon_url=author.user.avatar_url
            )
        else:
            embed.set_author(
                name=msg.author.username,
                icon_url=msg.author.avatar_url)

        embed.timestamp = msg.timestamp.isoformat()
        embed.color = config.get_color(len(star.stars))

        return content, embed


# prune
# Pin top stars
# modlog

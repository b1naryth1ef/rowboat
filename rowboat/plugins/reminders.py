import humanize
import gevent

from datetime import datetime, timedelta

from disco.util.sanitize import S

from rowboat.plugins import RowboatPlugin as Plugin
from rowboat.util.timing import Eventual
from rowboat.util.input import parse_duration
from rowboat.types.plugin import PluginConfig
from rowboat.models.message import Reminder
from rowboat.constants import (
    SNOOZE_EMOJI, GREEN_TICK_EMOJI, GREEN_TICK_EMOJI_ID, YEAR_IN_SEC
)


class RemindersConfig(PluginConfig):
    pass


@Plugin.with_config(RemindersConfig)
class RemindersPlugin(Plugin):
    def load(self, ctx):
        super(RemindersPlugin, self).load(ctx)
        self.reminder_task = Eventual(self.trigger_reminders)
        self.spawn_later(10, self.queue_reminders)

    def queue_reminders(self):
        try:
            next_reminder = Reminder.select().order_by(
                Reminder.remind_at.asc()
            ).limit(1).get()
        except Reminder.DoesNotExist:
            return

        self.reminder_task.set_next_schedule(next_reminder.remind_at)

    def trigger_reminders(self):
        reminders = Reminder.with_message_join().where(
            (Reminder.remind_at < (datetime.utcnow() + timedelta(seconds=1)))
        )

        waitables = []
        for reminder in reminders:
            waitables.append(self.spawn(self.trigger_reminder, reminder))

        for waitable in waitables:
            waitable.join()

        self.queue_reminders()

    def trigger_reminder(self, reminder):
        message = reminder.message_id
        channel = self.state.channels.get(message.channel_id)
        if not channel:
            self.log.warning('Not triggering reminder, channel %s was not found!',
                message.channel_id)
            reminder.delete_instance()
            return

        msg = channel.send_message(u'<@{}> you asked me at {} ({} ago) to remind you about: {}'.format(
            message.author_id,
            reminder.created_at,
            humanize.naturaldelta(reminder.created_at - datetime.utcnow()),
            S(reminder.content)
        ))

        # Add the emoji options
        msg.add_reaction(SNOOZE_EMOJI)
        msg.add_reaction(GREEN_TICK_EMOJI)

        try:
            mra_event = self.wait_for_event(
                'MessageReactionAdd',
                message_id=msg.id,
                conditional=lambda e: (
                    (e.emoji.name == SNOOZE_EMOJI or e.emoji.id == GREEN_TICK_EMOJI_ID) and
                    e.user_id == message.author_id
                )
            ).get(timeout=30)
        except gevent.Timeout:
            reminder.delete_instance()
            return
        finally:
            # Cleanup
            msg.delete_reaction(SNOOZE_EMOJI)
            msg.delete_reaction(GREEN_TICK_EMOJI)

        if mra_event.emoji.name == SNOOZE_EMOJI:
            reminder.remind_at = datetime.utcnow() + timedelta(minutes=20)
            reminder.save()
            msg.edit(u'Ok, I\'ve snoozed that reminder for 20 minutes.')
            return

        reminder.delete_instance()

    @Plugin.command('clear', group='r', global_=True)
    def cmd_remind_clear(self, event):
        count = Reminder.delete_for_user(event.author.id)
        return event.msg.reply(':ok_hand: I cleared {} reminders for you'.format(count))

    @Plugin.command('add', '<duration:str> <content:str...>', group='r', global_=True)
    @Plugin.command('remind', '<duration:str> <content:str...>', global_=True)
    def cmd_remind(self, event, duration, content):
        if Reminder.count_for_user(event.author.id) > 30:
            return event.msg.reply(':warning: you an only have 15 reminders going at once!')

        remind_at = parse_duration(duration)
        if remind_at > (datetime.utcnow() + timedelta(seconds=5 * YEAR_IN_SEC)):
            return event.msg.reply(':warning: thats too far in the future, I\'ll forget!')

        r = Reminder.create(
            message_id=event.msg.id,
            remind_at=remind_at,
            content=content
        )
        self.reminder_task.set_next_schedule(r.remind_at)
        event.msg.reply(':ok_hand: I\'ll remind you at {} ({})'.format(
            r.remind_at.isoformat(),
            humanize.naturaldelta(r.remind_at - datetime.utcnow()),
        ))

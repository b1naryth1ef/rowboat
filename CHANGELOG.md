# Changelog

## V1.2.0

### Features

- Twitch plugin added, can be used to track and notify a server of streams going online. (Currently early beta)

## V1.1.1

- Removed some utilities commands that didn't fit rowboats goal
- Etc SQL changes

## V1.1.0

### Features

- **MAJOR** Added support for audit-log reasons withing various admin actions. This will log the reason you provide for kicks/bans/mutes/etc within the Discord audit-log.
- **MAJOR** !mute behavior has changed. If a valid duration string is the first part of the reason, a !mute command is transformed into a tempmute. This should help resolve a common mistake people make.
- !join and !leave will no longer respond if no group roles are specified within the admin config
- Added a SQL command for global admins to graph word usage in a server.

### Bugfixes

- Fixed reloading of SQLPlugin in development
- Fixed some user images causing `get_dominant_colors` to return an incorrect value causing a command exception
- Fixed error case in modlog when handling VoiceStateUpdate
- Fixed a case where a user could not save the webconfig because the web access object had their ID stored as a string
- Fixed censor throwing errors when a message which was censored was already deleted

## V1.0.5

Similar changes to v1.0.4

## V1.0.4

### Bugfixes

- Fixed invalid function call causing errors w/ CHANGE\_USERNAME event

## V1.0.3

### Features

- Added two new modlog events, `MEMBER_TEMPMUTE_EXPIRE` and `MEMBER_TEMPBAN_EXPIRE` which are triggered when their respective infractions expire

### Bugfixes

- Fixed cases where certain modlog channels could become stuck due to transient Discord issues
- Fixed cases where content in certain censor filters would be ignored due to its casing, censor now ignores all casing in filters within its config

### Etc

- Don't leave the ROWBOAT\_GUILD\_ID, its special (and not doing this makes it impossible to bootstrap the bot otherwise)
- Improved the performance of !stats

## V1.0.2

### Bugfixes

- Fixed the user in a ban/forceban's modlog message being `<UNKNOWN>`. The modlog entry will now contain their ID if Rowboat cannot resolve further user information
- Fixed the duration of unlocking a role being 6 minutes instead of 5 minutes like the response message said
- Fixed some misc errors thrown when passing webhook messages to censor/spam plugins
- Fixed case where Rowboat guild access was not being properly synced due to invalid data being passed in the web configuration for some guilds
- Fixed the documentation URL being outdated
- Fixed some commands being incorrectly exposed publically
- Fixed the ability to revoke or change ones own roles within the configuration

### Etc

- Removed ignored\_channels, this concept is no longer (and hasn't been for a long time) used.
- Improved the performance (and formatting) around the !info command

## V1.0.1

### Bugfixes

- Fixed admin add/rmv role being able to operate on role that matched the command executors highest role.
- Fixed error triggered when removing debounces that where already partially-removed
- Fixed add/remove role throwing a command error when attempting to execute the modlog portion of their code.
- Fixed case where User.tempmute was called externally (e.g. by spam) for a guild without a mute role setup

## V1.0.0

### **BREAKING** Group Permissions Protection

This update includes a change to the way admin-groups (aka joinable roles) work. When a user attempts to join a group now, rowboat will check and confirm the role does not give any unwanted permissions (e.g. _anything_ elevated). This check can not be skipped or disabled in the configuration. Groups are explicitly meant to give cosmetic or channel-based permissions to users, and should _never_ include elevated permissions. In the case that a group role somehow is created or gets permissions, this prevents any users from using Rowboat as an elevation attack. Combined with guild role locking, this should prevent almost all possible permission escalation attacks.

### Guild Role Locking

This new feature allows Rowboat to lock-down a role, completely preventing/reverting updates to it. Roles can be unlocked by an administrator using the `!role unlock <role_id>` command, or by removing them from the config. The intention of this feature is to help locking down servers from permission escalation attacks. Role locking should be enabled for all roles that do not and should not change regularly, and for added protection you can disable the unlock command within your config.

```yaml
plugins:
  admin:
    locked_roles: [ROLE_ID_HERE]
```

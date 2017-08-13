# Changelog

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

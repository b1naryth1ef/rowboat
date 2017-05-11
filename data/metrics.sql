-- Stores information about a guild at a given point in time
CREATE TABLE guild_snapshots (
  time      TIMESTAMPTZ  NOT NULL,
  guild_id  BIGINT       NOT NULL,

  members INTEGER,
  members_online INTEGER,
  members_offline INTEGER,
  members_away INTEGER,
  members_dnd INTEGER,
  members_voice INTEGER,

  emojis SMALLINT,

  PRIMARY KEY (time, guild_id)
);

-- Stores information about messages in a channel
CREATE TABLE channel_messages_snapshot (
  time        TIMESTAMPTZ  NOT NULL,
  channel_id  BIGINT       NOT NULL,

  created INTEGER,
  updated INTEGER,
  deleted INTEGER,
  mentions INTEGER,
  users INTEGER,

  PRIMARY KEY (time, channel_id)
);

# stream_tagger

A discord bot that allows users to live tag live-streams, inspired by Korotagger.

You can use `/settings quiet` to run it alongside another tagger bot, and use `/tags_from_history` to load tags made before the bot joined the server.

## Usage

The bot supports both classic commands and slash commands. In both cases, creating tags is done via backtick (`` ` ``).
For the other classic commands, the prefix is configurable by server mods.

### Tagging
Tag by writing a message, prefixed by `` ` ``.

Example:  
``  `your tag``

You can __edit__ the message to edit the tag.

Press ❌ to delete the tag from bot's database. ⭐ adds a star in the tag output.

### Sub-tags
You can use more than one back tick to create indents in the output. Eg.:
```
``another tag
``one more tag
`` `tagggg
```
renders as:

7m20s | your tag  
├ 8m30s | another tag  
├ 8m38s | one more tag  
└─ 9m39s | tagggg  


#### Adjust

Use `/adjust -10` or `[prefix]adjust -10` to move your tag 10 seconds earlier. Positive values move it later.
Note that by default there is a -20 offset on all tags (adjustable per server).

### Printing tags

Use `/tags <stream>` to dump the tags for a stream. The stream doesn't need any previous setup, the data is fetched and tags are compiled when this command is run.

`stream` can be the
* name a of Vtuber, full or partial (`Selen Tatsuki 【NIJISANJI EN】` or `tatsu`),
* channel url (`https://www.youtube.com/channel/UCV1xUwfM2v2oBtT3JNvic3w`, `https://www.twitch.tv/selentatsuki`, `UCV1xUwfM2v2oBtT3JNvic3w`),
* stream url (`https://www.youtube.com/watch?v=99cq9wz2SOc`, `https://www.twitch.tv/videos/1510546725`, `99cq9wz2SOc`)

If you use the Vtuber name, the bot looks at all the accounts of the streamer across platforms to find the latest stream.

For "member's only" streams and private VODs, you may have to provide the stream url directly.

Currently, Youtube and Twitch are explicitly supported. For unsupported platforms, the bot still does a best effort to extract the information.
If the bot doesn't work for a website that you want (eg. Twitter spaces, Niconico), feel free to request support.

#### Advanced tags

You can use `/tags_advanced` to access more options.

* `stream`: This is optional if you use `start_time` or `server` options.
* `start_time`: Manually provide a start time. Either `13:00:00` in UTC today, or a Unix timestamp. 
* `duration`: Use with `start_time`. Leave empty for until current time, if no stream is provided.
* `format`: How to format the tags. `classic` tries to mimic Korotagger. Default is `alternative`. (Related `/settings default_format`)
* `server`: A server name or id to steal the tags from. When stealing tags, `stream` and `start_time` are not necessary. (Related `/settings set_private`)
* `own`: Only show your tags.
* `delete_last`: Delete the latest tag. Don't use this with other options. (The tags are never deleted from the database.)
* `offset`: Offset the time stamps.
* `min_stars`: Minimum treshold for number of stars.

#### Without slash commands

An example command that uses all the above options (don't forget the prefix):  
`tags selen classic start=13:01:00 duration=2:00:00 server=123456 own offset=30 min_stars=2`  
To delete the last tag dump: `tags delete`

### Loading older tags

You can use `/tags_from_history 2` to load tags from the last 2 days. Useful if you want the bot to load tags made before the bot joined.

## Setup

It is recommended to use the bot with slash commands.
If you don't want the slash commands, you can either disable them, or use an invite link that doesn't include the "app commands" scope.
It is best to not use slash commands and classic commands together.

### With slash commands

First thing to do is to go to Server Settings -> Integrations to adjust which roles can use which commands (`` ` `` is always available to everyone.)

If you have a hidden channel that you want to use the bot in (such as a channel only available to yt-members) as private by `/settings set_private True`.
The tags (`` ` ``) made in this channel will not be able to be "stolen" by other servers.

That is all, there is no `register` or `watch` command. You can view other settings by typing `/settings`.

### Without slash commands

First set a prefix by mentioning the bot: `@bot settings set_prefix !` to set the prefix to `!`. Mentioning the bot is always a valid prefix.

Only "admin"s (or users with "Manage Server" permissions) can use the `settings` and `tags` commands.
To add users or roles to the admin list, use `admin_add`, `admin_rem` and `admin_list` with user and role names or ids.

You can use `help settings` to view help regarding all settings commands, or `help settings command` to view detailed help.

## Hosting the bot

With Python 3.10 minimum, `pip install git+https://github.com/OdielDomanie/stream_tagger.git`.

Create a .env with `HOLODEX_TOKEN` and `DISCORD_TOKEN`.

To run, `python -m stream_tagger`.

---

Most often, the video data is extracted directly. Some functionality is powered by Holodex API.
Holodex API License: https://holodex.stoplight.io/docs/holodex/8166fcec5dbe2-license


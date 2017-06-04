#!/usr/bin/env python
from gevent import monkey; monkey.patch_all()

from werkzeug.serving import run_with_reloader
from gevent import wsgi
from rowboat import ENV
from rowboat.web import rowboat
from rowboat.sql import init_db
from yaml import load

import os
import copy
import click
import signal
import logging
import gevent
import subprocess


class BotSupervisor(object):
    def __init__(self, env={}):
        self.proc = None
        self.env = env
        self.bind_signals()
        self.start()

    def bind_signals(self):
        signal.signal(signal.SIGUSR1, self.handle_sigusr1)

    def handle_sigusr1(self, signum, frame):
        print 'SIGUSR1 - RESTARTING'
        gevent.spawn(self.restart)

    def start(self):
        env = copy.deepcopy(os.environ)
        env.update(self.env)
        self.proc = subprocess.Popen(['python', '-m', 'disco.cli', '--config', 'config.yaml'], env=env)

    def stop(self):
        self.proc.terminate()

    def restart(self):
        try:
            self.stop()
        except:
            pass

        self.start()

    def run_forever(self):
        while True:
            self.proc.wait()
            gevent.sleep(5)


@click.group()
def cli():
    logging.getLogger().setLevel(logging.INFO)


@cli.command()
@click.option('--reloader/--no-reloader', '-r', default=False)
def serve(reloader):
    def run():
        wsgi.WSGIServer(('0.0.0.0', 8686), rowboat.app).serve_forever()

    if reloader:
        run_with_reloader(run)
    else:
        run()


@cli.command()
@click.option('--env', '-e', default='local')
def bot(env):
    with open('config.yaml', 'r') as f:
        config = load(f)

    supervisor = BotSupervisor(env={
        'ENV': env,
        'DSN': config['DSN'],
    })
    supervisor.run_forever()


@cli.command('add-global-admin')
@click.argument('user-id')
def add_global_admin(user_id):
    from rowboat.redis import rdb
    from rowboat.models.user import User
    init_db(ENV)
    rdb.sadd('global_admins', user_id)
    User.update(admin=True).where(User.user_id == user_id).execute()
    print 'Ok, added {} as a global admin'.format(user_id)


@cli.command('wh-add')
@click.argument('guild-id')
@click.argument('flag')
def add_whitelist(guild_id, flag):
    from rowboat.models.guild import Guild
    init_db(ENV)

    flag = Guild.WhitelistFlags.get(flag)
    if not flag:
        print 'Invalid flag'
        return

    try:
        guild = Guild.get(guild_id=guild_id)
    except Guild.DoesNotExist:
        print 'No guild exists with that id'
        return

    guild.whitelist.append(int(flag))
    guild.save()
    guild.emit_update()
    print 'added flag'


@cli.command('wh-rmv')
@click.argument('guild-id')
@click.argument('flag')
def rmv_whitelist(guild_id, flag):
    from rowboat.models.guild import Guild
    init_db(ENV)

    flag = Guild.WhitelistFlags.get(flag)
    if not flag:
        print 'Invalid flag'
        return

    try:
        guild = Guild.get(guild_id=guild_id)
    except Guild.DoesNotExist:
        print 'No guild exists with that id'
        return

    guild.whitelist.remove(int(flag))
    guild.save()
    guild.emit_update()
    print 'removed flag'


if __name__ == '__main__':
    cli()

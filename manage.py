#!/usr/bin/env python
from gevent import monkey; monkey.patch_all()

from werkzeug.serving import run_with_reloader
from gevent import wsgi
from rowboat.web import rowboat, before_first_request

import logging
import click
import BaseHTTPServer
import subprocess


SUPERVISOR = None


class BotSupervisor(object):
    def __init__(self):
        self.proc = None
        self.start()

    def start(self):
        self.proc = subprocess.Popen(['python', '-m', 'disco.cli', '--config', 'config.yaml'])

    def stop(self):
        self.proc.terminate()

    def restart(self):
        try:
            self.stop()
        except:
            pass

        self.start()


class RestarterHandler(BaseHTTPServer.BaseHTTPRequestHandler):
    def do_POST(s):
        s.send_response(200)
        s.end_headers()

        subprocess.check_call(['git', 'pull', 'origin', 'master'])
        SUPERVISOR.restart()


@click.group()
def cli():
    before_first_request()
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
def bot():
    global SUPERVISOR

    SUPERVISOR = BotSupervisor()
    httpd = BaseHTTPServer.HTTPServer(('0.0.0.0', 8080), RestarterHandler)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass

    httpd.server_close()

if __name__ == '__main__':
    cli()
